"""
RNLI Lifeboat Station Finder — A2A Agent

Exposes an Agent-to-Agent (A2A) compliant server that answers questions about
RNLI lifeboat stations.  It uses an LLM (via Gravitee Gateway / Ollama) for
natural-language understanding and calls tools via the Gravitee MCP server,
so every tool call is visible as traffic in APIM analytics.

User context is passed as a JSON prefix in the message:
  [USER_CONTEXT:{"name":"Joe Doe","email":"...","plan":"gold","visits":[...]}]
  <actual user message>
"""

import json
import logging
import os
import uuid
from typing import Any, Optional, List

import httpx
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Message, Role, TextPart
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import OpenAI, RateLimitError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8001"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://gateway:8082/llm-proxy")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "ollama:qwen3:0.6b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# MCP server exposed by Gravitee — tool calls route through APIM gateway
MCP_HTTP_URL = os.getenv(
    "MCP_HTTP_URL",
    "http://gio-apim-gateway:8082/lifeboat-mcp/mcp",
)

# Fallback health-check URL (direct to lifeboat-api while MCP server warms up)
LIFEBOAT_API_BASE = os.getenv("LIFEBOAT_API_BASE", "http://lifeboat-api:8000")

# Sea Conditions Agent — called via A2A through the Gravitee gateway
WEATHER_AGENT_URL = os.getenv(
    "WEATHER_AGENT_URL",
    "http://gio-apim-gateway:8082/weather-agent",
)

# MCP tools reserved for the weather A2A agent — excluded from LLM tool selection
# so the A2A hop is preserved in the sequence diagram rather than being short-circuited
# by direct MCP calls.  They're still visible in the MCP Inspector as a catalogue entry.
_WEATHER_MCP_TOOLS = frozenset({"getSeaConditions", "getTidalEvents"})

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

# Short prompt used for the first LLM call (tool selection).
# Kept brief so small models (qwen3:0.6b) reliably choose the correct tool.
BASE_SYSTEM_PROMPT = (
    "You are an RNLI lifeboat station assistant. "
    "Always call a tool to fetch data when the user asks about stations, locations, or visits. "
    "When the user asks about sea conditions, weather, waves, wind, or tides for a location, "
    "call findNearestStations for that location — sea and tidal conditions are fetched and "
    "appended automatically after the station search."
)

# Richer prompt used for the second LLM call (formatting tool results).
FORMAT_SYSTEM_PROMPT = (
    "You are the RNLI Lifeboat Station Finder, an expert assistant for the Royal National "
    "Lifeboat Institution. Present the tool results clearly and helpfully.\n\n"
    "For each station include: name, type (ALB=offshore, ILB=inshore), address, distance if "
    "available, and a walking directions link using the exact 'google_maps_url' value as a "
    "markdown link: [🗺️ Get walking directions](url).\n\n"
    "Do NOT include raw coordinates (lat/lon) in your response — they are handled separately.\n\n"
    "Be friendly and professional."
)


def build_system_prompts(context: Optional[dict] = None) -> tuple[str, str]:
    """Return (tool_selection_prompt, format_result_prompt) for the given context.

    The tool-selection prompt is intentionally short so small models reliably
    pick the right tool.  The format prompt is richer and used only for the
    second LLM call that turns tool results into a natural-language response.
    """
    tool_prompt = BASE_SYSTEM_PROMPT
    fmt_prompt = FORMAT_SYSTEM_PROMPT

    if not context:
        return tool_prompt, fmt_prompt

    name = context.get("name", "there")
    plan = (context.get("plan") or "standard").lower()
    visits = context.get("visits") or []

    personal_section = f"\n\nCurrent user: {name}"
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
            f"\n{name} has previously visited: "
            + "; ".join(v.get("station", "") for v in visits)
            + ". Answer visit history questions directly without calling any tools."
        )
        fmt_prompt += (
            f"\n\n{name} has previously visited these RNLI stations:\n"
            + "\n".join(visit_lines)
        )

    if plan == "gold":
        personal_section += " Show any 'recent_launch' data from tool results."
        fmt_prompt += (
            "\n\nAs a Gold Member, present any 'recent_launch' data as an exclusive "
            "Gold Member insight: '🚤 Latest Launch (Gold exclusive): <date> — <desc>'"
        )

    tool_prompt += personal_section
    fmt_prompt += f"\n\nUser: {name}"
    return tool_prompt, fmt_prompt


