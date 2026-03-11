"""
Gravitee APIM initialisation script for the RNLI Lifeboat Station Finder demo.

Waits for the Management API to be ready, then imports and publishes:
  1. RNLI Lifeboat Stations API  (proxy → lifeboat-api:8000)
  2. RNLI Stations Agent API     (proxy → rnli-a2a-agent:8001)
  3. LLM Proxy API               (proxy → host.docker.internal:11434)
"""

import json
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APIM_BASE_URL = "http://gio-apim-management-api:8083"
ENVIRONMENT = "DEFAULT"
ORGANIZATION = "DEFAULT"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"
MAX_WAIT_SECONDS = 300
API_DEFS_DIR = Path(__file__).parent / "apim-apis"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str):
    print(f"[gravitee-init] {msg}", flush=True)


def get_session() -> requests.Session:
    s = requests.Session()
    s.auth = (ADMIN_USER, ADMIN_PASSWORD)
    s.headers.update({"Content-Type": "application/json"})
    return s


def wait_for_apim(session: requests.Session):
    url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/apis"
    log(f"Waiting for Management API at {APIM_BASE_URL} ...")
    deadline = time.time() + MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            r = session.get(url, timeout=5)
            if r.status_code < 500:
                log("Management API is ready.")
                return
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(5)
    log("ERROR: Timed out waiting for Management API.")
    sys.exit(1)


def import_api(session: requests.Session, definition: dict) -> str | None:
    """Import an API definition; return the API id or None on failure."""
    api_name = definition.get("api", {}).get("name", "unknown")
    url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/_import/definition"
    r = session.post(url, json=definition, timeout=30)

    if r.status_code == 400:
        err = r.text.lower()
        if "already exists" in err or "duplicate" in err:
            log(f"  API '{api_name}' already exists — looking up ID")
            return get_api_id_by_name(session, api_name)
        log(f"  ERROR importing '{api_name}': {r.text}")
        return None

    if not r.ok:
        log(f"  ERROR importing '{api_name}': {r.status_code} {r.text}")
        return None

    api_id = r.json().get("id")
    log(f"  Imported '{api_name}' → {api_id}")
    return api_id


def get_api_id_by_name(session: requests.Session, name: str) -> str | None:
    url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis"
    r = session.get(url, timeout=10)
    if not r.ok:
        return None
    for api in r.json().get("data", []):
        if api.get("name") == name:
            return api.get("id")
    return None


def publish_and_start(session: requests.Session, api_id: str, api_name: str):
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"

    # Publish
    r = session.get(base, timeout=10)
    if r.ok:
        config = r.json()
        config["lifecycleState"] = "PUBLISHED"
        session.put(base, json=config, timeout=10)

    # Start
    r = session.post(f"{base}/_start", timeout=10)
    if r.ok or (r.status_code == 400 and "already started" in r.text.lower()):
        log(f"  '{api_name}' published and started")
    else:
        log(f"  WARNING: could not start '{api_name}': {r.text}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("=" * 60)
    log("RNLI Gravitee APIM Initialisation")
    log("=" * 60)

    session = get_session()
    wait_for_apim(session)
    # Give the API a moment to fully settle
    time.sleep(5)

    definition_files = sorted(API_DEFS_DIR.glob("*.json"))
    if not definition_files:
        log("WARNING: No API definition files found — nothing to import.")
        sys.exit(0)

    success, failed = 0, 0
    for path in definition_files:
        log(f"Processing {path.name} ...")
        try:
            definition = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            log(f"  ERROR: cannot parse {path.name}: {e}")
            failed += 1
            continue

        api_name = definition.get("api", {}).get("name", path.stem)
        api_id = import_api(session, definition)
        if api_id:
            publish_and_start(session, api_id, api_name)
            success += 1
        else:
            failed += 1

    log("=" * 60)
    log(f"Done — {success} API(s) imported, {failed} failed.")
    log("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
