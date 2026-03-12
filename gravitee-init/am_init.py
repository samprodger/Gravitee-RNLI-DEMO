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

# Gold tier user (JWT / AM OAuth)
USER_FIRST_NAME = "Joe"
USER_LAST_NAME = "Doe"
USER_EMAIL = "joe.doe@gravitee.io"
USER_USERNAME = "joe.doe@gravitee.io"
USER_PASSWORD = "HelloWorld@123"

# Silver tier user (API key holder — created in AM for demo completeness)
SILVER_USER_FIRST_NAME = "Silver"
SILVER_USER_LAST_NAME = "Subscriber"
SILVER_USER_EMAIL = "silver.user@rnli.org"
SILVER_USER_USERNAME = "silver.user@rnli.org"
SILVER_USER_PASSWORD = "HelloWorld@123"

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

    def create_silver_user(self) -> bool:
        log(f"Creating Silver tier user '{SILVER_USER_USERNAME}'...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/users"
        payload = {
            "firstName": SILVER_USER_FIRST_NAME,
            "lastName": SILVER_USER_LAST_NAME,
            "email": SILVER_USER_EMAIL,
            "username": SILVER_USER_USERNAME,
            "password": SILVER_USER_PASSWORD,
            "forceResetPassword": False,
            "preRegistration": False,
        }
        try:
            r = self.session.post(url, json=payload, timeout=10)
            if r.status_code == 400:
                if "already exists" in r.json().get("message", "").lower():
                    log(f"✓ Silver user '{SILVER_USER_USERNAME}' already exists.")
                    return True
            r.raise_for_status()
            log(f"✓ Silver user '{SILVER_USER_USERNAME}' created.")
            return True
        except requests.exceptions.RequestException as e:
            log(f"WARNING: Failed to create silver user (non-fatal): {e}")
            return True  # non-fatal

    # -----------------------------------------------------------------------
    # Custom Login Form (social buttons)
    # -----------------------------------------------------------------------

    # Thymeleaf login form with RNLI branding and dummy social login buttons.
    # Social buttons use onclick="return false;" — they are purely decorative
    # for the FGA demo and do not initiate any OAuth flow.
    LOGIN_FORM_CONTENT = """\
<!DOCTYPE html>
<html lang="en" xmlns:th="http://www.thymeleaf.org">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sign In – RNLI Data Portal</title>
  <link rel="stylesheet" th:href="@{/assets/ui-components.css}" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0f2f5;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .card {
      background: #ffffff;
      border-radius: 14px;
      box-shadow: 0 4px 32px rgba(0,0,0,0.12);
      padding: 44px 40px 40px;
      width: 100%;
      max-width: 420px;
    }
    .brand { display: flex; align-items: center; gap: 13px; margin-bottom: 4px; }
    .brand-icon {
      width: 44px; height: 44px;
      background: #E8392A;
      border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0;
    }
    .brand-name { font-size: 19px; font-weight: 700; color: #111827; }
    .brand-name span { color: #E8392A; }
    .brand { margin-bottom: 28px; }
    .alert-error {
      background: #fef2f2; border: 1px solid #fca5a5; color: #dc2626;
      padding: 11px 14px; border-radius: 8px; font-size: 14px; margin-bottom: 20px;
    }
    .field { margin-bottom: 18px; }
    .field label { display: block; font-size: 13px; font-weight: 500; color: #374151; margin-bottom: 6px; }
    .field input {
      width: 100%; padding: 10px 14px;
      border: 1.5px solid #d1d5db; border-radius: 8px;
      font-size: 14px; color: #111827; outline: none;
      transition: border-color .2s, box-shadow .2s;
    }
    .field input::placeholder { color: #9ca3af; }
    .field input:focus { border-color: #E8392A; box-shadow: 0 0 0 3px rgba(232,57,42,0.12); }
    .btn-signin {
      width: 100%; padding: 12px;
      background: #E8392A; color: #fff; border: none;
      border-radius: 8px; font-size: 15px; font-weight: 600;
      cursor: pointer; margin-top: 6px;
      transition: background .2s;
    }
    .btn-signin:hover { background: #c9301f; }
    .divider {
      display: flex; align-items: center; gap: 12px;
      margin: 26px 0 18px; color: #9ca3af; font-size: 13px;
    }
    .divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: #e5e7eb; }
    .social-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .social-btn {
      display: flex; align-items: center; justify-content: center; gap: 8px;
      padding: 10px 12px;
      border: 1.5px solid #d1d5db; border-radius: 8px;
      font-size: 13px; font-weight: 500; color: #374151;
      background: #fff; cursor: pointer;
      transition: background .15s, border-color .15s;
    }
    .social-btn:hover { background: #f9fafb; border-color: #9ca3af; }
    .demo-note {
      text-align: center; font-size: 11px; color: #9ca3af;
      margin-top: 18px;
    }
  </style>
</head>
<body>
<div class="card">

  <!-- Brand header -->
  <div class="brand">
    <div class="brand-icon">
      <svg width="24" height="18" viewBox="0 0 24 18" fill="none" xmlns="http://www.w3.org/2000/svg">
        <line x1="9" y1="1" x2="9" y2="7" stroke="white" stroke-width="1.5" stroke-linecap="round"/>
        <path d="M9 1.5 L14 4 L9 6.5 Z" fill="white" opacity="0.9"/>
        <rect x="4.5" y="6.5" width="11" height="4.5" rx="1.5" fill="white" opacity="0.9"/>
        <path d="M1 11H4.5L19.5 11Q21.5 11 22.5 12.3L20.5 15Q17 16.5 4 16.5Q1.5 16.5 1 15L1 12.5Q1 11 1 11Z" fill="white" opacity="0.85"/>
      </svg>
    </div>
    <span class="brand-name">RNLI <span>Data Portal</span></span>
  </div>

  <!-- Error -->
  <div th:if="${param.error != null}" class="alert-error">
    Incorrect username or password. Please try again.
  </div>

  <!-- Login form -->
  <form th:action="@{login}" method="post" autocomplete="off">
    <input type="hidden" th:if="${_csrf != null}" th:name="${_csrf.parameterName}" th:value="${_csrf.token}" />
    <div class="field">
      <label for="username">Email or username</label>
      <input id="username" type="text" name="username" placeholder="joe.doe@gravitee.io" required autofocus />
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input id="password" type="password" name="password" placeholder="&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;&#x2022;" required />
    </div>
    <button type="submit" class="btn-signin">Sign in</button>
  </form>

  <!-- Social login (demo only — buttons are decorative) -->
  <div class="divider">or continue with</div>
  <div class="social-grid">

    <!-- Google -->
    <button type="button" class="social-btn" onclick="return false;" title="Social login — demo only">
      <svg width="16" height="16" viewBox="0 0 24 24">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
      Google
    </button>

    <!-- Apple -->
    <button type="button" class="social-btn" onclick="return false;" title="Social login — demo only">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
      </svg>
      Apple
    </button>

    <!-- Microsoft -->
    <button type="button" class="social-btn" onclick="return false;" title="Social login — demo only">
      <svg width="16" height="16" viewBox="0 0 24 24">
        <rect x="1"  y="1"  width="10" height="10" fill="#F25022"/>
        <rect x="13" y="1"  width="10" height="10" fill="#7FBA00"/>
        <rect x="1"  y="13" width="10" height="10" fill="#00A4EF"/>
        <rect x="13" y="13" width="10" height="10" fill="#FFB900"/>
      </svg>
      Microsoft
    </button>

    <!-- GitHub -->
    <button type="button" class="social-btn" onclick="return false;" title="Social login — demo only">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
      </svg>
      GitHub
    </button>

  </div>
  <p class="demo-note">Social login available in production environment</p>

</div>
</body>
</html>
"""

    def configure_login_form(self) -> bool:
        """
        Override the AM domain login form with a custom RNLI-branded template
        that includes dummy social login buttons (Google, Apple, Microsoft, GitHub).
        """
        log("Configuring custom login form with social login buttons...")
        url = f"{AM_BASE_URL}/management/organizations/{ORGANIZATION}/environments/{ENVIRONMENT}/domains/{self.domain_id}/forms"

        # Try to find an existing form and update via PUT, else create via POST
        # Note: PUT body only accepts {content, enabled} — template/id fields are rejected
        # AM returns a single form object (dict) when filtered by template, not a list
        existing_id = None
        try:
            r = self.session.get(url, params={"template": "LOGIN"}, timeout=10)
            if r.ok:
                forms = r.json()
                if isinstance(forms, dict) and forms.get("id"):
                    existing_id = forms.get("id")
                elif isinstance(forms, list) and forms:
                    existing_id = forms[0].get("id")
        except requests.exceptions.RequestException:
            pass

        put_payload  = {"content": self.LOGIN_FORM_CONTENT, "enabled": True}
        post_payload = {"template": "LOGIN", "content": self.LOGIN_FORM_CONTENT, "enabled": True}
        try:
            if existing_id:
                r = self.session.put(f"{url}/{existing_id}", json=put_payload, timeout=15)
            else:
                r = self.session.post(url, json=post_payload, timeout=15)
            if r.ok:
                action = "updated" if existing_id else "configured"
                log(f"✓ Custom login form {action} with social buttons.")
                return True
            log(f"WARNING: Could not configure login form ({r.status_code}): {r.text[:120]}")
            return True  # non-fatal
        except requests.exceptions.RequestException as e:
            log(f"WARNING: Login form config failed (non-fatal): {e}")
            return True  # non-fatal

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

        self.create_silver_user()
        self.configure_login_form()

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
