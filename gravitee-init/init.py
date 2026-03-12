"""
Gravitee APIM initialisation script for the RNLI Lifeboat Station Finder demo.

Waits for the Management API to be ready, then imports and publishes:
  1. RNLI Lifeboat Stations API  (proxy → lifeboat-api:8000, keyless plan)
  2. RNLI Stations Agent API     (proxy → rnli-a2a-agent:8001, keyless plan)
  3. LLM Proxy API               (proxy → host.docker.internal:11434/v1, guard rails + rate limit + cache)
  4. RNLI Visited Stations API   (proxy → lifeboat-api:8000, JWT plan via AM JWKS)
  5. RNLI Lifeboat MCP Server    (proxy → lifeboat-api:8000, MCP entrypoint)
  6. RNLI Databricks Stations API (proxy → databricks-stations-api:8000, Bronze keyless + Gold JWT)

For APIs with the _rnli_multi_plans flag, also creates a Gravitee APIM application
(clientId matching the AM OAuth app) and subscribes it to the JWT plan so the
gateway can resolve subscriptions when a JWT token is presented.
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

# Flag key for definitions that embed multiple plans — skips single-plan creation logic
# and instead just ensures all imported plans are published.
MULTI_PLANS_FLAG = "_rnli_multi_plans"

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


def get_api_type(session: requests.Session, api_id: str) -> str | None:
    """Return the type ('PROXY', 'LLM_PROXY', etc.) of an existing API."""
    url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"
    r = session.get(url, timeout=10)
    if r.ok:
        return r.json().get("type")
    return None


def delete_api(session: requests.Session, api_id: str, api_name: str) -> bool:
    """Close plans, stop, and delete an API. Returns True on success."""
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"
    # Close and delete all plans first
    r = session.get(f"{base}/plans", timeout=10)
    if r.ok:
        for plan in r.json().get("data", []):
            pid = plan.get("id")
            if (plan.get("status") or "").upper() == "PUBLISHED":
                session.post(f"{base}/plans/{pid}/_close", timeout=10)
            session.delete(f"{base}/plans/{pid}", timeout=10)
    # Stop then delete
    session.post(f"{base}/_stop", timeout=10)
    del_r = session.delete(base, timeout=10)
    if del_r.ok:
        log(f"  Deleted '{api_name}' (type migration)")
        return True
    log(f"  WARNING: could not delete '{api_name}': {del_r.text[:120]}")
    return False


def import_api(session: requests.Session, definition: dict) -> str | None:
    """Import an API definition; return the API id or None on failure.

    If the API already exists with a different type (e.g. LLM_PROXY vs PROXY),
    the old API is deleted and re-imported so the type change takes effect.
    Checks for type mismatches BEFORE attempting the import to avoid relying
    on the error response to detect conflicts.
    """
    api_name = definition.get("api", {}).get("name", "unknown")
    wanted_type = definition.get("api", {}).get("type", "PROXY")
    url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/_import/definition"

    # Pre-check: does an API with this name already exist?
    existing_id = get_api_id_by_name(session, api_name)
    if existing_id:
        existing_type = get_api_type(session, existing_id)
        if existing_type and existing_type != wanted_type:
            log(f"  API '{api_name}' type mismatch ({existing_type} → {wanted_type}) — re-creating")
            if delete_api(session, existing_id, api_name):
                time.sleep(2)  # brief pause for MongoDB consistency
                existing_id = None  # fall through to fresh import below
            else:
                log(f"  WARNING: could not delete '{api_name}'; keeping existing ({existing_type})")
                return existing_id
        else:
            log(f"  API '{api_name}' already exists ({existing_type}) — looking up ID")
            return existing_id

    # Fresh import (either new API or post-deletion re-import)
    r = session.post(url, json=definition, timeout=30)

    if r.status_code == 400:
        err = r.text.lower()
        if "already exists" in err or "duplicate" in err:
            # Shouldn't happen if pre-check ran, but handle gracefully
            fallback_id = get_api_id_by_name(session, api_name)
            if fallback_id:
                log(f"  API '{api_name}' already exists — looking up ID")
                return fallback_id
        log(f"  ERROR importing '{api_name}': {r.text[:120]}")
        return None

    if not r.ok:
        log(f"  ERROR importing '{api_name}': {r.status_code} {r.text[:120]}")
        return None

    api_id = r.json().get("id")
    log(f"  Imported '{api_name}' as {wanted_type} → {api_id}")
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


def ensure_all_plans_published(session: requests.Session, api_id: str, api_name: str):
    """
    Ensure every plan on the API is published. Used when the API definition embeds
    multiple plans (multi-plan mode) rather than relying on single-plan creation.
    """
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"
    r = session.get(f"{base}/plans", timeout=10)
    if not r.ok:
        log(f"  WARNING: could not list plans for '{api_name}': {r.text}")
        return
    plans = r.json().get("data", [])
    if not plans:
        log(f"  WARNING: no plans found for '{api_name}' — nothing to publish")
        return
    for plan in plans:
        plan_id = plan.get("id")
        plan_name = plan.get("name", plan_id)
        status = (plan.get("status") or "").upper()
        if status == "PUBLISHED":
            log(f"  Plan '{plan_name}' already published for '{api_name}'")
            continue
        pub_r = session.post(f"{base}/plans/{plan_id}/_publish", timeout=10)
        if pub_r.ok:
            log(f"  Published plan '{plan_name}' for '{api_name}'")
        else:
            log(f"  WARNING: could not publish plan '{plan_name}': {pub_r.text[:120]}")


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


def publish_and_start(session: requests.Session, api_id: str, api_name: str, use_jwt: bool = False, definition: dict | None = None, multi_plans: bool = False):
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"

    if multi_plans:
        # Multi-plan mode: plans were imported with the definition; just publish all of them.
        log(f"  Multi-plan mode for '{api_name}' — publishing all imported plans")
        ensure_all_plans_published(session, api_id, api_name)
    else:
        # Single-plan mode: remove wrong type then ensure correct plan exists.
        cleanup_wrong_plan_type(session, api_id, api_name, target_use_jwt=use_jwt)
        if use_jwt:
            ensure_jwt_plan(session, api_id, api_name)
        else:
            ensure_published_plan(session, api_id, api_name)

    r = session.get(base, timeout=10)
    if r.ok:
        config = r.json()
        config["lifecycleState"] = "PUBLISHED"
        if definition:
            api_def = definition.get("api", {})

            # ── endpointGroups (timeouts, providers, models) ──────────────────
            if "endpointGroups" in api_def:
                egs = api_def["endpointGroups"]
                # Gravitee stores sharedConfiguration as a dict; JSON files may
                # have it as a JSON-encoded string — parse so PUT sends the right type.
                for eg in egs:
                    sc = eg.get("sharedConfiguration")
                    if isinstance(sc, str):
                        try:
                            eg["sharedConfiguration"] = json.loads(sc)
                        except json.JSONDecodeError:
                            pass
                    # Same for any endpoint-level sharedConfigurationOverride
                    for ep in eg.get("endpoints", []):
                        sco = ep.get("sharedConfigurationOverride")
                        if isinstance(sco, str):
                            try:
                                ep["sharedConfigurationOverride"] = json.loads(sco)
                            except json.JSONDecodeError:
                                pass
                config["endpointGroups"] = egs
                log(f"  Updating endpointGroups for '{api_name}'")

            # ── flows (policies: guard rails, rate limit, cache, etc.) ─────────
            if "flows" in api_def:
                config["flows"] = api_def["flows"]
                log(f"  Updating flows for '{api_name}'")

            # ── resources (toxicity classifier, cache, etc.) ──────────────────
            if "resources" in api_def:
                # Resource configurations may be JSON-encoded strings
                for res in api_def["resources"]:
                    cfg = res.get("configuration")
                    if isinstance(cfg, str):
                        try:
                            res["configuration"] = json.loads(cfg)
                        except json.JSONDecodeError:
                            pass
                config["resources"] = api_def["resources"]
                log(f"  Updating resources for '{api_name}'")

            # ── analytics (payload logging) ───────────────────────────────────
            if "analytics" in api_def:
                config["analytics"] = api_def["analytics"]
                log(f"  Updating analytics for '{api_name}'")

        session.put(base, json=config, timeout=10)

    r = session.post(f"{base}/_start", timeout=10)
    if r.ok or (r.status_code == 400 and "already started" in r.text.lower()):
        log(f"  '{api_name}' published and started")
    else:
        log(f"  WARNING: could not start '{api_name}': {r.text}")

    # Force a deploy event so the gateway picks up changes immediately.
    # LLM_PROXY uses the v2 /deployments endpoint; other types use v1 /deploy.
    api_type = definition.get("api", {}).get("type", "PROXY") if definition else "PROXY"
    if api_type == "LLM_PROXY":
        deploy_url = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}/deployments"
        dr = session.post(deploy_url, json={"deploymentLabel": "init"}, timeout=15)
    else:
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
# APIM Application + Subscription helpers (for JWT plans)
# ---------------------------------------------------------------------------

def ensure_apim_application(session: requests.Session, client_id: str, app_name: str) -> str | None:
    """
    Ensure a Gravitee APIM application exists with the given OAuth clientId.
    Returns the application ID.
    """
    url = f"{APIM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/applications"

    # Check if it already exists
    r = session.get(url, timeout=10)
    if r.ok:
        for app in r.json().get("data", []):
            settings = app.get("settings", {}) or {}
            app_client_id = (
                settings.get("app", {}).get("client_id", "")
                or settings.get("oauth", {}).get("clientId", "")
            )
            if app_client_id == client_id:
                app_id = app.get("id")
                log(f"  APIM application '{app_name}' (clientId={client_id}) already exists: {app_id}")
                return app_id

    log(f"  Creating APIM application '{app_name}' (clientId={client_id})...")
    payload = {
        "name": app_name,
        "description": f"OAuth application (AM clientId: {client_id})",
        "applicationType": "WEB",
        "settings": {"app": {"client_id": client_id}},
    }
    cr = session.post(url, json=payload, timeout=10)
    if cr.ok:
        app_id = cr.json().get("id")
        log(f"  ✓ Created APIM application '{app_name}': {app_id}")
        return app_id
    log(f"  WARNING: could not create APIM application: {cr.text[:120]}")
    return None


def ensure_jwt_plan_subscription(session: requests.Session, api_id: str, api_name: str, app_id: str):
    """
    Ensure the Gravitee APIM application is subscribed to the API's JWT plan.
    This is required for the JWT plan to resolve subscriptions at runtime.
    """
    base = f"{APIM_BASE_URL}/management/v2/environments/{ENVIRONMENT}/apis/{api_id}"

    # Find the JWT plan ID
    r = session.get(f"{base}/plans", timeout=10)
    if not r.ok:
        log(f"  WARNING: could not list plans for JWT subscription: {r.text[:80]}")
        return
    jwt_plan_id = None
    for plan in r.json().get("data", []):
        if ((plan.get("security") or {}).get("type") or "").upper() == "JWT":
            jwt_plan_id = plan.get("id")
            break
    if not jwt_plan_id:
        log(f"  WARNING: no JWT plan found for '{api_name}' — skipping subscription")
        return

    # Check if subscription already exists
    subs_r = session.get(f"{base}/subscriptions", timeout=10)
    if subs_r.ok:
        for sub in subs_r.json().get("data", []):
            if sub.get("plan", {}).get("id") == jwt_plan_id and sub.get("status") in ("ACCEPTED", "APPROVED"):
                log(f"  JWT subscription already active for '{api_name}'")
                return

    # Create subscription
    log(f"  Creating JWT plan subscription for '{api_name}'...")
    sub_r = session.post(f"{base}/subscriptions", json={"applicationId": app_id, "planId": jwt_plan_id}, timeout=10)
    if not sub_r.ok:
        log(f"  WARNING: could not create subscription: {sub_r.text[:120]}")
        return
    sub_id = sub_r.json().get("id")

    # Accept the subscription
    acc_r = session.post(f"{base}/subscriptions/{sub_id}/_accept", json={}, timeout=10)
    if acc_r.ok:
        log(f"  ✓ JWT plan subscription created and accepted for '{api_name}'")
    else:
        log(f"  WARNING: could not accept subscription: {acc_r.text[:80]}")


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
        multi_plans = bool(raw.get(MULTI_PLANS_FLAG, False))
        # For multi-plan APIs, optionally create a Gravitee APIM application and
        # subscribe it to the JWT plan so the gateway can resolve subscriptions.
        jwt_app_client_id = raw.get("_rnli_jwt_app_client_id")  # e.g. "rnli-lifeboat"
        jwt_app_name = raw.get("_rnli_jwt_app_name", "RNLI Lifeboat Website")

        # Strip any non-standard top-level keys before importing
        definition = {k: v for k, v in raw.items() if not k.startswith("_rnli_")}

        api_name = definition.get("api", {}).get("name", path.stem)
        api_id = import_api(session, definition)
        if api_id:
            publish_and_start(session, api_id, api_name, use_jwt=use_jwt, definition=definition, multi_plans=multi_plans)
            # If a JWT app client_id is specified, create the app and subscribe
            if jwt_app_client_id:
                app_id = ensure_apim_application(session, jwt_app_client_id, jwt_app_name)
                if app_id:
                    ensure_jwt_plan_subscription(session, api_id, api_name, app_id)
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
