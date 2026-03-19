"""
RNLI Sea Conditions & Tidal Agent — A2A Agent

A2A-compliant agent that returns current sea conditions and tidal events
for a given UK coastal location.  Called by the RNLI Station Finder agent
after locating stations, so the demo shows agent-to-agent communication
routed through the Gravitee API Gateway.

Data sources (no API keys required):
  - Open-Meteo Marine API   — wave height, swell, sea-surface conditions
  - Open-Meteo Forecast API — wind speed/direction, weather code, visibility
  - Simplified harmonic tide model — next HW/LW times & heights for UK coast
"""

import asyncio
import json
import logging
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers.request_handler import RequestHandler, ServerError
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, Message, Role, TextPart
from openai import OpenAI

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_SERVER_PORT = int(os.getenv("AGENT_SERVER_PORT", "8001"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://gateway:8082/llm-proxy")
LLM_API_KEY = os.getenv("LLM_API_KEY", "not-needed")
LLM_MODEL = os.getenv("LLM_MODEL", "ollama:qwen3:0.6b")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

KAFKA_BOOTSTRAP    = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "redpanda:9092")
KAFKA_ENABLED      = os.getenv("KAFKA_ENABLED", "true").lower() == "true"
TOPIC_CONDITIONS   = "rnli.sea-conditions"
TOPIC_TIDES        = "rnli.tides"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Kafka publisher (fire-and-forget, never blocks the A2A response)
# ---------------------------------------------------------------------------

_kafka_producer = None


async def _get_kafka_producer():
    global _kafka_producer
    if _kafka_producer is None:
        from aiokafka import AIOKafkaProducer  # lazy import — only needed at runtime
        prod = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        await prod.start()
        _kafka_producer = prod
    return _kafka_producer


async def _publish(topic: str, record: dict) -> None:
    """Publish a record to a Kafka topic. Logs and swallows any error."""
    if not KAFKA_ENABLED:
        return
    try:
        producer = await asyncio.wait_for(_get_kafka_producer(), timeout=5.0)
        await asyncio.wait_for(producer.send(topic, record), timeout=5.0)
        logger.info("Kafka → %s (%d keys)", topic, len(record))
    except Exception as exc:
        logger.warning("Kafka publish skipped (%s): %s", topic, exc)


# ---------------------------------------------------------------------------
# Tidal model (harmonic, no API key required)
# ---------------------------------------------------------------------------

# M2 semi-diurnal tidal period in seconds (12h 25min 14sec)
_M2_PERIOD = 44_714

# Approximate tidal ranges for UK coastal regions (metres, spring range)
_TIDAL_REGIONS = [
    # (lat_min, lat_max, lon_min, lon_max, range_m)
    (51.0, 51.8, -4.0, -2.5, 10.5),   # Bristol Channel
    (53.0, 54.5, -5.5, -3.0,  7.0),   # Irish Sea (Liverpool)
    (54.5, 60.0, -6.0, -4.0,  3.5),   # Scotland west
    (56.0, 60.0, -2.5,  2.0,  3.5),   # Scotland east / North Sea north
    (51.0, 56.0, -0.5,  2.0,  4.5),   # North Sea / east coast England
    (49.5, 51.5, -6.0,  1.5,  4.5),   # English Channel + SW Approaches
]

# Standard port HW time offsets from Dover (minutes) — nearest port used for phase
_PORT_OFFSETS = [
    # (lat,   lon,    offset_min_from_Dover_HW)
    (51.1,  1.3,     0),    # Dover (reference M2 port)
    (50.7, -1.9,   -60),   # Poole
    (50.9, -1.4,    60),   # Southampton
    (50.8, -1.1,    30),   # Portsmouth
    (50.8, -0.1,   -50),   # Brighton
    (50.6, -2.5,   -40),   # Weymouth
    (50.4, -4.1,  -250),   # Plymouth
    (50.2, -5.1,  -290),   # Falmouth
    (50.5, -5.0,  -290),   # Padstow
    (50.1, -5.5,  -300),   # Penzance
    (51.5, -2.7,   110),   # Bristol (Avonmouth)
    (51.5, -3.2,    90),   # Cardiff
    (51.6, -3.9,    40),   # Swansea
    (51.7, -5.1,   -10),   # Milford Haven
    (52.7, -4.1,  -120),   # Barmouth
    (53.3, -4.6,  -150),   # Holyhead
    (53.4, -3.0,  -200),   # Liverpool
    (53.9, -3.0,  -200),   # Fleetwood
    (54.1, -3.2,  -185),   # Barrow
    (54.6, -5.9,  -310),   # Belfast
    (54.3, -0.4,    50),   # Scarborough
    (54.9, -1.4,    30),   # Sunderland
    (55.0, -1.4,    15),   # Tynemouth
    (55.9, -3.2,   -20),   # Leith
    (57.1, -2.1,   -15),   # Aberdeen
    (57.5, -1.8,   -15),   # Peterhead
    (58.4, -3.1,   -30),   # Wick
]


def _tidal_range(lat: float, lon: float) -> float:
    for lat_min, lat_max, lon_min, lon_max, rng in _TIDAL_REGIONS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return rng
    return 4.0


def _phase_offset(lat: float, lon: float) -> float:
    """Return M2 phase offset (0–1) for this location via nearest standard port."""
    best_dist = float("inf")
    best_min = 0
    for plat, plon, offset_min in _PORT_OFFSETS:
        d = math.hypot(lat - plat, lon - plon)
        if d < best_dist:
            best_dist = d
            best_min = offset_min
    return (best_min * 60) / _M2_PERIOD


def compute_tides(lat: float, lon: float) -> list[dict]:
    """Return the next 4 tidal events (HW/LW) with UTC times and heights."""
    now = datetime.now(timezone.utc)
    tidal_range = _tidal_range(lat, lon)
    phase_off = _phase_offset(lat, lon)

    # Phase at epoch (2024-01-01 00:00 UTC is close to a Dover HW)
    epoch = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    elapsed = (now - epoch).total_seconds()
    current_phase = (elapsed / _M2_PERIOD + phase_off) % 1.0

    # Time (seconds) to next HW (phase→0.0) and next LW (phase→0.5)
    time_to_hw = ((0.0 - current_phase) % 1.0) * _M2_PERIOD
    time_to_lw = ((0.5 - current_phase) % 1.0) * _M2_PERIOD

    # Skip events too close (within 5 min)
    if time_to_hw < 300:
        time_to_hw += _M2_PERIOD
    if time_to_lw < 300:
        time_to_lw += _M2_PERIOD

    mean_level = max(1.0, tidal_range * 0.3)
    hw_h = round(mean_level + tidal_range / 2, 1)
    lw_h = round(max(0.2, mean_level - tidal_range / 2), 1)

    hw1 = now + timedelta(seconds=time_to_hw)
    lw1 = now + timedelta(seconds=time_to_lw)

    events = sorted([
        {"type": "High Water", "time": hw1, "height_m": hw_h},
        {"type": "Low Water",  "time": lw1, "height_m": lw_h},
        {"type": "High Water", "time": hw1 + timedelta(seconds=_M2_PERIOD), "height_m": hw_h},
        {"type": "Low Water",  "time": lw1 + timedelta(seconds=_M2_PERIOD), "height_m": lw_h},
    ], key=lambda e: e["time"])

    def _in_from_now(t: datetime) -> str:
        secs = int((t - now).total_seconds())
        h, m = divmod(secs // 60, 60)
        if h and m:
            return f"in {h}h {m}m"
        elif h:
            return f"in {h}h"
        else:
            return f"in {m}m"

    return [
        {
            "type": e["type"],
            "time": e["time"].strftime("%H:%M UTC"),
            "in": _in_from_now(e["time"]),
            "height_m": e["height_m"],
        }
        for e in events
    ]


# ---------------------------------------------------------------------------
# Open-Meteo data fetchers
# ---------------------------------------------------------------------------

_WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Thunderstorm + heavy hail",
}
_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]


