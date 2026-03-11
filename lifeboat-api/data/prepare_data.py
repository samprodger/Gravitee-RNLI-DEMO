"""
Prepare RNLI Lifeboat Stations data from raw GeoJSON.

Reads stations_raw.geojson and extracts key fields into a clean stations.json
that the lifeboat API will use at runtime.

Fields extracted from GeoJSON properties:
  Station, County, Region, Division, Country, URL, SAP_ID,
  StationType, Lat, Long, LivesavingRegion, LivesavingArea
"""

import json
import os
import sys


RAW_PATH = os.path.join(os.path.dirname(__file__), "stations_raw.geojson")
OUT_PATH = os.path.join(os.path.dirname(__file__), "stations.json")


def clean_string(value):
    """Return stripped string or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def parse_float(value):
    """Return float or None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def process_feature(feature):
    """Extract and normalise fields from a GeoJSON feature."""
    props = feature.get("properties") or {}

    station = clean_string(props.get("Station"))
    if not station:
        return None  # skip features without a station name

    lat = parse_float(props.get("Lat"))
    lon = parse_float(props.get("Long"))

    # Fall back to geometry coordinates if property lat/lon are missing
    if lat is None or lon is None:
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates")
        if coords and len(coords) >= 2:
            lon = parse_float(coords[0])
            lat = parse_float(coords[1])

    if lat is None or lon is None:
        print(f"  WARNING: skipping '{station}' — missing coordinates", file=sys.stderr)
        return None

    return {
        "name": station,
        "county": clean_string(props.get("County")),
        "region": clean_string(props.get("Region")),
        "division": clean_string(props.get("Division")),
        "country": clean_string(props.get("Country")),
        "station_type": clean_string(props.get("StationType")),
        "lat": lat,
        "lon": lon,
        "url": clean_string(props.get("URL")),
        "sap_id": clean_string(props.get("SAP_ID")),
        "lifesaving_region": clean_string(props.get("LivesavingRegion")),
        "lifesaving_area": clean_string(props.get("LivesavingArea")),
    }


def main():
    if not os.path.exists(RAW_PATH):
        print(f"ERROR: Raw GeoJSON not found at {RAW_PATH}", file=sys.stderr)
        print(
            "Download it with:\n"
            "  curl -L -o lifeboat-api/data/stations_raw.geojson \\\n"
            '    "https://opendata.arcgis.com/api/v3/datasets/'
            '7dad2e58254345c08dfde737ec348166_0/downloads/data'
            '?format=geojson&spatialRefId=4326&where=1=1"',
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Reading {RAW_PATH} ...")
    with open(RAW_PATH, "r", encoding="utf-8") as fh:
        geojson = json.load(fh)

    feature_type = geojson.get("type")
    if feature_type != "FeatureCollection":
        print(
            f"ERROR: expected FeatureCollection, got '{feature_type}'",
            file=sys.stderr,
        )
        sys.exit(1)

    features = geojson.get("features", [])
    print(f"Processing {len(features)} raw features ...")

    stations = []
    skipped = 0
    for feature in features:
        record = process_feature(feature)
        if record:
            stations.append(record)
        else:
            skipped += 1

    # Sort by country then station name for consistent ordering
    stations.sort(key=lambda s: (s["country"] or "", s["name"]))

    print(f"  Extracted  : {len(stations)} stations")
    print(f"  Skipped    : {skipped} (missing name or coordinates)")

    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(stations, fh, indent=2, ensure_ascii=False)

    print(f"Wrote {OUT_PATH}")
    print()

    # Summary
    types = {}
    regions = {}
    for s in stations:
        t = s.get("station_type") or "Unknown"
        types[t] = types.get(t, 0) + 1
        r = s.get("region") or "Unknown"
        regions[r] = regions.get(r, 0) + 1

    print("Station types:")
    for t, count in sorted(types.items()):
        print(f"  {t}: {count}")
    print()
    print("Regions:")
    for r, count in sorted(regions.items()):
        print(f"  {r}: {count}")


if __name__ == "__main__":
    main()