# ---------------------------------------------------------------------------
# MCP client helpers
# ---------------------------------------------------------------------------


def _mcp_tools_to_openai_format(mcp_tools) -> list[dict]:
    """Convert MCP tool definitions to OpenAI function calling format.

    Forces inputSchema through a JSON round-trip to ensure it is a plain
    Python dict — the MCP library may return Pydantic models that the OpenAI
    SDK cannot serialize, causing silent failures in tool calling.
    """
    openai_tools = []
    for tool in mcp_tools:
        schema = tool.inputSchema or {"type": "object", "properties": {}}
        # Force plain dict (handles Pydantic models returned by mcp library)
        if not isinstance(schema, dict):
            schema = json.loads(json.dumps(schema, default=str))
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": schema,
                },
            }
        )
    return openai_tools


def _extract_mcp_result(content_items) -> Any:
    """Parse MCP tool result content into a Python object."""
    if not content_items:
        return {}
    text = getattr(content_items[0], "text", None)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return {}


def _build_weather_map_block(lat: float, lon: float, location: str) -> str:
    """Return a single-point STATION_MAP block for a weather query forecast location."""
    data = json.dumps(
        {"stations": [{"name": f"📍 {location}", "lat": round(lat, 6), "lon": round(lon, 6),
                       "type": "", "distance_miles": None}],
         "query": location, "weather_point": True},
        separators=(",", ":"),
    )
    return f"\n[STATION_MAP:{data}]"


def _build_station_map_block(tool_result: Any, query: str) -> str:
    """
    Extract station coordinates from a tool result and return a machine-readable
    block that the frontend strips out to update the map.
    Returns an empty string if no mappable stations are found.
    """
    items: list = []
    if isinstance(tool_result, dict):
        items = tool_result.get("stations") or []
    elif isinstance(tool_result, list):
        items = tool_result

    stations = []
    for s in items:
        if not isinstance(s, dict):
            continue
        lat = s.get("lat") or s.get("latitude")
        lon = s.get("lon") or s.get("longitude")
        name = s.get("name", "")
        stype = s.get("station_type") or s.get("type", "")
        if lat is not None and lon is not None and name:
            dist = s.get("distance_miles")
            stations.append({
                "name": name,
                "lat": round(float(lat), 6),
                "lon": round(float(lon), 6),
                "type": stype,
                "distance_miles": round(float(dist), 1) if dist is not None else None,
            })

    if not stations:
        return ""

    data = json.dumps({"stations": stations, "query": query}, separators=(",", ":"))
    return f"\n[STATION_MAP:{data}]"


