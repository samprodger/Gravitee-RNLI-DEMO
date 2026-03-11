"""
RNLI Lifeboat Stations API

Endpoints:
  GET /health                                         - health check
  GET /stations                                       - list all stations (?type=ALB|ILB, ?region=Scotland)
  GET /stations/nearest?location=<postcode|town>&count=<n>  - nearest N stations
  GET /stations/{station_name}                        - single station detail
  GET /history                                        - visited stations for demo user (Joe Doe)
"""

import json
import math
import os
import re
import urllib.parse
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
# ---------------------------------------------------------------------------

RECENT_LAUNCHES = {
    "Poole": {
        "date": "8 March 2026",
        "description": "ALB launched to assist a 35ft yacht with engine failure 4 nautical miles south-east of Studland Bay. Two crew members returned safely to Poole Quay.",
        "lifeboat": "Severn class ALB",
        "outcome": "All safe",
    },
    "Swanage": {
        "date": "5 March 2026",
        "description": "ILB launched to assist two kayakers caught in strong currents off Peveril Ledge. Both paddlers brought safely ashore at Swanage Beach.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Tower": {
        "date": "9 March 2026",
        "description": "E-class ILB launched to assist a capsized paddleboarder near Blackfriars Bridge on the Thames. Person recovered and taken to shore uninjured.",
        "lifeboat": "E-class ILB",
        "outcome": "All safe",
    },
    "Brighton": {
        "date": "7 March 2026",
        "description": "ILB launched to assist a swimmer in difficulty off the Palace Pier. Casualty safely recovered and transferred to awaiting paramedics on the promenade.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Casualty transferred to paramedics",
    },
    "Falmouth": {
        "date": "6 March 2026",
        "description": "ALB launched to locate a fishing vessel overdue 12 nautical miles south-west of the Lizard. Vessel and crew of three located and escorted safely to Falmouth.",
        "lifeboat": "Tamar class ALB",
        "outcome": "All safe",
    },
    "Dover": {
        "date": "10 March 2026",
        "description": "ALB launched to assist a cross-Channel ferry passenger vessel with a medical emergency. Patient safely transferred to awaiting ambulance at Eastern Docks.",
        "lifeboat": "Severn class ALB",
        "outcome": "Patient transferred to hospital",
    },
    "Whitby": {
        "date": "4 March 2026",
        "description": "ILB launched to assist a fishing coble with water ingress off Whitby North Pier. Vessel towed safely to the inner harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Vessel towed to safety",
    },
    "Cromer": {
        "date": "3 March 2026",
        "description": "ALB launched to assist a cargo vessel with propulsion issues 15 nautical miles north of Cromer. Vessel towed to Yarmouth Roads.",
        "lifeboat": "Shannon class ALB",
        "outcome": "Vessel towed to port",
    },
    "Exmouth": {
        "date": "7 March 2026",
        "description": "ILB launched to assist a windsurfer with broken equipment near the Exe estuary entrance. Windsurfer recovered and returned to Exmouth beach.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Aberdeen": {
        "date": "8 March 2026",
        "description": "ALB launched to assist an offshore supply vessel with a crew medical emergency 20nm east of Aberdeen. Patient airlifted by coastguard helicopter.",
        "lifeboat": "Severn class ALB",
        "outcome": "Patient airlifted to hospital",
    },
    "Padstow": {
        "date": "5 March 2026",
        "description": "ALB launched to search for a missing person reported near Pentire Point. Search coordinated with HM Coastguard; person located safely on the cliff path.",
        "lifeboat": "Tamar class ALB",
        "outcome": "Missing person found safe",
    },
    "Plymouth": {
        "date": "9 March 2026",
        "description": "ILB launched to assist a RIB with engine failure in Plymouth Sound. Three persons safely towed back to Sutton Harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Torbay": {
        "date": "6 March 2026",
        "description": "ALB launched to assist a motor vessel aground on Thatcher Rock near Torquay. Vessel refloated on the rising tide and escorted to Torquay Marina.",
        "lifeboat": "Tamar class ALB",
        "outcome": "Vessel refloated and escorted",
    },
    "Tenby": {
        "date": "4 March 2026",
        "description": "ILB launched to assist divers who surfaced in difficulty near Caldey Island. Two divers recovered and returned safely to Tenby Harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Wells-next-the-Sea": {
        "date": "2 March 2026",
        "description": "ILB launched to assist a sailing dinghy that capsized near the main channel. Occupant rescued and dinghy recovered.",
        "lifeboat": "Atlantic 75 ILB",
        "outcome": "All safe",
    },
    "Lowestoft": {
        "date": "7 March 2026",
        "description": "ALB launched to assist a fishing vessel with net fouled in her propeller 8nm east of Lowestoft. Vessel freed and escorted to port.",
        "lifeboat": "Shannon class ALB",
        "outcome": "Vessel freed and escorted",
    },
    "Weymouth": {
        "date": "8 March 2026",
        "description": "ILB launched to locate a missing jet ski reported overdue in Weymouth Bay. Jet ski and rider located near Portland Bill, towed back to Weymouth.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "St Ives": {
        "date": "5 March 2026",
        "description": "ALB launched to assist a yacht with failed rigging in deteriorating conditions 6nm north of Cape Cornwall. Vessel towed safely to St Ives.",
        "lifeboat": "Mersey class ALB",
        "outcome": "Vessel towed to safety",
    },
    "Eastbourne": {
        "date": "6 March 2026",
        "description": "ILB launched to assist a kitesurfer in difficulty near Beachy Head. Kite surfer safely recovered and landed at Eastbourne seafront.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Margate": {
        "date": "9 March 2026",
        "description": "ILB launched to assist a small motorboat with engine failure off Botany Bay. Vessel towed safely to Margate Harbour.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Vessel towed to safety",
    },
    "Hastings": {
        "date": "3 March 2026",
        "description": "ALB launched to assist a fishing vessel with a crew member suffering chest pains 4nm south-east of Hastings. Patient transferred to paramedics on arrival.",
        "lifeboat": "Shannon class ALB",
        "outcome": "Patient transferred to hospital",
    },
    "Humber": {
        "date": "10 March 2026",
        "description": "ALB launched to assist a bulk carrier that had suffered engine failure in the Humber Estuary. Vessel assisted and escorted to Immingham.",
        "lifeboat": "Severn class ALB",
        "outcome": "Vessel escorted to port",
    },
    "Scarborough": {
        "date": "4 March 2026",
        "description": "ILB launched to assist a canoe that overturned in rough seas near South Bay. Occupant recovered suffering from cold exposure, transferred to paramedics.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "Casualty transferred to paramedics",
    },
    "Barmouth": {
        "date": "1 March 2026",
        "description": "ILB launched to assist a windsurfer in difficulty near the Barmouth Bar in strong offshore winds. Windsurfer safely recovered.",
        "lifeboat": "Atlantic 85 ILB",
        "outcome": "All safe",
    },
    "Loch Ness": {
        "date": "9 March 2026",
        "description": "ILB launched to assist a motorboat that had run aground on the northern shore of Loch Ness. Occupants transferred to shore.",
        "lifeboat": "D-class ILB",
        "outcome": "All safe",
    },
    "Hoylake": {
        "date": "6 March 2026",
        "description": "ALB launched to assist three persons cut off by the tide on a sandbank in the Dee Estuary. All three safely recovered.",
        "lifeboat": "Shannon class ALB",
        "outcome": "All safe",
    },
    "Troon": {
        "date": "7 March 2026",
        "description": "ALB launched to assist a sailing yacht dismasted in strong westerly gales in the Firth of Clyde. Vessel towed to Troon Marina.",
        "lifeboat": "Severn class ALB",
        "outcome": "Vessel towed to safety",
    },
}

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
    count: int = Query(5, ge=1, le=50, description="Number of nearest stations to return"),
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
