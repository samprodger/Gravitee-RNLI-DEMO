"""
RNLI Lifeboat Station Finder — A2A Agent

Exposes an Agent-to-Agent (A2A) compliant server that answers questions about
RNLI lifeboat stations.  It uses an LLM (via Gravitee Gateway / Ollama) for
natural-language understanding and calls the lifeboat-api service directly as
its tool back-end.

User context is passed as a JSON prefix in the message:
  [USER_CONTEXT:{"name":"Joe Doe","email":"...","plan":"gold","visits":[...]}]
  <actual user message>
"""

import json
import logging
import os
import re
import uuid
from typing import Any, Optional

import httpx
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Message, Role, TextPart
from openai import OpenAI, RateLimitError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8001"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://gateway:8082/llm-proxy")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "ollama:qwen3:0.6b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

LIFEBOAT_API_BASE = os.getenv("LIFEBOAT_API_BASE", "http://lifeboat-api:8000")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("rnli-a2a-agent")

# ---------------------------------------------------------------------------
# User context extraction
# ---------------------------------------------------------------------------

_USER_CONTEXT_PREFIX = "[USER_CONTEXT:"
_JSON_DECODER = json.JSONDecoder()


def extract_user_context(raw_message: str) -> tuple[Optional[dict], str]:
    """
    Extract the optional [USER_CONTEXT:{...}] prefix from a message.
    Uses the JSON decoder to find the exact end of the JSON object so that
    nested arrays/objects with their own ] characters don't confuse the parser.
    Returns (context_dict_or_None, cleaned_message).
    """
    if not raw_message.startswith(_USER_CONTEXT_PREFIX):
        return None, raw_message
    idx = len(_USER_CONTEXT_PREFIX)
    try:
        ctx, end = _JSON_DECODER.raw_decode(raw_message, idx)
    except json.JSONDecodeError:
        logger.warning("Could not parse USER_CONTEXT JSON")
        return None, raw_message
    # Expect a closing ] immediately after the JSON object
    rest = raw_message[end:]
    if rest.startswith("]"):
        rest = rest[1:]
    # Strip optional newline separator
    if rest.startswith("\n"):
        rest = rest[1:]
    return ctx, rest.strip()


# ---------------------------------------------------------------------------
# Dynamic system prompt builder
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = (
    "You are the RNLI Lifeboat Station Finder, an expert AI assistant for the "
    "Royal National Lifeboat Institution (RNLI). You help people find their nearest "
    "lifeboat stations, understand the types of lifeboats in operation, and learn "
    "about RNLI coverage across the UK and Ireland.\n\n"
    "You have access to a comprehensive database of RNLI stations. Always use your "
    "tools to fetch station data when someone asks about locations or stations.\n\n"
    "Station types:\n"
    "- ALB (All-weather Lifeboat): large offshore lifeboats for all conditions.\n"
    "- ILB (Inshore Lifeboat): smaller, faster boats for rescues close to shore.\n\n"
    "When presenting station results from your tools, for each station include:\n"
    "- Station name and type\n"
    "- Address (copy the exact 'address' value from the tool response)\n"
    "- Walking directions: use the exact 'google_maps_url' value from the tool "
    "response as a markdown link, e.g. [🗺️ Get walking directions](https://...)\n"
    "- Distance in miles if present\n\n"
    "Format station lists clearly. Be helpful, friendly and professional."
)


