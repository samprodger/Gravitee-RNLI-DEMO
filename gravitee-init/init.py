"""
Gravitee APIM initialisation script for the RNLI Lifeboat Station Finder demo.

Waits for the Management API to be ready, then imports and publishes:
  1. RNLI Lifeboat Stations API  (proxy → lifeboat-api:8000, keyless plan)
  2. RNLI Stations Agent API     (proxy → rnli-a2a-agent:8001, keyless plan)
  3. LLM Proxy API               (proxy → host.docker.internal:11434, keyless plan)
  4. RNLI Visited Stations API   (proxy → lifeboat-api:8000, JWT plan via AM JWKS)
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

# Flag key inside JSON definitions to request a JWT plan instead of keyless
JWT_PLAN_FLAG = "_rnli_jwt_plan"

# AM JWKS endpoint (used when creating JWT plans)
AM_JWKS_URL = "http://gio-am-gateway:8092/gravitee/oidc/.well-known/jwks.json"


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


def ensure_published_plan(session: requests.Session, api_id: str, api_name: str):
    """
    Ensure the API has at least one published keyless plan.
    """
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"

    r = session.get(f"{base}/plans", timeout=10)
    if not r.ok:
        log(f"  WARNING: could not list plans for '{api_name}': {r.text}")
        return

    plans = r.json().get("data", [])

    for plan in plans:
        plan_id = plan.get("id")
        if (plan.get("status") or "").upper() == "PUBLISHED":
            log(f"  Plan '{plan.get('name')}' already published for '{api_name}'")
            continue
        pub_r = session.post(f"{base}/plans/{plan_id}/_publish", timeout=10)
        if pub_r.ok:
            log(f"  Published plan '{plan.get('name')}' for '{api_name}'")
        else:
            log(f"  WARNING: could not publish plan '{plan.get('name')}': {pub_r.text}")

    if plans:
        return

    log(f"  No plans found for '{api_name}' — creating keyless Free Plan")
    plan_body = {
        "name": "Free Plan",
        "definitionVersion": "V4",
        "status": "STAGING",
        "security": {"type": "KEY_LESS"},
        "mode": "STANDARD",
        "flows": [],
    }
    cr = session.post(f"{base}/plans", json=plan_body, timeout=10)
    if not cr.ok:
        log(f"  WARNING: could not create plan for '{api_name}': {cr.text}")
        return

    plan_id = cr.json().get("id")
    pub_r = session.post(f"{base}/plans/{plan_id}/_publish", timeout=10)
    if pub_r.ok:
        log(f"  Created and published Free Plan for '{api_name}'")
    else:
        log(f"  WARNING: could not publish new plan for '{api_name}': {pub_r.text}")


def ensure_jwt_plan(session: requests.Session, api_id: str, api_name: str):
    """
    Ensure the API has a published JWT plan validating tokens via AM JWKS.
    """
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"

    r = session.get(f"{base}/plans", timeout=10)
    if r.ok:
        plans = r.json().get("data", [])
        for plan in plans:
            plan_id = plan.get("id")
            if (plan.get("status") or "").upper() == "PUBLISHED":
                log(f"  JWT plan already published for '{api_name}'")
                return
            session.post(f"{base}/plans/{plan_id}/_publish", timeout=10)
        if plans:
            return

    log(f"  Creating JWT plan for '{api_name}'...")
    plan_body = {
        "name": "JWT Plan",
        "definitionVersion": "V4",
        "status": "STAGING",
        "security": {
            "type": "JWT",
            "configuration": {
                "signature": "RSA_RS256",
                "publicKeyResolver": "JWKS_URL",
                "resolverParameter": AM_JWKS_URL,
                "connectTimeout": 2000,
                "requestTimeout": 2000,
                "followRedirects": False,
                "useSystemProxy": False,
                "extractClaims": True,
                "propagateAuthHeader": True,
                "userClaim": "sub",
            },
        },
        "mode": "STANDARD",
        "flows": [],
    }
    cr = session.post(f"{base}/plans", json=plan_body, timeout=10)
    if not cr.ok:
        log(f"  WARNING: could not create JWT plan for '{api_name}': {cr.text}")
        return
    plan_id = cr.json().get("id")
    pub_r = session.post(f"{base}/plans/{plan_id}/_publish", timeout=10)
    if pub_r.ok:
        log(f"  Created and published JWT plan for '{api_name}'")
    else:
        log(f"  WARNING: could not publish JWT plan for '{api_name}': {pub_r.text}")


def cleanup_wrong_plan_type(session: requests.Session, api_id: str, api_name: str, target_use_jwt: bool):
    """
    Close and delete plans that are the wrong type so the correct plan can be created.
    E.g. if we want a keyless plan but a JWT plan exists, remove it.
    """
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"
    r = session.get(f"{base}/plans", timeout=10)
    if not r.ok:
        return

    plans = r.json().get("data", [])
    for plan in plans:
        plan_id = plan.get("id")
        plan_sec = ((plan.get("security") or {}).get("type") or "").upper()
        plan_status = (plan.get("status") or "").upper()

        is_jwt = plan_sec == "JWT"
        is_keyless = plan_sec in ("KEY_LESS", "KEYLESS")

        # Remove if it's the wrong type
        should_delete = (not target_use_jwt and is_jwt) or (target_use_jwt and is_keyless)
        if not should_delete:
            continue

        log(f"  Removing mismatched {plan_sec} plan from '{api_name}' (want {'JWT' if target_use_jwt else 'KEYLESS'})")
        # Must close before deleting
        if plan_status == "PUBLISHED":
            close_r = session.post(f"{base}/plans/{plan_id}/_close", timeout=10)
            if not close_r.ok:
                log(f"    WARNING: could not close plan {plan_id}: {close_r.text}")
                continue
        del_r = session.delete(f"{base}/plans/{plan_id}", timeout=10)
        if del_r.ok:
            log(f"    Deleted {plan_sec} plan {plan_id}")
        else:
            log(f"    WARNING: could not delete plan {plan_id}: {del_r.text}")


def publish_and_start(session: requests.Session, api_id: str, api_name: str, use_jwt: bool = False):
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"

    # Remove any plans of the wrong type first (handles re-runs after config change)
    cleanup_wrong_plan_type(session, api_id, api_name, target_use_jwt=use_jwt)

    if use_jwt:
        ensure_jwt_plan(session, api_id, api_name)
    else:
        ensure_published_plan(session, api_id, api_name)

    r = session.get(base, timeout=10)
    if r.ok:
        config = r.json()
        config["lifecycleState"] = "PUBLISHED"
        session.put(base, json=config, timeout=10)

    r = session.post(f"{base}/_start", timeout=10)
    if r.ok or (r.status_code == 400 and "already started" in r.text.lower()):
        log(f"  '{api_name}' published and started")
    else:
        log(f"  WARNING: could not start '{api_name}': {r.text}")

    # Force a deploy event so the gateway picks up plan changes immediately.
    # The v1 /deploy endpoint writes a proper deployment event to MongoDB.
    deploy_url = (
        f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}"
        f"/environments/{ENVIRONMENT}/apis/{api_id}/deploy"
    )
    dr = session.post(deploy_url, timeout=10)
    if dr.ok:
        log(f"  '{api_name}' deployed to gateway")
    else:
        log(f"  WARNING: deploy event failed for '{api_name}': {dr.text[:120]}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log("=" * 60)
    log("RNLI Gravitee APIM Initialisation")
    log("=" * 60)

    session = get_session()
    wait_for_apim(session)
    time.sleep(5)

    definition_files = sorted(API_DEFS_DIR.glob("*.json"))
    if not definition_files:
        log("WARNING: No API definition files found — nothing to import.")
        sys.exit(0)

    success, failed = 0, 0
    for path in definition_files:
        log(f"Processing {path.name} ...")
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            log(f"  ERROR: cannot parse {path.name}: {e}")
            failed += 1
            continue

        use_jwt = bool(raw.get(JWT_PLAN_FLAG, False))

        # Strip any non-standard top-level keys before importing
        definition = {k: v for k, v in raw.items() if not k.startswith("_rnli_")}

        api_name = definition.get("api", {}).get("name", path.stem)
        api_id = import_api(session, definition)
        if api_id:
            publish_and_start(session, api_id, api_name, use_jwt=use_jwt)
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
