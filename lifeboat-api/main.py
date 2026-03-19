"""
RNLI Lifeboat Stations API

Endpoints:
  GET /health                                         - health check
  GET /stations                                       - list all stations (?type=ALB|ILB, ?region=Scotland)
  GET /stations/nearest?location=<postcode|town>&count=<n>  - nearest N stations
  GET /stations/{station_name}                        - single station detail
  GET /history                                        - visited stations for demo user (Joe Doe)
"""

import asyncio
import json
import math
import os
import re
import urllib.parse
from contextlib import asynccontextmanager
from datetime import date, timedelta
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
# Postal addresses for key RNLI stations
# ---------------------------------------------------------------------------

STATION_ADDRESSES = {
    "Poole": "6 New Quay Road, Poole, BH15 4AF",
    "Swanage": "The Pier, High Street, Swanage, BH19 2LJ",
    "Tower": "Lambeth Pier, Albert Embankment, London, SE1 7SP",
    "Brighton": "King's Road Arches, Brighton, BN1 2FN",
    "Eastbourne": "Royal Parade, Eastbourne, BN22 7AQ",
    "Falmouth": "Prince of Wales Pier, Falmouth, TR11 3DF",
    "Dover": "Eastern Docks, Dover, CT16 1JA",
    "Whitby": "Pier Road, Whitby, YO21 3PU",
    "Scarborough": "Sandside, Scarborough, YO11 1PG",
    "Tenby": "The Harbour, Tenby, SA70 7BU",
    "Weymouth": "Newton's Cove, Weymouth, DT4 8TR",
    "Exmouth": "Mamhead Slipway, Exmouth, EX8 1QS",
    "Torbay": "Beacon Quay, Torquay, TQ1 2BG",
    "Plymouth": "Sutton Harbour, Plymouth, PL4 0DW",
    "Padstow": "The Harbour, Padstow, PL28 8AQ",
    "St Ives": "Smeaton's Pier, St Ives, TR26 1PW",
    "Penlee": "Newlyn Harbour, Penzance, TR18 5HW",
    "Aberdeen": "Pocra Quay, Aberdeen, AB11 5DQ",
    "Arbroath": "Customs House, Arbroath, DD11 1PD",
    "Dunbar": "Victoria Harbour, Dunbar, EH42 1HN",
    "Eyemouth": "The Harbour, Eyemouth, TD14 5SD",
    "Flamborough": "North Landing, Flamborough, YO15 1BJ",
    "Filey": "Coble Landing, Filey, YO14 9PU",
    "Skegness": "The Sea Front, Skegness, PE25 3EH",
    "Cromer": "The Gangway, Cromer, NR27 9ET",
    "Sheringham": "Lifeboat Plain, Sheringham, NR26 8JR",
    "Hoylake": "Lifeboat Road, Hoylake, CH47 4AA",
    "New Brighton": "Ham and Egg Terrace, New Brighton, CH45 2JR",
    "Lytham St Annes": "West Beach, Lytham St Annes, FY8 5DQ",
    "Barrow": "Roa Island, Barrow-in-Furness, LA13 0QN",
    "Workington": "Prince of Wales Dock, Workington, CA14 2NP",
    "Sunderland": "Marina, North Dock, Sunderland, SR6 0PW",
    "Hartlepool": "Victoria Harbour, Hartlepool, TS24 0SH",
    "Bridlington": "South Pier, Bridlington, YO15 3AL",
    "Wells-next-the-Sea": "The Beach, Wells-next-the-Sea, NR23 1DR",
    "Lowestoft": "Trawl Dock, Lowestoft, NR33 0AQ",
    "Southend-on-Sea": "Western Esplanade, Southend-on-Sea, SS1 1EF",
    "Margate": "Harbour Arm, Margate, CT9 1BQ",
    "Ramsgate": "Royal Harbour, Ramsgate, CT11 8LS",
    "Hastings": "Rock-a-Nore Road, Hastings, TN34 3DW",
    "Shoreham Harbour": "Kingston Wharf, Shoreham-by-Sea, BN43 5HU",
    "Littlehampton": "Rope Walk, Littlehampton, BN17 5DH",
    "Selsey": "Beach Road, Selsey, PO20 0LR",
    "Bembridge": "The Duver, Bembridge, PO35 5NJ",
    "Yarmouth": "The Quay, Yarmouth, Isle of Wight, PO41 0PE",
    "Lymington": "Bath Road, Lymington, SO41 3SE",
    "Mudeford": "The Run, Mudeford, BH23 3NT",
    "Portland": "Castle Cove, Portland, DT5 1EQ",
    "St Abbs": "The Harbour, St Abbs, TD14 5PW",
    "Dunmore East": "The Harbour, Dunmore East, Co. Waterford, X91 WF95",
    "Dun Laoghaire": "Harbour Road, Dún Laoghaire, Co. Dublin, A96 Y000",
    "Howth": "The Harbour, Howth, Co. Dublin, D13 P997",
    "Humber": "Spurn Head, Kilnsea, HU12 0UB",
    "Barmouth": "The Quay, Barmouth, LL42 1HB",
    "Tenby": "The Harbour, Tenby, SA70 7BU",
    "St Davids": "St Justinians, Pembrokeshire, SA62 6PS",
    "Fishguard": "The Harbour, Fishguard, SA65 9HE",
    "Cardigan": "Patch, Cardigan, SA43 1AF",
    "New Quay": "Pier Road, New Quay, SA45 9PS",
    "Aberystwyth": "South Beach, Aberystwyth, SY23 1JS",
    "Aberdaron": "Porth Meudwy, Aberdaron, LL53 8BS",
    "Pwllheli": "The Marina, Pwllheli, LL53 5YT",
    "Barmouth": "The Quay, Barmouth, LL42 1HB",
    "Rhyl": "East Parade, Rhyl, LL18 3AL",
    "Flint": "Oakenholt, Flint, CH6 5RW",
    "Conwy": "The Quay, Conwy, LL32 8BB",
    "Beaumaris": "The Pier, Beaumaris, LL58 8BB",
    "Moelfre": "Moelfre, Isle of Anglesey, LL72 8HH",
    "Trearddur Bay": "Trearddur Bay, Anglesey, LL65 2LP",
    "Llandudno": "The Pier, Llandudno, LL30 2LN",
    "Anglesey": "Beach Road, Rhosneigr, LL64 5QL",
    "Porthdinllaen": "Porthdinllaen, Gwynedd, LL53 6DA",
    "Loch Ness": "Dochgarroch, Inverness, IV3 8JG",
    "Invergordon": "Marine Parade, Invergordon, IV18 0HD",
    "Thurso": "Shore Street, Thurso, KW14 8DG",
    "Wick": "Harbour Place, Wick, KW1 5HA",
    "Lochinver": "The Harbour, Lochinver, IV27 4JY",
    "Stornoway": "Shell Street, Stornoway, HS1 2BS",
    "Longhope": "Osmondwall, South Walls, Hoy, KW16 3PD",
    "Lerwick": "Albert Quay, Lerwick, ZE1 0LL",
    "Kirkwall": "Harbour Street, Kirkwall, KW15 1LE",
    "Tobermory": "Main Street, Tobermory, Isle of Mull, PA75 6NU",
    "Oban": "Gallanach Road, Oban, PA34 4PD",
    "Tighnabruaich": "The Pier, Tighnabruaich, PA21 2AE",
    "Troon": "South Harbour, Troon, KA10 6DN",
    "Girvan": "The Harbour, Girvan, KA26 9GG",
    "Portpatrick": "The Harbour, Portpatrick, DG9 8JN",
    "Campbeltown": "Campbeltown Loch, Campbeltown, PA28 6JA",
}

