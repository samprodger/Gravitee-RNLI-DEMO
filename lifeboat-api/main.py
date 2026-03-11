"""
RNLI Lifeboat Stations API

Endpoints:
  GET /health                                         - health check
  GET /stations                                       - list all stations (?type=ALB|ILB, ?region=Scotland)
  GET /stations/nearest?location=<postcode|town>&count=<n>  - nearest N stations
  GET /stations/{station_name}                        - single station detail
"""

import json
import math
import os
import re
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "stations.json")

# UK postcode pattern (full or outward-only forms, e.g. "SW1A 2AA" or "SW1A")
UK_POSTCODE_RE = re.compile(
    r"^([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}|[A-Z]{1,2}\d[A-Z\d]?)$",
    re.IGNORECASE,
)

POSTCODES_IO_BASE = "https://api.postcodes.io"

# ---------------------------------------------------------------------------
# In-memory station store
# ---------------------------------------------------------------------------

_stations: List[dict] = []


def load_stations() -> List[dict]:
    """Load stations from the JSON data file."""
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(
            f"stations.json not found at {DATA_PATH}. "
            "Run lifeboat-api/data/prepare_data.py to generate it."
        )
    with open(DATA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stations
    _stations = load_stations()
    print(f"[lifeboat-api] Loaded {len(_stations)} stations from {DATA_PATH}")
    yield


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RNLI Lifeboat Stations API",
    description="Public API for RNLI lifeboat station data. Provides station lookup, nearest-station search, and filtering by type or region.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two points."""
    R = 6371.0  # Earth's radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def is_uk_postcode(value: str) -> bool:
    """Return True if value looks like a UK postcode."""
    return bool(UK_POSTCODE_RE.match(value.strip()))


async def resolve_postcode(postcode: str) -> Optional[tuple[float, float]]:
    """
    Call postcodes.io to get lat/lon for a UK postcode.
    Returns (lat, lon) or None on failure.
    """
    clean = postcode.upper().replace(" ", "")
    url = f"{POSTCODES_IO_BASE}/postcodes/{clean}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result") or {}
                lat = result.get("latitude")
                lon = result.get("longitude")
                if lat is not None and lon is not None:
                    return float(lat), float(lon)
        except Exception:
            pass
    return None


async def resolve_town(town: str) -> Optional[tuple[float, float]]:
    """
    Call postcodes.io places API to get lat/lon for a UK town name.
    Returns (lat, lon) or None on failure.
    """
    url = f"{POSTCODES_IO_BASE}/places"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(url, params={"q": town, "limit": 1})
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result") or []
                if result:
                    place = result[0]
                    lat = place.get("latitude")
                    lon = place.get("longitude")
                    if lat is not None and lon is not None:
                        return float(lat), float(lon)
        except Exception:
            pass
    return None


def _normalise_name(s: str) -> str:
    """Lower-case, collapse whitespace for name comparison."""
    return re.sub(r"\s+", " ", s.strip().lower())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", summary="Health check")
def health():
    return {"status": "ok", "stations_loaded": len(_stations)}


@app.get("/stations", summary="List all stations")
def list_stations(
    type: Optional[str] = Query(
        None,
        description="Filter by station type: ALB (all-weather lifeboat) or ILB (inshore lifeboat)",
    ),
    region: Optional[str] = Query(
        None,
        description="Filter by region name, e.g. Scotland, Wales, South West",
    ),
):
    """Return all lifeboat stations, with optional filtering by type or region."""
    results = _stations

    if type:
        type_upper = type.upper()
        results = [s for s in results if (s.get("station_type") or "").upper() == type_upper]

    if region:
        region_lower = region.lower()
        results = [
            s for s in results
            if (s.get("region") or "").lower() == region_lower
        ]

    return {
        "count": len(results),
        "stations": results,
    }


@app.get("/stations/nearest", summary="Find nearest stations to a postcode or town")
async def nearest_stations(
    location: str = Query(
        ...,
        description="UK postcode (e.g. 'SW1A 2AA') or town name (e.g. 'Brighton')",
    ),
    count: int = Query(5, ge=1, le=50, description="Number of nearest stations to return"),
):
    """
    Find the nearest RNLI lifeboat stations to a given UK postcode or town.

    The location string is first checked against a UK postcode pattern.
    If it looks like a postcode, postcodes.io is used for geocoding.
    Otherwise the places API is used to geocode a town name.
    """
    loc = location.strip()

    coords: Optional[tuple[float, float]] = None

    if is_uk_postcode(loc):
        coords = await resolve_postcode(loc)
        if coords is None:
            raise HTTPException(
                status_code=404,
                detail=f"Could not geocode postcode '{loc}'. Check the postcode is valid.",
            )
    else:
        coords = await resolve_town(loc)
        if coords is None:
            raise HTTPException(
                status_code=404,
                detail=f"Could not find location '{loc}'. Try a UK postcode or a larger town name.",
            )

    lat, lon = coords

    # Calculate distances and sort
    with_distance = []
    for station in _stations:
        slat = station.get("lat")
        slon = station.get("lon")
        if slat is None or slon is None:
            continue
        dist = haversine_km(lat, lon, slat, slon)
        entry = dict(station)
        entry["distance_km"] = round(dist, 2)
        with_distance.append(entry)

    with_distance.sort(key=lambda s: s["distance_km"])
    nearest = with_distance[:count]

    return {
        "location": loc,
        "latitude": lat,
        "longitude": lon,
        "count": len(nearest),
        "stations": nearest,
    }


@app.get("/stations/{station_name}", summary="Get a specific station by name")
def get_station(station_name: str):
    """
    Return detailed information for a single lifeboat station.

    The station name lookup is case-insensitive.
    """
    target = _normalise_name(station_name)

    for station in _stations:
        if _normalise_name(station.get("name", "")) == target:
            return station

    raise HTTPException(
        status_code=404,
        detail=f"Station '{station_name}' not found. Use GET /stations to list all stations.",
    )