def build_system_prompt(context: Optional[dict] = None) -> str:
    """Build a personalised system prompt based on user context."""
    prompt = BASE_SYSTEM_PROMPT

    if not context:
        return prompt

    name = context.get("name", "there")
    plan = (context.get("plan") or "standard").lower()
    visits = context.get("visits") or []

    personal_section = f"\n\n---\nCURRENT USER: {name}"

    if plan == "gold":
        personal_section += " (⭐ Gold Member)"

    if visits:
        visit_lines = []
        for v in visits:
            station = v.get("station", "Unknown")
            date = v.get("date", "")
            stype = v.get("station_type", "")
            line = f"  - {station}"
            if stype:
                line += f" ({stype})"
            if date:
                line += f" — visited {date}"
            visit_lines.append(line)
        personal_section += (
            f"\n{name} has previously visited these RNLI stations:\n"
            + "\n".join(visit_lines)
            + "\n\nIf they ask about their visits (e.g. 'When did I visit Poole?' or "
            "'What was my last station visit?'), answer directly from this list "
            "without calling any tools."
        )

    if plan == "gold":
        personal_section += (
            "\n\nAs a Gold Member, when you retrieve station data, also share any "
            "'recent_launch' information included in the station response. "
            "Present it as an exclusive Gold Member insight, e.g.: "
            "'🚤 Latest Launch (Gold exclusive): <date> — <description>'"
        )

    return prompt + personal_section


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_nearest_stations",
            "description": (
                "Find the nearest RNLI lifeboat stations to a given UK location. "
                "The location can be a postcode (e.g. 'SW1A 2AA') or a town name "
                "(e.g. 'Brighton'). Returns a list of stations with distances, "
                "addresses, and Google Maps walking directions links."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "UK postcode or town name to search from",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of nearest stations to return (default 3, max 10)",
                        "default": 3,
                    },
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_station_details",
            "description": (
                "Get full details about a specific RNLI lifeboat station by name. "
                "Returns station type, county, region, country, address, "
                "Google Maps walking directions URL, and recent launch data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "station_name": {
                        "type": "string",
                        "description": "Name of the lifeboat station, e.g. 'Dover', 'Falmouth'",
                    },
                },
                "required": ["station_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_stations_by_type",
            "description": (
                "List all RNLI lifeboat stations of a given type. "
                "Use 'ALB' for all-weather lifeboats or 'ILB' for inshore lifeboats."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "station_type": {
                        "type": "string",
                        "description": "Station type: 'ALB' (all-weather lifeboat) or 'ILB' (inshore lifeboat)",
                        "enum": ["ALB", "ILB"],
                    },
                },
                "required": ["station_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_stations_by_region",
            "description": (
                "List all RNLI lifeboat stations in a specific region. "
                "Available regions include: Scotland, Wales, North East, North West, "
                "South East, South West, East, Ireland, Channel Islands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": "Region name, e.g. 'Scotland', 'Wales', 'South West'",
                    },
                },
                "required": ["region"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Lifeboat API client
# ---------------------------------------------------------------------------


class LifeboatAPIClient:
    """Thin async HTTP client for the lifeboat-api service."""

    def __init__(self, base_url: str = LIFEBOAT_API_BASE):
        self.base_url = base_url.rstrip("/")

    async def find_nearest_stations(
        self, location: str, count: int = 3
    ) -> dict[str, Any]:
        url = f"{self.base_url}/stations/nearest"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"location": location, "count": count})
            resp.raise_for_status()
            return resp.json()

    async def get_station_details(self, station_name: str) -> dict[str, Any]:
        url = f"{self.base_url}/stations/{station_name}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 404:
                return {"error": f"Station '{station_name}' not found."}
            resp.raise_for_status()
            return resp.json()

    async def list_stations_by_type(self, station_type: str) -> dict[str, Any]:
        url = f"{self.base_url}/stations"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"type": station_type})
            resp.raise_for_status()
            return resp.json()

    async def list_stations_by_region(self, region: str) -> dict[str, Any]:
        url = f"{self.base_url}/stations"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"region": region})
            resp.raise_for_status()
            return resp.json()


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible, pointing at Ollama via Gravitee)
# ---------------------------------------------------------------------------


