#!/usr/bin/env python3
"""
Gravitee AM Initialisation Script for the RNLI Lifeboat Station Finder demo.

Sets up:
  - Security domain "gravitee"
  - Browser OAuth app "RNLI Lifeboat Finder" (client_id: rnli-lifeboat)
  - User: joe.doe@gravitee.io / HelloWorld@123
  - MCP Server protected resource for the lifeboat stations API
"""

import glob
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests
import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AM_BASE_URL = os.getenv("AM_BASE_URL", "http://localhost:8093")
AM_USERNAME = os.getenv("AM_USERNAME", "admin")
AM_PASSWORD = os.getenv("AM_PASSWORD", "adminadmin")
ORGANIZATION = os.getenv("ORGANIZATION", "DEFAULT")
ENVIRONMENT = os.getenv("ENVIRONMENT", "DEFAULT")

DOMAIN_NAME = "gravitee"

APPS_CONFIG_DIR = os.getenv("APPS_CONFIG_DIR", "/app/am-apps")
MCP_SERVERS_CONFIG_DIR = os.getenv("MCP_SERVERS_CONFIG_DIR", "/app/am-mcp-servers")

# User to create
USER_FIRST_NAME = "Joe"
USER_LAST_NAME = "Doe"
USER_EMAIL = "joe.doe@gravitee.io"
USER_USERNAME = "joe.doe@gravitee.io"
USER_PASSWORD = "HelloWorld@123"

MAX_RETRIES = 40
RETRY_DELAY = 5


def log(msg: str):
    print(f"[am-init] {msg}", flush=True)