async def _fetch_mcp_tools() -> list[dict]:
    """Connect to the MCP server and return tools in OpenAI format."""
    async with streamablehttp_client(MCP_HTTP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = _mcp_tools_to_openai_format(result.tools)
            logger.info(
                "MCP tools available: %s", [t["function"]["name"] for t in tools]
            )
            return tools


async def _call_mcp_tool(tool_name: str, tool_args: dict) -> Any:
    """Call a tool via the Gravitee MCP server and return the parsed result."""
    logger.info("MCP call → %s(%s)", tool_name, tool_args)
    async with streamablehttp_client(MCP_HTTP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, tool_args)
            data = _extract_mcp_result(result.content)
            logger.info("MCP result received for %s", tool_name)
            return data


async def _call_weather_agent(lat: float, lon: float, location_hint: str = "") -> str:
    """
    Call the RNLI Sea Conditions Agent via A2A JSON-RPC through the Gravitee gateway.
    The call is routed through the gateway so it appears in APIM analytics and the
    real-time sequence diagram — demonstrating agent-to-agent communication.
    Returns the formatted sea conditions + tidal events text, or empty string on failure.
    """
    hint = f" near {location_hint}" if location_hint else ""
    message_text = f"conditions at {lat},{lon}{hint}"

    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "id": str(uuid.uuid4()),
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
            }
        },
    }

    logger.info("A2A call → weather-agent (%.4f, %.4f)", lat, lon)
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(f"{WEATHER_AGENT_URL}/", json=payload)
            r.raise_for_status()
            result = r.json().get("result", {})
            # A2A response: result is a Message with parts
            parts = result.get("parts", [])
            text = " ".join(
                p.get("text", "") for p in parts
                if isinstance(p, dict) and p.get("kind") == "text"
            ).strip()
            if not text:
                # Some SDK versions wrap parts differently
                text = result.get("content", "")
            logger.info("A2A weather-agent response received (%d chars)", len(text))
            return text
    except Exception as exc:
        logger.warning("Weather agent call failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# LLM client (OpenAI-compatible, pointing at Ollama via Gravitee)
# ---------------------------------------------------------------------------


class LLMClient:
    """OpenAI-compatible LLM client."""

    def __init__(self):
        self.client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=60.0)
        self.model = LLM_MODEL
        self.temperature = LLM_TEMPERATURE

    async def process_query(
        self,
        query: str,
        tools: list[dict],
        system_prompt: str = BASE_SYSTEM_PROMPT,
        history: Optional[List[dict]] = None,
        force_tool: Optional[str] = None,
    ) -> tuple[str, list[dict]]:
        """Send a user query and return (content, tool_calls).

        force_tool: if set, instruct the LLM to call this specific tool (bypasses 'auto').
        """
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": query})
        params: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            params["tools"] = tools
            if force_tool:
                params["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
            else:
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
        system_prompt: str = FORMAT_SYSTEM_PROMPT,
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
    """RNLI Lifeboat Station Finder agent — uses Gravitee MCP server for tools."""

    # Keep last N user+assistant pairs per context to give the LLM memory
    # without overwhelming small models like qwen3:0.6b
    MAX_HISTORY_TURNS = 3

    def __init__(self):
        self.llm = LLMClient()
        self._mcp_tools: list[dict] = []
        self._initialized = False
        self._conversation_histories: dict[str, list] = {}

    async def initialize(self):
        """Fetch available MCP tools from the Gravitee gateway."""
        try:
            self._mcp_tools = await _fetch_mcp_tools()
            self._initialized = True
            logger.info("Agent initialized with %d MCP tools", len(self._mcp_tools))
        except Exception as e:
            logger.warning(
                "Could not connect to MCP server at %s: %s — will retry on first request",
                MCP_HTTP_URL,
                e,
            )
            # Fall back to a direct health check of lifeboat-api
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.get(f"{LIFEBOAT_API_BASE}/health")
                logger.info("Lifeboat API is healthy (MCP server not yet ready)")
            except Exception:
                pass
            self._initialized = True  # allow requests; MCP tools fetched lazily

    async def _ensure_mcp_tools(self) -> list[dict]:
        """Lazily fetch MCP tools if not already cached."""
        if not self._mcp_tools:
            try:
                self._mcp_tools = await _fetch_mcp_tools()
            except Exception as e:
                logger.error("Failed to fetch MCP tools: %s", e)
        return self._mcp_tools

    def _get_history(self, context_id: Optional[str]) -> list:
        if not context_id:
            return []
        return self._conversation_histories.get(context_id, [])

    def _save_history(self, context_id: Optional[str], user_msg: str, agent_reply: str):
        if not context_id:
            return
        history = self._conversation_histories.get(context_id, [])
        history = history + [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": agent_reply},
        ]
        # Trim to last MAX_HISTORY_TURNS exchanges
        max_msgs = self.MAX_HISTORY_TURNS * 2
        if len(history) > max_msgs:
            history = history[-max_msgs:]
        self._conversation_histories[context_id] = history

    @staticmethod
    def _is_weather_query(message: str) -> bool:
        """Return True if the message is primarily about sea conditions / weather / tides."""
        lower = message.lower()
        weather_terms = {
            "weather", "sea condition", "wave", "swell", "wind", "tide", "tidal",
            "high water", "low water", "conditions", "visibility", "forecast",
        }
        return any(term in lower for term in weather_terms)

    async def process_request(self, raw_message: str, context_id: Optional[str] = None) -> str:
        """Process a user message (with optional context prefix) and return a response."""
        # Extract user context if present
        context, message = extract_user_context(raw_message)
        # Two-phase prompts: short for tool selection, rich for result formatting
        tool_prompt, fmt_prompt = build_system_prompts(context)

        if context:
            logger.info(
                "User context: name=%s, plan=%s, visits=%d",
                context.get("name"),
                context.get("plan"),
                len(context.get("visits") or []),
            )

        weather_intent = self._is_weather_query(message)

        logger.info("=" * 60)
        logger.info("User: %s (weather_intent=%s)", message, weather_intent)

        history = self._get_history(context_id)

        try:
            all_tools = await self._ensure_mcp_tools()
            # Exclude weather MCP tools — those are handled via A2A to preserve the
            # agent-to-agent hop in the sequence diagram.  They remain visible in the
            # MCP Inspector as a capability catalogue entry.
            tools = [
                t for t in all_tools
                if t.get("function", {}).get("name") not in _WEATHER_MCP_TOOLS
            ]

            if not tools:
                return (
                    "I'm still connecting to the station database — please try again in a moment."
                )

            # Force findNearestStations for weather queries — small models tend to
            # answer directly rather than calling a tool, so we override tool_choice.
            force = "findNearestStations" if weather_intent else None
            content, tool_calls = await self.llm.process_query(
                message, tools, system_prompt=tool_prompt, history=history,
                force_tool=force,
            )

            if not tool_calls:
                # LLM answered directly (e.g. from context — visit history questions)
                if content:
                    self._save_history(context_id, message, content)
                    return content
                return (
                    "I can help you find RNLI lifeboat stations. "
                    "Try asking me to find stations near a postcode or town, "
                    "or ask about stations in a specific region."
                )

            tool_call = tool_calls[0]
            tool_name = tool_call["function"]["name"]
            tool_args = tool_call["function"]["arguments"]

            logger.info("Calling MCP tool: %s with args: %s", tool_name, tool_args)
            tool_result = await _call_mcp_tool(tool_name, tool_args)
            logger.info("MCP tool result received")

            map_block = _build_station_map_block(tool_result, message)

            # --- Weather / sea conditions path ---
            # When the user asked about weather/tides we called findNearestStations only
            # to obtain coordinates.  Skip the station list and return weather data only.
            weather_text = ""
            weather_location = ""
            stations_for_weather = (
                tool_result.get("stations") if isinstance(tool_result, dict) else []
            ) or []

            if stations_for_weather:
                first = stations_for_weather[0]
                w_lat = first.get("lat") or first.get("latitude")
                w_lon = first.get("lon") or first.get("longitude")
                weather_location = (
                    tool_result.get("location") or first.get("name", "")
                    if isinstance(tool_result, dict) else first.get("name", "")
                )
                if w_lat is not None and w_lon is not None:
                    weather_text = await _call_weather_agent(
                        float(w_lat), float(w_lon), weather_location
                    )

            if weather_intent:
                # Return only sea conditions — no station list, single-point map
                if weather_text:
                    loc_label = f" near {weather_location}" if weather_location else ""
                    final_response = (
                        f"**🌊 Sea Conditions & Tides{loc_label}**\n\n" + weather_text
                    )
                    # Replace multi-station map with a single forecast-point marker
                    if stations_for_weather and w_lat is not None and w_lon is not None:
                        map_block = _build_weather_map_block(
                            float(w_lat), float(w_lon), weather_location
                        )
                        final_response += map_block
                    else:
                        map_block = ""
                else:
                    final_response = (
                        "I couldn't retrieve sea conditions right now — "
                        "please try again in a moment."
                    )
                    map_block = ""
            else:
                # --- Station finder path ---
                final_response = await self.llm.process_tool_result(
                    message, tool_call, tool_result, system_prompt=fmt_prompt
                )
                if map_block:
                    final_response += map_block
                if weather_text:
                    loc_label = f" near {weather_location}" if weather_location else ""
                    final_response += (
                        f"\n\n---\n\n**🌊 Sea Conditions & Tides{loc_label}**\n\n"
                        + weather_text
                    )

            # Save history without map block so the LLM isn't fed raw JSON on the next turn
            clean_for_history = final_response.replace(map_block, "").strip()
            self._save_history(context_id, message, clean_for_history)

            logger.info("Response generated")
            logger.info("=" * 60)
            return final_response

        except RateLimitError as e:
            logger.warning("Rate limit hit: %s", e)
            return "You have reached the request limit. Please wait a moment before trying again."
        except Exception as e:
            err_str = str(e)
            if "toxic" in err_str.lower() or "prompt validation" in err_str.lower() or "guard" in err_str.lower():
                logger.warning("Guard rails triggered: %s", e)
                return (
                    "I'm sorry, I can't process that request. "
                    "Please rephrase and try again."
                )
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
                    "Could not initialize on startup: %s. Will retry on first request.",
                    e,
                )
                self.agent._initialized = True

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

        context_id = getattr(params, "contextId", None)
        response_content = await self.agent.process_request(user_message, context_id=context_id)

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