class LLMClient:
    """OpenAI-compatible LLM client."""

    def __init__(self):
        self.client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
        self.model = LLM_MODEL
        self.temperature = LLM_TEMPERATURE

    async def process_query(
        self,
        query: str,
        tools: list[dict],
        system_prompt: str = BASE_SYSTEM_PROMPT,
    ) -> tuple[str, list[dict]]:
        """Send a user query and return (content, tool_calls)."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**params)

        if not response.choices:
            return "", []

        message = response.choices[0].message
        content = message.content or ""

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                tool_calls.append(
                    {
                        "id": tc.id,
                        "function": {"name": tc.function.name, "arguments": args},
                    }
                )
            logger.info(
                "LLM chose tools: %s",
                [tc["function"]["name"] for tc in tool_calls],
            )
        return content, tool_calls

    async def process_tool_result(
        self,
        original_query: str,
        tool_call: dict,
        tool_result: Any,
        system_prompt: str = BASE_SYSTEM_PROMPT,
    ) -> str:
        """Feed the tool result back to the LLM for a final natural-language answer."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": original_query},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_call.get("id", "call_0"),
                        "type": "function",
                        "function": {
                            "name": tool_call["function"]["name"],
                            "arguments": json.dumps(
                                tool_call["function"]["arguments"]
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id", "call_0"),
                "content": (
                    json.dumps(tool_result)
                    if isinstance(tool_result, (dict, list))
                    else str(tool_result)
                ),
            },
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class RNLIAgent:
    """RNLI Lifeboat Station Finder agent."""

    def __init__(self):
        self.api_client = LifeboatAPIClient()
        self.llm = LLMClient()
        self._initialized = False

    async def initialize(self):
        """Verify the lifeboat API is reachable."""
        url = f"{self.api_client.base_url}/health"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        logger.info("Lifeboat API is healthy")
        self._initialized = True

    async def _dispatch_tool(self, tool_name: str, args: dict) -> Any:
        """Call the appropriate lifeboat API method."""
        if tool_name == "find_nearest_stations":
            location = args.get("location", "")
            count = int(args.get("count", 3))
            return await self.api_client.find_nearest_stations(location, count)
        elif tool_name == "get_station_details":
            station_name = args.get("station_name", "")
            return await self.api_client.get_station_details(station_name)
        elif tool_name == "list_stations_by_type":
            station_type = args.get("station_type", "ALB")
            return await self.api_client.list_stations_by_type(station_type)
        elif tool_name == "list_stations_by_region":
            region = args.get("region", "")
            return await self.api_client.list_stations_by_region(region)
        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def process_request(self, raw_message: str) -> str:
        """Process a user message (with optional context prefix) and return a response."""
        # Extract user context if present
        context, message = extract_user_context(raw_message)
        system_prompt = build_system_prompt(context)

        if context:
            logger.info(
                "User context: name=%s, plan=%s, visits=%d",
                context.get("name"),
                context.get("plan"),
                len(context.get("visits") or []),
            )

        logger.info("=" * 60)
        logger.info("User: %s", message)

        try:
            content, tool_calls = await self.llm.process_query(
                message, TOOLS, system_prompt=system_prompt
            )

            if not tool_calls:
                # LLM answered directly (e.g. from context — visit history questions)
                if content:
                    return content
                return (
                    "I can help you find RNLI lifeboat stations. "
                    "Try asking me to find stations near a postcode or town, "
                    "or ask about stations in a specific region."
                )

            tool_call = tool_calls[0]
            tool_name = tool_call["function"]["name"]
            tool_args = tool_call["function"]["arguments"]

            logger.info("Calling tool: %s with args: %s", tool_name, tool_args)
            tool_result = await self._dispatch_tool(tool_name, tool_args)
            logger.info("Tool result received")

            final_response = await self.llm.process_tool_result(
                message, tool_call, tool_result, system_prompt=system_prompt
            )

            logger.info("Response generated")
            logger.info("=" * 60)
            return final_response

        except RateLimitError as e:
            logger.warning("Rate limit hit: %s", e)
            return "You have reached the request limit. Please wait a moment before trying again."
        except httpx.HTTPError as e:
            logger.error("API error: %s", e)
            return (
                "I encountered an issue connecting to the lifeboat station database. "
                "Please try again in a moment."
            )
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            return (
                "I'm sorry, something went wrong while processing your request. "
                "Please try again."
            )


# ---------------------------------------------------------------------------
# A2A application
# ---------------------------------------------------------------------------


def create_agent_card() -> AgentCard:
    """Build the agent card describing this agent to A2A clients."""
    skill = AgentSkill(
        id="rnli_station_finder",
        name="RNLI Lifeboat Station Finder",
        description=(
            "Find RNLI lifeboat stations near any UK postcode or town. "
            "Search by region, filter by station type (ALB/ILB), and get "
            "full details including addresses, walking directions, and — "
            "for Gold Members — recent lifeboat launch history."
        ),
        tags=["rnli", "lifeboat", "maritime", "rescue", "uk", "stations"],
    )

    capabilities = AgentCapabilities(
        streaming=True,
        pushNotifications=False,
        stateTransitionHistory=True,
    )

    return AgentCard(
        name="RNLI Lifeboat Station Finder",
        version="1.0.0",
        description=(
            "AI-powered assistant for finding RNLI lifeboat stations across the "
            "UK and Ireland. Supports postcode and town-based searches, regional "
            "filtering, detailed station information with addresses and Google Maps "
            "walking directions, visit history recall, and Gold Member launch data."
        ),
        url="http://localhost:8003",
        capabilities=capabilities,
        skills=[skill],
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        protocolVersion="0.3.0",
        preferredTransport="JSONRPC",
    )


class RNLIRequestHandler(RequestHandler):
    """A2A request handler for the RNLI agent."""

    def __init__(self):
        self.agent = RNLIAgent()
        super().__init__()

    async def _ensure_initialized(self):
        if not self.agent._initialized:
            try:
                await self.agent.initialize()
            except Exception as e:
                logger.warning(
                    "Could not connect to lifeboat API on startup: %s. "
                    "Will retry on first request.",
                    e,
                )
                self.agent._initialized = True  # allow requests to proceed

    async def on_message_send(self, params, context):
        await self._ensure_initialized()

        user_message = ""
        if params.message.parts:
            for part in params.message.parts:
                if hasattr(part, "text"):
                    user_message = part.text
                    break
                elif isinstance(part, dict):
                    if "text" in part:
                        user_message = part["text"]
                        break
                elif hasattr(part, "__dict__"):
                    part_dict = part.__dict__
                    if "text" in part_dict:
                        user_message = part_dict["text"]
                        break
                    root = part_dict.get("root")
                    if root and hasattr(root, "text"):
                        user_message = root.text
                        break
                    if root and isinstance(root, dict) and "text" in root:
                        user_message = root["text"]
                        break

        if not user_message:
            user_message = "Hello, how can you help me?"

        response_content = await self.agent.process_request(user_message)

        return Message(
            messageId=str(uuid.uuid4()),
            role=Role.agent,
            parts=[TextPart(text=response_content)],
        )

    async def on_message_send_stream(self, params, context):
        response = await self.on_message_send(params, context)
        yield response

    async def on_create_task(self, params, context=None):
        raise ServerError()

    async def on_list_tasks(self, params, context=None):
        return []

    async def on_get_task(self, params, context=None):
        raise ServerError()

    async def on_cancel_task(self, params, context=None):
        raise ServerError()

    async def on_set_task_push_notification_config(self, params, context=None):
        raise ServerError()

    async def on_get_task_push_notification_config(self, params, context=None):
        raise ServerError()

    async def on_resubscribe_to_task(self, params, context=None):
        raise ServerError()

    async def on_list_task_push_notification_config(self, params, context=None):
        return []

    async def on_delete_task_push_notification_config(self, params, context=None):
        return None


def create_app():
    agent_card = create_agent_card()
    handler = RNLIRequestHandler()
    a2a_app = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    app = a2a_app.build()

    async def startup():
        logger.info("RNLI Lifeboat Station Finder Agent starting on port %d", AGENT_SERVER_PORT)
        try:
            await handler._ensure_initialized()
        except Exception as e:
            logger.warning("Startup init failed (will retry): %s", e)

    app.add_event_handler("startup", startup)
    return app


def main():
    logging.getLogger().setLevel(logging.INFO)
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=AGENT_SERVER_PORT, log_level="info")


if __name__ == "__main__":
    main()
