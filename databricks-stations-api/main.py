"""
Mock Databricks SQL REST API — RNLI Lifeboat Stations

Implements: POST /api/2.0/sql/statements  (Databricks SQL Statement Execution API)

Access control enforced by Gravitee API Gateway:
  - Bronze plan (keyless / anonymous) → Gravitee injects X-RNLI-Plan: bronze
    Returns: id, name, station_type, region  (public summary)

  - Silver plan (API key) → Gravitee injects X-RNLI-Plan: silver
    Returns: all Bronze fields + country, lat, lon, address  (location data)

  - Gold plan (JWT via Gravitee AM) → Gravitee injects X-RNLI-Plan: gold
    Returns: all Silver fields + crew_count, launches_per_year,
             recent_launch_date, recent_launch_outcome  (operational data)

The response mimics the real Databricks SQL API format (JSON_ARRAY result set).
"""

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RNLI Databricks Stations API (Mock)",
    description="Mock Databricks SQL API returning RNLI lifeboat station data "
                "at Bronze or Gold tier based on the X-RNLI-Plan header injected by Gravitee.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Station data — all fields always present in the service; Gravitee controls
# which fields callers are allowed to see via the X-RNLI-Plan header.
# ---------------------------------------------------------------------------

STATIONS: list[dict] = [
    {
        "id": "ST001", "name": "Poole", "station_type": "ALB", "region": "South West",
        "country": "England", "lat": 50.7150, "lon": -1.9870,
        "address": "6 New Quay Road, Poole, BH15 4AF",
        "crew_count": 54, "launches_per_year": 107,
        "recent_launch_date": "8 March 2026",
        "recent_launch_outcome": "ALB to 35ft yacht with engine failure 4nm SE of Studland Bay — all safe",
    },
    {
        "id": "ST002", "name": "Swanage", "station_type": "ILB", "region": "South West",
        "country": "England", "lat": 50.6080, "lon": -1.9550,
        "address": "The Pier, High Street, Swanage, BH19 2LJ",
        "crew_count": 28, "launches_per_year": 64,
        "recent_launch_date": "5 March 2026",
        "recent_launch_outcome": "ILB to two kayakers in strong currents off Peveril Ledge — all safe",
    },
    {
        "id": "ST003", "name": "Tower", "station_type": "ILB", "region": "Thames",
        "country": "England", "lat": 51.5050, "lon": -0.1190,
        "address": "Lambeth Pier, Albert Embankment, London, SE1 7SP",
        "crew_count": 25, "launches_per_year": 86,
        "recent_launch_date": "9 March 2026",
        "recent_launch_outcome": "E-class ILB to capsized paddleboarder near Blackfriars Bridge — all safe",
    },
    {
        "id": "ST004", "name": "Brighton", "station_type": "ILB", "region": "South East",
        "country": "England", "lat": 50.8190, "lon": -0.1380,
        "address": "King's Road Arches, Brighton, BN1 2FN",
        "crew_count": 22, "launches_per_year": 72,
        "recent_launch_date": "7 March 2026",
        "recent_launch_outcome": "ILB to swimmer in difficulty off the Palace Pier — transferred to paramedics",
    },
    {
        "id": "ST005", "name": "Falmouth", "station_type": "ALB", "region": "South West",
        "country": "England", "lat": 50.1523, "lon": -5.0564,
        "address": "Prince of Wales Pier, Falmouth, TR11 3DF",
        "crew_count": 46, "launches_per_year": 93,
        "recent_launch_date": "6 March 2026",
        "recent_launch_outcome": "ALB located overdue fishing vessel 12nm SW of Lizard — crew of 3 safe",
    },
    {
        "id": "ST006", "name": "Dover", "station_type": "ALB", "region": "South East",
        "country": "England", "lat": 51.1270, "lon": 1.3100,
        "address": "Eastern Docks, Dover, CT16 1JA",
        "crew_count": 51, "launches_per_year": 118,
        "recent_launch_date": "10 March 2026",
        "recent_launch_outcome": "ALB to cross-Channel ferry medical emergency — patient transferred to hospital",
    },
    {
        "id": "ST007", "name": "Aberdeen", "station_type": "ALB", "region": "Scotland",
        "country": "Scotland", "lat": 57.1435, "lon": -2.0826,
        "address": "Pocra Quay, Aberdeen, AB11 5DQ",
        "crew_count": 43, "launches_per_year": 79,
        "recent_launch_date": "8 March 2026",
        "recent_launch_outcome": "ALB to offshore supply vessel 20nm east of Aberdeen — patient airlifted",
    },
    {
        "id": "ST008", "name": "Padstow", "station_type": "ALB", "region": "South West",
        "country": "England", "lat": 50.5435, "lon": -4.9364,
        "address": "The Harbour, Padstow, PL28 8AQ",
        "crew_count": 39, "launches_per_year": 68,
        "recent_launch_date": "5 March 2026",
        "recent_launch_outcome": "ALB searched for missing person near Pentire Point — found safe on cliff path",
    },
    {
        "id": "ST009", "name": "Cromer", "station_type": "ALB", "region": "East",
        "country": "England", "lat": 52.9310, "lon": 1.2960,
        "address": "The Gangway, Cromer, NR27 9ET",
        "crew_count": 35, "launches_per_year": 61,
        "recent_launch_date": "3 March 2026",
        "recent_launch_outcome": "ALB towed cargo vessel 15nm north of Cromer to Yarmouth Roads",
    },
    {
        "id": "ST010", "name": "Tenby", "station_type": "ILB", "region": "Wales",
        "country": "Wales", "lat": 51.6740, "lon": -4.7000,
        "address": "The Harbour, Tenby, SA70 7BU",
        "crew_count": 26, "launches_per_year": 55,
        "recent_launch_date": "4 March 2026",
        "recent_launch_outcome": "ILB to divers in difficulty near Caldey Island — all safe",
    },
    {
        "id": "ST011", "name": "Loch Ness", "station_type": "ILB", "region": "Scotland",
        "country": "Scotland", "lat": 57.3295, "lon": -4.3995,
        "address": "Dochgarroch, Inverness, IV3 8JG",
        "crew_count": 18, "launches_per_year": 32,
        "recent_launch_date": "9 March 2026",
        "recent_launch_outcome": "ILB to motorboat aground on northern shore of Loch Ness — all safe",
    },
    {
        "id": "ST012", "name": "Troon", "station_type": "ALB", "region": "Scotland",
        "country": "Scotland", "lat": 55.5514, "lon": -4.6795,
        "address": "South Harbour, Troon, KA10 6DN",
        "crew_count": 37, "launches_per_year": 74,
        "recent_launch_date": "7 March 2026",
        "recent_launch_outcome": "ALB towed dismasted yacht in Firth of Clyde gales to Troon Marina",
    },
    {
        "id": "ST013", "name": "Hoylake", "station_type": "ALB", "region": "North West",
        "country": "England", "lat": 53.3906, "lon": -3.1955,
        "address": "Lifeboat Road, Hoylake, CH47 4AA",
        "crew_count": 41, "launches_per_year": 82,
        "recent_launch_date": "6 March 2026",
        "recent_launch_outcome": "ALB to three persons cut off by tide on Dee Estuary sandbank — all safe",
    },
    {
        "id": "ST014", "name": "Whitby", "station_type": "ILB", "region": "North East",
        "country": "England", "lat": 54.4870, "lon": -0.6120,
        "address": "Pier Road, Whitby, YO21 3PU",
        "crew_count": 24, "launches_per_year": 58,
        "recent_launch_date": "4 March 2026",
        "recent_launch_outcome": "ILB to fishing coble with water ingress off North Pier — towed to inner harbour",
    },
    {
        "id": "ST015", "name": "Humber", "station_type": "ALB", "region": "North East",
        "country": "England", "lat": 53.5782, "lon": 0.1105,
        "address": "Spurn Head, Kilnsea, HU12 0UB",
        "crew_count": 48, "launches_per_year": 95,
        "recent_launch_date": "10 March 2026",
        "recent_launch_outcome": "ALB to bulk carrier with engine failure in Humber Estuary — escorted to Immingham",
    },
]