def _cardinal(deg: float) -> str:
    return _DIRS[round(deg / 22.5) % 16]


def _beaufort(mph: float) -> str:
    thresholds = [(1,"Calm"),(7,"Light air"),(18,"Light-moderate breeze"),
                  (28,"Moderate breeze"),(38,"Fresh breeze"),(49,"Strong breeze"),
                  (61,"Near gale"),(74,"Gale"),(88,"Severe gale")]
    for limit, label in thresholds:
        if mph < limit:
            return label
    return "Storm"


async def _fetch_marine(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "UTC",
        "current": ",".join([
            "wave_height","wave_direction","wave_period",
            "swell_wave_height","swell_wave_direction","swell_wave_period",
        ]),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(OPEN_METEO_MARINE_URL, params=params)
        r.raise_for_status()
        return r.json().get("current", {})


async def _fetch_forecast(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "UTC",
        "current": "wind_speed_10m,wind_direction_10m,weather_code,visibility",
        "daily": "sunrise,sunset",
        "wind_speed_unit": "mph",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(OPEN_METEO_FORECAST_URL, params=params)
        r.raise_for_status()
        data = r.json()
        result = data.get("current", {})
        daily = data.get("daily", {})
        sunrises = daily.get("sunrise", [])
        sunsets = daily.get("sunset", [])
        if sunrises:
            result["_sunrise"] = sunrises[0]  # ISO string e.g. "2026-03-19T06:32"
        if sunsets:
            result["_sunset"] = sunsets[0]
        return result


def _coastal_warning(wind_mph: float | None, wave_m: float | None) -> dict | None:
    """Derive a coastal warning level from wind speed and wave height."""
    w = wind_mph or 0.0
    h = wave_m or 0.0
    if w >= 55 or h >= 4.0:
        return {"level": "Red", "colour": "🔴",
                "reason": f"Extreme conditions — wind {round(w)} mph, waves {h}m"}
    if w >= 38 or h >= 2.5:
        return {"level": "Amber", "colour": "🟠",
                "reason": f"Rough conditions — wind {round(w)} mph, waves {h}m"}
    if w >= 25 or h >= 1.5:
        return {"level": "Yellow", "colour": "🟡",
                "reason": f"Moderate conditions — wind {round(w)} mph, waves {h}m"}
    return None


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------


class LLMClient:
    def __init__(self):
        self._client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=60.0)

    def format_conditions(self, location_hint: str, raw: dict) -> str:
        prompt = (
            "You are a concise RNLI sea safety advisor. Format the data below into a "
            "short briefing (under 200 words).\n"
            "Structure: 1) any coastal_warning (show level + reason prominently), "
            "2) current conditions (wind speed+direction, waves, swell, visibility, weather), "
            "3) sunrise/sunset UTC, 4) tidal events with time-from-now.\n"
            "Use plain language and emoji where helpful. "
            "Only show a warning if coastal_warning is present in the data. "
            "Do NOT mention warnings, safety notes, or thresholds if coastal_warning is absent.\n"
            f"Location: {location_hint}\n\n"
            f"Data:\n{json.dumps(raw, indent=2)}"
        )
        resp = self._client.chat.completions.create(
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Sea conditions + tidal agent core
# ---------------------------------------------------------------------------


class SeaConditionsAgent:

    def __init__(self):
        self._llm = LLMClient()

    @staticmethod
    def _parse_coords(text: str) -> tuple[float, float] | None:
        m = re.search(r"(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", text)
        if m:
            return float(m.group(1)), float(m.group(2))
        return None

    @staticmethod
    def _extract_location_hint(text: str) -> str:
        """Pull a human-readable location name from the message if present."""
        m = re.search(r"near\s+(.+?)(?:\s+\(|$)", text, re.IGNORECASE)
        return m.group(1).strip() if m else "the requested location"

    async def process(self, message: str) -> str:
        coords = self._parse_coords(message)
        if not coords:
            return (
                "I need a location as latitude,longitude "
                "(e.g. 'conditions at 50.71,-1.98')."
            )
        lat, lon = coords
        location_hint = self._extract_location_hint(message)
        logger.info("Sea conditions request: %.4f, %.4f (%s)", lat, lon, location_hint)

        # Fetch marine and weather data concurrently
        try:
            marine, forecast = await asyncio.gather(
                _fetch_marine(lat, lon),
                _fetch_forecast(lat, lon),
                return_exceptions=True,
            )
            if isinstance(marine, Exception):
                logger.warning("Marine fetch failed: %s", marine)
                marine = {}
            if isinstance(forecast, Exception):
                logger.warning("Forecast fetch failed: %s", forecast)
                forecast = {}
        except Exception as exc:
            logger.warning("Open-Meteo fetch error: %s", exc)
            marine, forecast = {}, {}

        tides = compute_tides(lat, lon)

        raw: dict[str, Any] = {}

        if marine:
            wh = marine.get("wave_height")
            wd = marine.get("wave_direction")
            wp = marine.get("wave_period")
            sh = marine.get("swell_wave_height")
            sd = marine.get("swell_wave_direction")
            if wh is not None:
                raw["wave_height_m"] = round(float(wh), 1)
            if wp is not None:
                raw["wave_period_s"] = round(float(wp), 1)
            if wd is not None:
                raw["wave_direction"] = _cardinal(float(wd))
            if sh is not None:
                raw["swell_height_m"] = round(float(sh), 1)
            if sd is not None:
                raw["swell_direction"] = _cardinal(float(sd))

        wind_mph_val = None
        if forecast:
            ws = forecast.get("wind_speed_10m")
            wdir = forecast.get("wind_direction_10m")
            wcode = forecast.get("weather_code")
            vis = forecast.get("visibility")
            sunrise = forecast.get("_sunrise")
            sunset = forecast.get("_sunset")
            if ws is not None:
                wind_mph_val = round(float(ws))
                raw["wind_speed_mph"] = wind_mph_val
                raw["wind_description"] = _beaufort(float(ws))
            if wdir is not None:
                raw["wind_direction"] = _cardinal(float(wdir))
            if wcode is not None:
                raw["conditions"] = _WMO.get(int(wcode), "Unknown")
            if vis is not None:
                raw["visibility_km"] = round(float(vis) / 1000, 1)
            if sunrise:
                raw["sunrise_utc"] = sunrise[11:16] if len(sunrise) >= 16 else sunrise
            if sunset:
                raw["sunset_utc"] = sunset[11:16] if len(sunset) >= 16 else sunset

        warning = _coastal_warning(wind_mph_val, raw.get("wave_height_m"))
        if warning:
            raw["coastal_warning"] = warning

        raw["tidal_events"] = tides

        # Publish to Kafka — fire and forget, never delays the A2A response
        _ts = datetime.now(timezone.utc).isoformat()
        asyncio.create_task(_publish(TOPIC_CONDITIONS, {
            "timestamp": _ts, "lat": lat, "lon": lon, "location": location_hint, **raw,
        }))
        asyncio.create_task(_publish(TOPIC_TIDES, {
            "timestamp": _ts, "lat": lat, "lon": lon, "location": location_hint,
            "tidal_events": tides,
            **({"coastal_warning": raw["coastal_warning"]} if raw.get("coastal_warning") else {}),
        }))

        # Format via LLM (call goes through Gravitee gateway → visible in sequence diagram)
        try:
            return self._llm.format_conditions(location_hint, raw)
        except Exception as exc:
            logger.warning("LLM formatting failed (%s) — using fallback", exc)
            return self._fallback_format(raw)

    @staticmethod
    def _fallback_format(raw: dict) -> str:
        lines = []
        warn = raw.get("coastal_warning")
        if warn:
            lines.append(f"{warn['colour']} **{warn['level']} Coastal Warning** — {warn['reason']}\n")
        lines.append("**Sea Conditions**")
        if raw.get("conditions"):
            lines.append(f"- Weather: {raw['conditions']}")
        if raw.get("wind_speed_mph") is not None:
            lines.append(
                f"- Wind: {raw['wind_speed_mph']} mph {raw.get('wind_direction','')} "
                f"({raw.get('wind_description','')})"
            )
        if raw.get("wave_height_m") is not None:
            lines.append(
                f"- Waves: {raw['wave_height_m']}m, {raw.get('wave_period_s','?')}s period "
                f"from {raw.get('wave_direction','?')}"
            )
        if raw.get("swell_height_m") is not None:
            lines.append(f"- Swell: {raw['swell_height_m']}m from {raw.get('swell_direction','?')}")
        if raw.get("visibility_km") is not None:
            lines.append(f"- Visibility: {raw['visibility_km']} km")
        if raw.get("sunrise_utc") or raw.get("sunset_utc"):
            lines.append(
                f"- 🌅 Sunrise: {raw.get('sunrise_utc','?')} UTC  |  "
                f"🌇 Sunset: {raw.get('sunset_utc','?')} UTC"
            )
        if raw.get("tidal_events"):
            lines.append("\n**Tidal Events (UTC)**")
            for t in raw["tidal_events"]:
                lines.append(f"- {t['type']}: {t['time']} ({t.get('in','')}) — {t['height_m']}m")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# A2A request handler
# ---------------------------------------------------------------------------


class WeatherRequestHandler(RequestHandler):

    def __init__(self):
        self._agent = SeaConditionsAgent()
        super().__init__()

    def _extract_text(self, params) -> str:
        for part in (params.message.parts or []):
            if hasattr(part, "text"):
                return part.text
            if isinstance(part, dict) and "text" in part:
                return part["text"]
            if hasattr(part, "__dict__"):
                d = part.__dict__
                if "text" in d:
                    return d["text"]
                root = d.get("root")
                if root and hasattr(root, "text"):
                    return root.text
        return ""

    async def on_message_send(self, params, context=None):
        text = self._extract_text(params)
        try:
            reply = await self._agent.process(text)
        except Exception as exc:
            logger.exception("Unhandled error: %s", exc)
            reply = "Unable to retrieve sea conditions at this time."

        return Message(
            messageId=str(uuid.uuid4()),
            role=Role.agent,
            parts=[TextPart(text=reply)],
        )

    async def on_message_send_stream(self, params, context=None):
        yield await self.on_message_send(params, context)

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


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def create_app():
    agent_card = AgentCard(
        name="RNLI Sea Conditions Agent",
        version="1.0.0",
        description=(
            "Returns current sea conditions (wave height, swell, wind speed/direction, "
            "visibility) and next tidal events (high/low water times and heights) for "
            "any UK coastal location.  Called by the RNLI Station Finder agent to "
            "provide contextual sea safety information alongside station results."
        ),
        url=f"http://localhost:{AGENT_SERVER_PORT}/",
        capabilities=AgentCapabilities(streaming=True, pushNotifications=False),
        skills=[
            AgentSkill(
                id="sea-conditions",
                name="Sea Conditions & Tides",
                description=(
                    "Current sea state, wave height, swell, wind, visibility, "
                    "and next 4 tidal events for a lat/lon."
                ),
                tags=["rnli", "maritime", "weather", "tides", "sea", "safety"],
                examples=[
                    "conditions at 50.71,-1.98",
                    "sea state at 54.5,-3.5 near Whitehaven",
                ],
            )
        ],
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        protocolVersion="0.3.0",
        preferredTransport="JSONRPC",
    )

    handler = WeatherRequestHandler()
    a2a_app = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    app = a2a_app.build()

    async def startup():
        logger.info(
            "RNLI Sea Conditions Agent starting on port %d", AGENT_SERVER_PORT
        )

    app.add_event_handler("startup", startup)
    return app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=AGENT_SERVER_PORT)