# ---------------------------------------------------------------------------
# Recent lifeboat launch data (demo data — Gold Member exclusive)
# Dates are computed relative to today so they always look current.
# ---------------------------------------------------------------------------

def _ago(days: int) -> str:
    """Return a formatted date string for N days before today."""
    d = date.today() - timedelta(days=days)
    return d.strftime("%d %B %Y").lstrip("0")


def _build_recent_launches() -> dict:
    return {
    "Poole": {
        "date": _ago(11),
        "description": "ALB launched to assist a 35ft yacht with engine failure 4 nautical miles south-east of Studland Bay. Two crew members returned safely to Poole Quay.",
        "lifeboat": "Severn class ALB",
        "outcome": "All safe",
    },
    "Swanage": {
        "date": _ago(14),
        "description": "ILB launched to assist two kayakers caught in strong currents off Peveril Ledge. Both paddlers brought safely ashore at Swanage Beach.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Tower": {
        "date": _ago(10),
        "description": "E-class ILB launched to assist a capsized paddleboarder near Blackfriars Bridge on the Thames. Person recovered and taken to shore uninjured.",
        "lifeboat": "E-class ILB",
        "outcome": "All safe",
    },
    "Brighton": {
        "date": _ago(12),
        "description": "ILB launched to assist a swimmer in difficulty off the Palace Pier. Casualty safely recovered and transferred to awaiting paramedics on the promenade.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Casualty transferred to paramedics",
    },
    "Falmouth": {
        "date": _ago(13),
        "description": "ALB launched to locate a fishing vessel overdue 12 nautical miles south-west of the Lizard. Vessel and crew of three located and escorted safely to Falmouth.",
        "lifeboat": "Tamar class ALB",
        "outcome": "All safe",
    },
    "Dover": {
        "date": _ago(9),
        "description": "ALB launched to assist a cross-Channel ferry passenger vessel with a medical emergency. Patient safely transferred to awaiting ambulance at Eastern Docks.",
        "lifeboat": "Severn class ALB",
        "outcome": "Patient transferred to hospital",
    },
    "Whitby": {
        "date": _ago(15),
        "description": "ILB launched to assist a fishing coble with water ingress off Whitby North Pier. Vessel towed safely to the inner harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Vessel towed to safety",
    },
    "Cromer": {
        "date": _ago(16),
        "description": "ALB launched to assist a cargo vessel with propulsion issues 15 nautical miles north of Cromer. Vessel towed to Yarmouth Roads.",
        "lifeboat": "Shannon class ALB",
        "outcome": "Vessel towed to port",
    },
    "Exmouth": {
        "date": _ago(12),
        "description": "ILB launched to assist a windsurfer with broken equipment near the Exe estuary entrance. Windsurfer recovered and returned to Exmouth beach.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Aberdeen": {
        "date": _ago(11),
        "description": "ALB launched to assist an offshore supply vessel with a crew medical emergency 20nm east of Aberdeen. Patient airlifted by coastguard helicopter.",
        "lifeboat": "Severn class ALB",
        "outcome": "Patient airlifted to hospital",
    },
    "Padstow": {
        "date": _ago(14),
        "description": "ALB launched to search for a missing person reported near Pentire Point. Search coordinated with HM Coastguard; person located safely on the cliff path.",
        "lifeboat": "Tamar class ALB",
        "outcome": "Missing person found safe",
    },
    "Plymouth": {
        "date": _ago(10),
        "description": "ILB launched to assist a RIB with engine failure in Plymouth Sound. Three persons safely towed back to Sutton Harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Torbay": {
        "date": _ago(13),
        "description": "ALB launched to assist a motor vessel aground on Thatcher Rock near Torquay. Vessel refloated on the rising tide and escorted to Torquay Marina.",
        "lifeboat": "Tamar class ALB",
        "outcome": "Vessel refloated and escorted",
    },
    "Tenby": {
        "date": _ago(15),
        "description": "ILB launched to assist divers who surfaced in difficulty near Caldey Island. Two divers recovered and returned safely to Tenby Harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Wells-next-the-Sea": {
        "date": _ago(17),
        "description": "ILB launched to assist a sailing dinghy that capsized near the main channel. Occupant rescued and dinghy recovered.",
        "lifeboat": "Atlantic 75 ILB",
        "outcome": "All safe",
    },
    "Lowestoft": {
        "date": _ago(12),
        "description": "ALB launched to assist a fishing vessel with net fouled in her propeller 8nm east of Lowestoft. Vessel freed and escorted to port.",
        "lifeboat": "Shannon class ALB",
        "outcome": "Vessel freed and escorted",
    },
    "Weymouth": {
        "date": _ago(11),
        "description": "ILB launched to locate a missing jet ski reported overdue in Weymouth Bay. Jet ski and rider located near Portland Bill, towed back to Weymouth.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "St Ives": {
        "date": _ago(14),
        "description": "ALB launched to assist a yacht with failed rigging in deteriorating conditions 6nm north of Cape Cornwall. Vessel towed safely to St Ives.",
        "lifeboat": "Mersey class ALB",
        "outcome": "Vessel towed to safety",
    },
    "Eastbourne": {
        "date": _ago(13),
        "description": "ILB launched to assist a kitesurfer in difficulty near Beachy Head. Kite surfer safely recovered and landed at Eastbourne seafront.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Margate": {
        "date": _ago(10),
        "description": "ILB launched to assist a small motorboat with engine failure off Botany Bay. Vessel towed safely to Margate Harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Vessel towed to safety",
    },
    "Hastings": {
        "date": _ago(16),
        "description": "ALB launched to assist a fishing vessel with a crew member suffering chest pains 4nm south-east of Hastings. Patient transferred to paramedics on arrival.",
        "lifeboat": "Shannon class ALB",
        "outcome": "Patient transferred to hospital",
    },
    "Humber": {
        "date": _ago(9),
        "description": "ALB launched to assist a bulk carrier that had suffered engine failure in the Humber Estuary. Vessel assisted and escorted to Immingham.",
        "lifeboat": "Severn class ALB",
        "outcome": "Vessel escorted to port",
    },
    "Scarborough": {
        "date": _ago(15),
        "description": "ILB launched to assist a canoe that overturned in rough seas near South Bay. Occupant recovered suffering from cold exposure, transferred to paramedics.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Casualty transferred to paramedics",
    },
    "Barmouth": {
        "date": _ago(18),
        "description": "ILB launched to assist a windsurfer in difficulty near the Barmouth Bar in strong offshore winds. Windsurfer safely recovered.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Loch Ness": {
        "date": _ago(10),
        "description": "ILB launched to assist a motorboat that had run aground on the northern shore of Loch Ness. Occupants transferred to shore.",
        "lifeboat": "D-class ILB",
        "outcome": "All safe",
    },
    "Hoylake": {
        "date": _ago(13),
        "description": "ALB launched to assist three persons cut off by the tide on a sandbank in the Dee Estuary. All three safely recovered.",
        "lifeboat": "Shannon class ALB",
        "outcome": "All safe",
    },
    "Troon": {
        "date": _ago(12),
        "description": "ALB launched to assist a sailing yacht dismasted in strong westerly gales in the Firth of Clyde. Vessel towed to Troon Marina.",
        "lifeboat": "Severn class ALB",
        "outcome": "Vessel towed to safety",
    },
    "Bembridge": {
        "date": _ago(2),
        "description": "ILB launched after a golden retriever named Biscuit chased a ball into the Solent and was unable to return against the tide. Biscuit was recovered half a mile offshore, exhausted but unharmed, and reunited with his very relieved owner on the beach.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Dog rescued safe and well 🐕",
    },
    }


RECENT_LAUNCHES = _build_recent_launches()

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
# Station enrichment
# ---------------------------------------------------------------------------

def enrich_station(station: dict) -> dict:
    """
    Enrich a station dict with postal address, Google Maps walking directions URL,
    and (where available) recent lifeboat launch data.
    """
    name = station.get("name", "")
    lat = station.get("lat")
    lon = station.get("lon")

    # Postal address
    if name in STATION_ADDRESSES:
        station["address"] = STATION_ADDRESSES[name]
    else:
        county = station.get("county", "")
        country = station.get("country", "UK")
        parts = [p for p in [f"RNLI Lifeboat Station", county, country] if p]
        station["address"] = ", ".join(parts)

    # Google Maps walking directions URL (use coordinates for accuracy)
    if lat is not None and lon is not None:
        station["google_maps_url"] = (
            f"https://www.google.com/maps/dir/?api=1"
            f"&destination={lat},{lon}"
            f"&travelmode=walking"
        )
    else:
        encoded = urllib.parse.quote(f"{name} Lifeboat Station UK")
        station["google_maps_url"] = (
            f"https://www.google.com/maps/search/?api=1&query={encoded}"
        )

    # Recent launch (Gold Member exclusive)
    launch = RECENT_LAUNCHES.get(name)
    if launch:
        station["recent_launch"] = launch

    return station


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
    Geocode a UK town/city name using the Nominatim (OpenStreetMap) API.
    Falls back to the postcodes.io places API on failure.
    Returns (lat, lon) or None on failure.
    """
    # --- Primary: Nominatim (OSM) ---
    nominatim_url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "RNLI-Lifeboat-Station-Finder/1.0 (demo)"}
    async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
        try:
            resp = await client.get(
                nominatim_url,
                params={
                    "q": town,
                    "countrycodes": "gb",
                    "format": "json",
                    "limit": 1,
                    "addressdetails": 0,
                },
            )
            if resp.status_code == 200:
                results = resp.json()
                if results:
                    lat = results[0].get("lat")
                    lon = results[0].get("lon")
                    if lat is not None and lon is not None:
                        return float(lat), float(lon)
        except Exception:
            pass

    # --- Fallback: postcodes.io places API ---
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

    enriched = [enrich_station(dict(s)) for s in results]
    return {
        "count": len(enriched),
        "stations": enriched,
    }


@app.get("/stations/nearest", summary="Find nearest stations to a postcode or town")
async def nearest_stations(
    location: str = Query(
        ...,
        description="UK postcode (e.g. 'SW1A 2AA') or town name (e.g. 'Brighton')",
    ),
    count: int = Query(3, ge=1, le=50, description="Number of nearest stations to return"),
):
    """
    Find the nearest RNLI lifeboat stations to a given UK postcode or town.
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

    # Calculate distances, sort, enrich
    with_distance = []
    for station in _stations:
        slat = station.get("lat")
        slon = station.get("lon")
        if slat is None or slon is None:
            continue
        dist = haversine_km(lat, lon, slat, slon)
        entry = dict(station)
        entry["distance_km"] = round(dist, 2)
        entry["distance_miles"] = round(dist * 0.621371, 2)
        with_distance.append(entry)

    with_distance.sort(key=lambda s: s["distance_km"])
    nearest = with_distance[:count]
    enriched = [enrich_station(s) for s in nearest]

    return {
        "location": loc,
        "latitude": lat,
        "longitude": lon,
        "count": len(enriched),
        "stations": enriched,
    }


@app.get("/history", summary="Get visited stations history (demo: Joe Doe)")
def get_visited_history():
    """
    Return the visited stations history for the demo user (Joe Doe).
    Includes station details enriched with address and recent launch data.
    """
    visits = [
        {
            "station": "Poole",
            "region": "South West",
            "station_type": "ALB",
            "date": "2024-08-15",
            "notes": "Fascinating visit to one of the UK's busiest stations. Saw the Severn class lifeboat up close.",
        },
        {
            "station": "Swanage",
            "region": "South West",
            "station_type": "ILB",
            "date": "2024-06-03",
            "notes": "Beautiful location on the Jurassic Coast. The volunteer crew gave a brilliant tour.",
        },
        {
            "station": "Tower",
            "region": "Thames",
            "station_type": "ILB",
            "date": "2025-01-22",
            "notes": "Unique Thames river station on the South Bank — responding to incidents on the river through London.",
        },
    ]

    # Enrich each visit with address and recent launch info
    enriched_visits = []
    for v in visits:
        station_name = v["station"]
        enriched = dict(v)
        enriched["address"] = STATION_ADDRESSES.get(station_name, f"RNLI Lifeboat Station, {station_name}")

        # Build a maps URL for the station from known coords or by name
        # Look up from _stations
        for s in _stations:
            if _normalise_name(s.get("name", "")) == _normalise_name(station_name):
                lat = s.get("lat")
                lon = s.get("lon")
                if lat and lon:
                    enriched["google_maps_url"] = (
                        f"https://www.google.com/maps/dir/?api=1"
                        f"&destination={lat},{lon}&travelmode=walking"
                    )
                break

        if "google_maps_url" not in enriched:
            encoded = urllib.parse.quote(f"{station_name} Lifeboat Station UK")
            enriched["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={encoded}"

        launch = RECENT_LAUNCHES.get(station_name)
        if launch:
            enriched["recent_launch"] = launch

        enriched_visits.append(enriched)

    return {
        "user": "joe.doe@gravitee.io",
        "displayName": "Joe Doe",
        "plan": "gold",
        "visits": enriched_visits,
    }


# ---------------------------------------------------------------------------
# Sea conditions & tidal endpoints (exposed as MCP tools via Gravitee)
# ---------------------------------------------------------------------------

_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_M2_PERIOD = 44_714  # seconds (12h 25min 14sec)

_TIDAL_REGIONS = [
    (51.0, 51.8, -4.0, -2.5, 10.5),   # Bristol Channel
    (53.0, 54.5, -5.5, -3.0,  7.0),   # Irish Sea
    (54.5, 60.0, -6.0, -4.0,  3.5),   # Scotland west
    (56.0, 60.0, -2.5,  2.0,  3.5),   # Scotland east / North Sea north
    (51.0, 56.0, -0.5,  2.0,  4.5),   # North Sea / east coast
    (49.5, 51.5, -6.0,  1.5,  4.5),   # English Channel + SW Approaches
]
_PORT_OFFSETS = [
    (51.1,  1.3,    0), (50.7, -1.9,  -60), (50.9, -1.4,   60),
    (50.8, -1.1,   30), (50.8, -0.1,  -50), (50.6, -2.5,  -40),
    (50.4, -4.1, -250), (50.2, -5.1, -290), (50.5, -5.0, -290),
    (50.1, -5.5, -300), (51.5, -2.7,  110), (51.5, -3.2,   90),
    (51.6, -3.9,   40), (51.7, -5.1,  -10), (52.7, -4.1, -120),
    (53.3, -4.6, -150), (53.4, -3.0, -200), (54.6, -5.9, -310),
    (54.3, -0.4,   50), (54.9, -1.4,   30), (55.0, -1.4,   15),
    (55.9, -3.2,  -20), (57.1, -2.1,  -15), (58.4, -3.1,  -30),
]
_WMO = {
    0:"Clear sky",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
    45:"Fog",51:"Light drizzle",53:"Drizzle",55:"Heavy drizzle",
    61:"Slight rain",63:"Moderate rain",65:"Heavy rain",
    80:"Rain showers",81:"Moderate showers",82:"Heavy showers",
    95:"Thunderstorm",
}
_DIRS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]


def _cardinal(deg: float) -> str:
    return _DIRS[round(deg / 22.5) % 16]


def _tidal_range(lat: float, lon: float) -> float:
    for lat_min, lat_max, lon_min, lon_max, rng in _TIDAL_REGIONS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return rng
    return 4.0


def _phase_offset(lat: float, lon: float) -> float:
    best_dist, best_min = float("inf"), 0
    for plat, plon, offset_min in _PORT_OFFSETS:
        d = math.hypot(lat - plat, lon - plon)
        if d < best_dist:
            best_dist, best_min = d, offset_min
    return (best_min * 60) / _M2_PERIOD


@app.get("/weather/conditions", summary="Current sea conditions for a coastal location")
async def get_sea_conditions(lat: float = Query(..., description="Latitude"), lon: float = Query(..., description="Longitude")):
    """
    Returns current sea conditions from Open-Meteo: wave height, swell, wind speed
    and direction, weather description, and visibility.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            marine_r, forecast_r = await asyncio.gather(
                client.get(_MARINE_URL, params={
                    "latitude": lat, "longitude": lon, "timezone": "UTC",
                    "current": "wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_direction,swell_wave_period",
                }),
                client.get(_FORECAST_URL, params={
                    "latitude": lat, "longitude": lon, "timezone": "UTC",
                    "current": "wind_speed_10m,wind_direction_10m,weather_code,visibility",
                    "wind_speed_unit": "mph",
                }),
            )
        marine = marine_r.json().get("current", {}) if marine_r.is_success else {}
        forecast = forecast_r.json().get("current", {}) if forecast_r.is_success else {}
    except Exception:
        marine, forecast = {}, {}

    result: dict = {"latitude": lat, "longitude": lon}
    if marine:
        wh = marine.get("wave_height")
        if wh is not None:
            result["wave_height_m"] = round(float(wh), 1)
        wp = marine.get("wave_period")
        if wp is not None:
            result["wave_period_s"] = round(float(wp), 1)
        wd = marine.get("wave_direction")
        if wd is not None:
            result["wave_direction"] = _cardinal(float(wd))
        sh = marine.get("swell_wave_height")
        if sh is not None:
            result["swell_height_m"] = round(float(sh), 1)
        sd = marine.get("swell_wave_direction")
        if sd is not None:
            result["swell_direction"] = _cardinal(float(sd))
    if forecast:
        ws = forecast.get("wind_speed_10m")
        if ws is not None:
            result["wind_speed_mph"] = round(float(ws))
        wdir = forecast.get("wind_direction_10m")
        if wdir is not None:
            result["wind_direction"] = _cardinal(float(wdir))
        wcode = forecast.get("weather_code")
        if wcode is not None:
            result["conditions"] = _WMO.get(int(wcode), "Unknown")
        vis = forecast.get("visibility")
        if vis is not None:
            result["visibility_km"] = round(float(vis) / 1000, 1)
    return result


@app.get("/weather/tides", summary="Next tidal events for a coastal location")
def get_tidal_events(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    count: int = Query(4, ge=1, le=8, description="Number of tidal events to return"),
):
    """
    Returns the next high and low water times and heights using a semi-diurnal
    harmonic model calibrated to UK Standard Port offsets.
    """
    from datetime import datetime, timedelta, timezone as _tz

    def _relative_time(t: datetime, ref: datetime) -> str:
        secs = int((t - ref).total_seconds())
        h, m = divmod(secs // 60, 60)
        if h and m:
            return f"in {h}h {m}m"
        elif h:
            return f"in {h}h"
        return f"in {m}m"

    tidal_range = _tidal_range(lat, lon)
    phase_off = _phase_offset(lat, lon)

    epoch = datetime(2024, 1, 1, 0, 0, 0, tzinfo=_tz.utc)
    now = datetime.now(_tz.utc)
    elapsed = (now - epoch).total_seconds()
    current_phase = (elapsed / _M2_PERIOD + phase_off) % 1.0

    time_to_hw = ((0.0 - current_phase) % 1.0) * _M2_PERIOD
    time_to_lw = ((0.5 - current_phase) % 1.0) * _M2_PERIOD
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
        {"type": "High Water", "time": hw1 + timedelta(seconds=2 * _M2_PERIOD), "height_m": hw_h},
        {"type": "Low Water",  "time": lw1 + timedelta(seconds=2 * _M2_PERIOD), "height_m": lw_h},
    ], key=lambda e: e["time"])

    return {
        "latitude": lat,
        "longitude": lon,
        "tidal_range_m": tidal_range,
        "events": [
            {
                "type": e["type"],
                "time": e["time"].strftime("%H:%M UTC"),
                "in": _relative_time(e["time"], now),
                "date": e["time"].strftime("%Y-%m-%d"),
                "height_m": e["height_m"],
            }
            for e in events[:count]
        ],
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
            return enrich_station(dict(station))

    raise HTTPException(
        status_code=404,
        detail=f"Station '{station_name}' not found. Use GET /stations to list all stations.",
    )