# Column definitions per tier
BRONZE_COLUMNS = ["id", "name", "station_type", "region"]
SILVER_COLUMNS = ["id", "name", "station_type", "region", "country", "lat", "lon", "address"]
GOLD_COLUMNS = [
    "id", "name", "station_type", "region",
    "country", "lat", "lon", "address",
    "crew_count", "launches_per_year",
    "recent_launch_date", "recent_launch_outcome",
]


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

def build_sql_response(plan: str) -> dict:
    """
    Build a Databricks SQL API-compatible response for the given plan tier.
    """
    if plan == "gold":
        columns = GOLD_COLUMNS
    elif plan == "silver":
        columns = SILVER_COLUMNS
    else:
        columns = BRONZE_COLUMNS

    schema = {
        "column_count": len(columns),
        "columns": [
            {"name": col, "type_text": "STRING", "position": i}
            for i, col in enumerate(columns)
        ],
    }

    data_array = [
        [str(station.get(col, "")) for col in columns]
        for station in STATIONS
    ]

    return {
        "statement_id": str(uuid.uuid4()),
        "status": {"state": "SUCCEEDED"},
        "manifest": {
            "format": "JSON_ARRAY",
            "schema": schema,
            "total_row_count": len(data_array),
            "truncated": False,
        },
        "result": {
            "chunk_index": 0,
            "row_offset": 0,
            "row_count": len(data_array),
            "data_array": data_array,
        },
        # Metadata useful for demo/debugging
        "_rnli_plan_tier": plan,
        "_rnli_columns_returned": len(columns),
        "_rnli_note": (
            "Full dataset: id, name, station_type, region, country, lat, lon, "
            "address, crew_count, launches_per_year, recent_launch_date, recent_launch_outcome"
            if plan == "gold"
            else "Station locations included — upgrade to Gold for crew strength and launch history"
            if plan == "silver"
            else "Upgrade to Silver for station locations or Gold for full operational data"
        ),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "databricks-stations-api", "stations": len(STATIONS)}


@app.post("/api/2.0/sql/statements")
async def execute_statement(request: Request):
    """
    Databricks SQL Statement Execution API (mock).

    Access tier controlled by Gravitee API Gateway:
    - X-RNLI-Plan: bronze  →  id, name, station_type, region
    - X-RNLI-Plan: silver  →  + country, lat, lon, address
    - X-RNLI-Plan: gold    →  + crew_count, launches_per_year, recent launch data

    The gateway injects this header based on the API subscription plan.
    Without authentication → Bronze (keyless plan).
    With valid JWT (Gravitee AM) → Gold (JWT plan).
    """
    raw_plan = (request.headers.get("X-RNLI-Plan") or "bronze").lower().strip()
    plan = raw_plan if raw_plan in ("bronze", "silver", "gold") else "bronze"
    return build_sql_response(plan)