class AMInitializer:
    def __init__(self):
        self.access_token: Optional[str] = None
        self.domain_id: Optional[str] = None
        self.domain_already_enabled: bool = False
        self.session = requests.Session()

    # -----------------------------------------------------------------------
    # Wait + Auth
    # -----------------------------------------------------------------------

    def wait_for_am(self) -> bool:
        log("Waiting for AM Management API...")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.session.get(
                    f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}",
                    timeout=5,
                )
                if r.status_code in (200, 401):
                    log("AM Management API is ready.")
                    return True
            except requests.exceptions.RequestException as e:
                log(f"Attempt {attempt}/{MAX_RETRIES}: not ready yet ({e})")
            time.sleep(RETRY_DELAY)
        log("ERROR: AM API did not become ready in time.")
        return False

    def authenticate(self) -> bool:
        log("Authenticating with AM...")
        for attempt in range(1, 16):
            try:
                r = self.session.post(
                    f"{AM_BASE_URL}/management/auth/token",
                    auth=(AM_USERNAME, AM_PASSWORD),
                    timeout=10,
                )
                if r.status_code == 401:
                    log(f"  Auth attempt {attempt}/15: 401 — AM not fully ready yet, retrying...")
                    time.sleep(5)
                    continue
                r.raise_for_status()
                self.access_token = r.json().get("access_token")
                if not self.access_token:
                    log("ERROR: No access token in response.")
                    return False
                self.session.headers.update({
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                })
                log("✓ Authenticated with AM.")
                return True
            except requests.exceptions.RequestException as e:
                log(f"  Auth attempt {attempt}/15 failed: {e}")
                time.sleep(5)
        log("ERROR: Authentication failed after 15 attempts.")
        return False

    # -----------------------------------------------------------------------
    # Domain
    # -----------------------------------------------------------------------

    def create_domain(self) -> bool:
        log(f"Creating security domain '{DOMAIN_NAME}'...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains"
        payload = {
            "name": DOMAIN_NAME,
            "description": "Security domain for RNLI Lifeboat Station Finder",
            "dataPlaneId": "default",
        }
        try:
            r = self.session.post(url, json=payload, timeout=10)
            if r.status_code == 400:
                if "already exists" in str(r.json()).lower():
                    log(f"Domain '{DOMAIN_NAME}' already exists — fetching it.")
                    return self._get_existing_domain()
            r.raise_for_status()
            data = r.json()
            self.domain_id = data.get("id")
            log(f"✓ Domain created: {self.domain_id}")
            return True
        except requests.exceptions.RequestException as e:
            log(f"ERROR: Failed to create domain: {e}")
            return False

    def _get_existing_domain(self) -> bool:
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains"
        try:
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            domains = r.json()
            if isinstance(domains, dict) and "data" in domains:
                domains = domains["data"]
            for d in domains:
                if d.get("name") == DOMAIN_NAME:
                    self.domain_id = d.get("id")
                    self.domain_already_enabled = d.get("enabled", False)
                    log(f"✓ Found existing domain: {self.domain_id} (enabled={self.domain_already_enabled})")
                    return True
            log(f"ERROR: Domain '{DOMAIN_NAME}' not found.")
            return False
        except requests.exceptions.RequestException as e:
            log(f"ERROR: Failed to get domains: {e}")
            return False

    def enable_domain(self) -> bool:
        if self.domain_already_enabled:
            log(f"✓ Domain already enabled.")
            return True
        log("Enabling domain...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}"
        try:
            r = self.session.patch(url, json={"enabled": True}, timeout=10)
            r.raise_for_status()
            log("✓ Domain enabled.")
            return True
        except requests.exceptions.RequestException as e:
            log(f"ERROR: Failed to enable domain: {e}")
            return False

    def configure_dcr(self) -> bool:
        log("Configuring DCR (allow localhost + HTTP redirect URIs)...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}"
        payload = {
            "oidc": {
                "clientRegistrationSettings": {
                    "allowLocalhostRedirectUri": True,
                    "allowHttpSchemeRedirectUri": True,
                }
            }
        }
        try:
            r = self.session.patch(url, json=payload, timeout=10)
            r.raise_for_status()
            log("✓ DCR configured.")
            return True
        except requests.exceptions.RequestException as e:
            log(f"WARNING: DCR config failed (non-fatal): {e}")
            return True  # non-fatal

    # -----------------------------------------------------------------------
    # Applications
    # -----------------------------------------------------------------------

    def load_app_configs(self) -> List[Dict[str, Any]]:
        log(f"Loading app configs from {APPS_CONFIG_DIR}...")
        configs = []
        files = glob.glob(os.path.join(APPS_CONFIG_DIR, "*.yaml")) + \
                glob.glob(os.path.join(APPS_CONFIG_DIR, "*.yml"))
        for f in files:
            try:
                with open(f) as fh:
                    cfg = yaml.safe_load(fh)
                    if cfg and cfg.get("name"):
                        configs.append(cfg)
                        log(f"  Loaded: {cfg.get('name')}")
            except Exception as e:
                log(f"WARNING: Failed to load {f}: {e}")
        log(f"✓ {len(configs)} app config(s) loaded.")
        return configs

    def create_application(self, app_config: Dict[str, Any]) -> Optional[str]:
        name = app_config.get("name")
        client_id = app_config.get("clientId")
        log(f"Creating application '{name}' (clientId={client_id})...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications"
        payload = {
            "name": name,
            "type": app_config.get("type", "BROWSER"),
            "clientId": client_id,
            "clientSecret": app_config.get("clientSecret"),
            "redirectUris": app_config.get("redirectUris", []),
        }
        if app_config.get("description"):
            payload["description"] = app_config["description"]
        try:
            r = self.session.post(url, json=payload, timeout=10)
            if r.status_code == 400:
                if "already exists" in str(r.json()).lower() or "clientId" in str(r.json()):
                    log(f"  Application '{client_id}' already exists — fetching.")
                    return self._get_existing_application(client_id)
            r.raise_for_status()
            app_id = r.json().get("id")
            log(f"  ✓ Application created: {app_id}")
            return app_id
        except requests.exceptions.RequestException as e:
            log(f"ERROR: Failed to create application '{name}': {e}")
            return None

    def _get_existing_application(self, client_id: str) -> Optional[str]:
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications"
        try:
            r = self.session.get(url, params={"q": client_id}, timeout=10)
            r.raise_for_status()
            apps = r.json()
            if isinstance(apps, dict) and "data" in apps:
                apps = apps["data"]
            for app in apps:
                app_id = app.get("id")
                if not app_id:
                    continue
                detail_url = f"{url}/{app_id}"
                try:
                    dr = self.session.get(detail_url, timeout=10)
                    dr.raise_for_status()
                    oauth = dr.json().get("settings", {}).get("oauth", {})
                    if oauth.get("clientId") == client_id:
                        log(f"  ✓ Found existing application: {app_id}")
                        return app_id
                except Exception:
                    continue
        except requests.exceptions.RequestException as e:
            log(f"ERROR: Failed to search applications: {e}")
        return None

    def configure_application_settings(self, app_id: str, app_config: Dict[str, Any]) -> bool:
        scopes = app_config.get("scopes", [])
        if not scopes:
            return True
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{app_id}"
        scope_settings = [{"scope": s, "defaultScope": False, "scopeApproval": 300} for s in scopes]
        payload = {"settings": {"oauth": {"scopeSettings": scope_settings}}}
        try:
            r = self.session.put(url, json=payload, timeout=10)
            r.raise_for_status()
            log(f"  ✓ Scopes configured: {scopes}")
            return True
        except requests.exceptions.RequestException as e:
            log(f"WARNING: Failed to configure scopes: {e}")
            return True  # non-fatal

    def add_identity_provider(self, app_id: str, app_name: str) -> bool:
        log(f"  Adding identity provider to '{app_name}'...")
        idp_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/identities"
        try:
            r = self.session.get(idp_url, timeout=10)
            r.raise_for_status()
            idps = r.json()
            system_idp_id = None
            for idp in idps:
                if idp.get("system") is True:
                    system_idp_id = idp.get("id")
                    break
            if not system_idp_id:
                log("  ERROR: No system identity provider found.")
                return False
            app_url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/applications/{app_id}"
            payload = {"identityProviders": [{"identity": system_idp_id, "selectionRule": "", "priority": 0}]}
            r2 = self.session.put(app_url, json=payload, timeout=10)
            r2.raise_for_status()
            log(f"  ✓ Identity provider added.")
            return True
        except requests.exceptions.RequestException as e:
            log(f"  ERROR: Failed to add identity provider: {e}")
            return False

    def create_all_applications(self, app_configs: List[Dict[str, Any]]) -> bool:
        for app_config in app_configs:
            name = app_config.get("name")
            app_id = self.create_application(app_config)
            if not app_id:
                return False
            self.configure_application_settings(app_id, app_config)
            self.add_identity_provider(app_id, name)
            log(f"✓ Application '{name}' fully configured.")
        return True

    # -----------------------------------------------------------------------
    # User
    # -----------------------------------------------------------------------

    def create_user(self) -> bool:
        log(f"Creating user '{USER_USERNAME}'...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/users"
        payload = {
            "firstName": USER_FIRST_NAME,
            "lastName": USER_LAST_NAME,
            "email": USER_EMAIL,
            "username": USER_USERNAME,
            "password": USER_PASSWORD,
            "forceResetPassword": False,
            "preRegistration": False,
        }
        try:
            r = self.session.post(url, json=payload, timeout=10)
            if r.status_code == 400:
                if "already exists" in r.json().get("message", "").lower():
                    log(f"✓ User '{USER_USERNAME}' already exists.")
                    return True
            r.raise_for_status()
            log(f"✓ User '{USER_USERNAME}' created.")
            return True
        except requests.exceptions.RequestException as e:
            log(f"ERROR: Failed to create user: {e}")
            return False

    # -----------------------------------------------------------------------
    # MCP Servers
    # -----------------------------------------------------------------------

    def load_mcp_server_configs(self) -> List[Dict[str, Any]]:
        log(f"Loading MCP server configs from {MCP_SERVERS_CONFIG_DIR}...")
        configs = []
        files = glob.glob(os.path.join(MCP_SERVERS_CONFIG_DIR, "*.yaml")) + \
                glob.glob(os.path.join(MCP_SERVERS_CONFIG_DIR, "*.yml"))
        for f in files:
            try:
                with open(f) as fh:
                    cfg = yaml.safe_load(fh)
                    if cfg and cfg.get("name"):
                        configs.append(cfg)
                        log(f"  Loaded MCP: {cfg.get('name')}")
            except Exception as e:
                log(f"WARNING: Failed to load {f}: {e}")
        log(f"✓ {len(configs)} MCP server config(s) loaded.")
        return configs

    def create_mcp_server(self, mcp_config: Dict[str, Any]) -> Optional[str]:
        name = mcp_config.get("name")
        client_id = mcp_config.get("clientId")
        log(f"Creating MCP Server '{name}'...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/protected-resources"
        features = []
        for tool in mcp_config.get("tools", []):
            features.append({
                "key": tool.get("key"),
                "description": tool.get("description", ""),
                "type": tool.get("type", "MCP_TOOL"),
                "scopes": tool.get("scopes", []),
            })
        payload = {
            "name": name,
            "description": mcp_config.get("description", ""),
            "resourceIdentifiers": mcp_config.get("resourceIdentifiers", []),
            "clientId": client_id,
            "clientSecret": mcp_config.get("clientSecret"),
            "type": mcp_config.get("type", "MCP_SERVER"),
            "features": features,
        }
        try:
            r = self.session.post(url, json=payload, timeout=10)
            if r.status_code == 400:
                if "already exists" in str(r.json()).lower() or "clientId" in str(r.json()):
                    log(f"  MCP Server '{client_id}' already exists.")
                    return "exists"
            r.raise_for_status()
            resource_id = r.json().get("id")
            log(f"  ✓ MCP Server created: {resource_id}")
            return resource_id
        except requests.exceptions.RequestException as e:
            log(f"WARNING: Failed to create MCP server '{name}': {e} (non-fatal)")
            return None

    def create_all_mcp_servers(self, mcp_configs: List[Dict[str, Any]]) -> bool:
        for mcp_config in mcp_configs:
            self.create_mcp_server(mcp_config)
        return True

    # -----------------------------------------------------------------------
    # Main
    # -----------------------------------------------------------------------

    def run(self) -> bool:
        log("=" * 60)
        log("RNLI AM Initialisation")
        log("=" * 60)

        if not self.wait_for_am():
            return False

        if not self.authenticate():
            return False

        if not self.create_domain():
            return False

        if not self.enable_domain():
            return False

        self.configure_dcr()

        app_configs = self.load_app_configs()
        if not self.create_all_applications(app_configs):
            return False

        if not self.create_user():
            return False

        mcp_configs = self.load_mcp_server_configs()
        self.create_all_mcp_servers(mcp_configs)

        log("=" * 60)
        log("✓ AM Initialisation complete.")
        log("=" * 60)
        return True


if __name__ == "__main__":
    initializer = AMInitializer()
    if not initializer.run():
        sys.exit(1)
