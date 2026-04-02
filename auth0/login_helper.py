"""
Auth0 OAuth Login Helper — Scoped Re-Login for Token Vault
==========================================================

Initiates an Auth0 authorization flow with the correct scopes so that
Token Vault can exchange federated tokens for Google Calendar and Slack.

Usage:
    # Re-login with Google Calendar scope:
    python auth0/login_helper.py google

    # Login with Slack scope:
    python auth0/login_helper.py slack

    # Login with both:
    python auth0/login_helper.py all

What this does:
    1. Starts a temporary HTTP server on localhost:3456
    2. Opens the Auth0 /authorize URL with the right connection + scopes
    3. Handles the callback, exchanges code for tokens
    4. Saves the refresh token and access token for Token Vault use
    5. Verifies the federated token exchange works

Auth0 features used:
    - Authorization Code Flow with PKCE
    - Token Vault (Federated Connection Access Token exchange)
    - Connected Accounts (offline_access for refresh tokens)
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from typing import Optional

import httpx

# Ensure auth0 package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────

AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://etms-api.example.com")

CALLBACK_PORT = 3456
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"

# Token file paths
TOKEN_DIR = os.path.dirname(os.path.abspath(__file__))
REFRESH_TOKEN_PATH = os.path.join(TOKEN_DIR, ".caregiver_refresh_token")
ACCESS_TOKEN_PATH = os.path.join(TOKEN_DIR, ".caregiver_token")

# ── Connection Configs ──────────────────────────────────────────

CONNECTIONS = {
    "google": {
        "connection": "google-oauth2",
        "connection_scope": "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events",
        "display_name": "Google Calendar",
    },
    "slack": {
        "connection": "sign-in-with-slack",
        "connection_scope": "chat:write channels:read",
        "display_name": "Slack",
    },
}


# ── PKCE helpers ────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── OAuth Callback Server ──────────────────────────────────────

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that captures the authorization code from Auth0."""

    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self._respond(
                200,
                "<html><body style='font-family:system-ui;text-align:center;padding-top:80px'>"
                "<h1 style='color:#16a34a'>✅ Login Successful!</h1>"
                "<p>You can close this tab and return to the terminal.</p>"
                "<p style='color:#6b7280;font-size:14px'>Auth0 Token Vault tokens saved.</p>"
                "</body></html>",
            )
        elif "error" in params:
            _CallbackHandler.error = params.get("error_description", params["error"])[0]
            self._respond(
                400,
                f"<html><body style='font-family:system-ui;text-align:center;padding-top:80px'>"
                f"<h1 style='color:#dc2626'>❌ Login Failed</h1>"
                f"<p>{_CallbackHandler.error}</p>"
                f"</body></html>",
            )
        else:
            self._respond(404, "Not found")

    def _respond(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        """Silence HTTP request logs."""
        pass


def _wait_for_callback(timeout: int = 120) -> Optional[str]:
    """Start callback server and wait for the authorization code."""
    server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
    server.timeout = timeout

    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None

    logger.info(f"  Waiting for callback on http://localhost:{CALLBACK_PORT}/callback ...")

    # Handle one request (the callback)
    start = time.time()
    while time.time() - start < timeout:
        server.handle_request()
        if _CallbackHandler.auth_code or _CallbackHandler.error:
            break

    server.server_close()

    if _CallbackHandler.error:
        logger.error(f"  ❌ Auth0 error: {_CallbackHandler.error}")
        return None

    return _CallbackHandler.auth_code


# ── Token Exchange ──────────────────────────────────────────────

def _exchange_code_for_tokens(
    code: str, code_verifier: str
) -> Optional[dict]:
    """Exchange authorization code for access + refresh tokens."""
    try:
        with httpx.Client() as client:
            resp = client.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": AUTH0_CLIENT_ID,
                    "client_secret": AUTH0_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": CALLBACK_URL,
                    "code_verifier": code_verifier,
                },
            )
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"  ❌ Token exchange failed: {resp.status_code} {resp.text}")
            return None
    except httpx.HTTPError as e:
        logger.error(f"  ❌ Token exchange HTTP error: {e}")
        return None


def _save_tokens(tokens: dict) -> None:
    """Save access token and refresh token to disk."""
    if tokens.get("access_token"):
        with open(ACCESS_TOKEN_PATH, "w") as f:
            f.write(tokens["access_token"])
        logger.info(f"  ✅ Access token saved to {os.path.basename(ACCESS_TOKEN_PATH)}")

    if tokens.get("refresh_token"):
        with open(REFRESH_TOKEN_PATH, "w") as f:
            f.write(tokens["refresh_token"])
        logger.info(f"  ✅ Refresh token saved to {os.path.basename(REFRESH_TOKEN_PATH)}")
    else:
        logger.warning("  ⚠️  No refresh token received — offline_access may not be enabled")

    if tokens.get("id_token"):
        # Decode ID token to show user info
        try:
            parts = tokens["id_token"].split(".")
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            logger.info(f"  👤 Logged in as: {claims.get('name', claims.get('email', 'unknown'))}")
            logger.info(f"  🆔 User ID: {claims.get('sub', 'unknown')}")
        except Exception:
            pass


# ── Token Vault Verification (3-Strategy) ──────────────────────
#
# Auth0 Token Vault supports multiple exchange strategies.  The login
# helper mirrors the production token_vault.py fallback chain:
#   1. Refresh-token exchange  (Token Vault native — may require paid plan)
#   2. Access-token exchange   (Token Vault native — may require paid plan)
#   3. Management API identity lookup  (works on all plans)
#
# If *any* strategy yields a usable upstream token, we report success.
# ---------------------------------------------------------------------------

AUTH0_M2M_CLIENT_ID = os.getenv("AUTH0_M2M_CLIENT_ID", "")
AUTH0_M2M_CLIENT_SECRET = os.getenv("AUTH0_M2M_CLIENT_SECRET", "")
AUTH0_CUSTOM_API_CLIENT_ID = os.getenv("AUTH0_CUSTOM_API_CLIENT_ID", "")
AUTH0_CUSTOM_API_CLIENT_SECRET = os.getenv("AUTH0_CUSTOM_API_CLIENT_SECRET", "")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")


def _get_m2m_token() -> Optional[str]:
    """Obtain an M2M access token for the Auth0 Management API."""
    if not AUTH0_M2M_CLIENT_ID or not AUTH0_M2M_CLIENT_SECRET:
        return None
    try:
        with httpx.Client() as client:
            resp = client.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": AUTH0_M2M_CLIENT_ID,
                    "client_secret": AUTH0_M2M_CLIENT_SECRET,
                    "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
                },
            )
            if resp.status_code == 200:
                return resp.json().get("access_token")
    except httpx.HTTPError:
        pass
    return None


def _mgmt_api_get_federated_token(
    user_id: str, connection: str
) -> Optional[str]:
    """Retrieve upstream provider token from user's Auth0 identity
    via Management API (Strategy 3 — works on all Auth0 plans)."""
    mgmt_token = _get_m2m_token()
    if not mgmt_token:
        return None
    try:
        with httpx.Client() as client:
            resp = client.get(
                f"https://{AUTH0_DOMAIN}/api/v2/users/{user_id}",
                headers={"Authorization": f"Bearer {mgmt_token}"},
                params={"include_fields": "true", "fields": "identities"},
            )
            if resp.status_code != 200:
                return None
            for identity in resp.json().get("identities", []):
                if identity.get("connection") == connection or identity.get("provider") == connection:
                    return identity.get("access_token")
    except httpx.HTTPError:
        pass
    return None


def _verify_token_vault_exchange(
    connection: str, scopes: str, display_name: str,
    tokens: Optional[dict] = None,
) -> tuple[bool, Optional[str], str]:
    """Verify that we can obtain a federated token for *connection*.

    Tries three strategies (matching production token_vault.py):
      1. Refresh-token exchange  (Token Vault native)
      2. Access-token exchange   (Token Vault native)
      3. Management API identity lookup

    Returns:
        (success, upstream_token, strategy_used)
    """
    refresh_token = None
    access_token = tokens.get("access_token") if tokens else None

    try:
        with open(REFRESH_TOKEN_PATH) as f:
            refresh_token = f.read().strip()
    except FileNotFoundError:
        pass

    logger.info(f"\n  Verifying Token Vault for {display_name}...")

    # --- Strategy 1: Refresh-token exchange ---
    if refresh_token:
        logger.info(f"  → Strategy 1: Refresh-token exchange...")
        try:
            with httpx.Client() as client:
                resp = client.post(
                    f"https://{AUTH0_DOMAIN}/oauth/token",
                    json={
                        "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
                        "client_id": AUTH0_CLIENT_ID,
                        "client_secret": AUTH0_CLIENT_SECRET,
                        "subject_token": refresh_token,
                        "subject_token_type": "urn:ietf:params:oauth:token-type:refresh_token",
                        "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
                        "connection": connection,
                        "scope": scopes,
                    },
                )
                if resp.status_code == 200:
                    token = resp.json().get("access_token", "")
                    logger.info(f"  ✅ Strategy 1 succeeded — Token Vault refresh-token exchange")
                    return True, token, "refresh-token exchange"
                logger.info(f"     Strategy 1 returned {resp.status_code} — trying next...")
        except httpx.HTTPError:
            logger.info(f"     Strategy 1 network error — trying next...")

    # --- Strategy 2: Access-token exchange ---
    if access_token:
        tv_client = AUTH0_CUSTOM_API_CLIENT_ID or AUTH0_CLIENT_ID
        tv_secret = AUTH0_CUSTOM_API_CLIENT_SECRET or AUTH0_CLIENT_SECRET
        logger.info(f"  → Strategy 2: Access-token exchange...")
        try:
            with httpx.Client() as client:
                resp = client.post(
                    f"https://{AUTH0_DOMAIN}/oauth/token",
                    json={
                        "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
                        "client_id": tv_client,
                        "client_secret": tv_secret,
                        "subject_token": access_token,
                        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                        "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
                        "connection": connection,
                        "scope": scopes,
                    },
                )
                if resp.status_code == 200:
                    token = resp.json().get("access_token", "")
                    logger.info(f"  ✅ Strategy 2 succeeded — Token Vault access-token exchange")
                    return True, token, "access-token exchange"
                logger.info(f"     Strategy 2 returned {resp.status_code} — trying next...")
        except httpx.HTTPError:
            logger.info(f"     Strategy 2 network error — trying next...")

    # --- Strategy 3: Management API fallback ---
    # Extract user_id from id_token or access_token
    user_id = None
    for t in [tokens.get("id_token"), access_token, refresh_token]:
        if t and "." in t:
            try:
                parts = t.split(".")
                payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                if "sub" in claims:
                    user_id = claims["sub"]
                    break
            except Exception:
                continue

    if user_id:
        logger.info(f"  → Strategy 3: Management API lookup for {user_id}...")
        upstream_token = _mgmt_api_get_federated_token(user_id, connection)
        if upstream_token:
            logger.info(f"  ✅ Strategy 3 succeeded — Management API identity token")
            return True, upstream_token, "Management API"
        logger.info(f"     Strategy 3: no upstream token found in identity")
    else:
        logger.info(f"     Strategy 3: could not extract user_id from tokens")

    logger.warning(f"  ❌ All 3 Token Vault strategies failed for {display_name}")
    return False, None, "none"


def _verify_google_calendar(access_token: str) -> bool:
    """Quick test: list calendar events using the federated token."""
    try:
        with httpx.Client() as client:
            resp = client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"maxResults": "1", "timeMin": "2026-01-01T00:00:00Z"},
            )
            if resp.status_code == 200:
                events = resp.json().get("items", [])
                logger.info(f"  ✅ Google Calendar API works! ({len(events)} events in response)")
                return True
            else:
                logger.warning(f"  ⚠️  Calendar API returned {resp.status_code}: {resp.text[:200]}")
                return False
    except httpx.HTTPError as e:
        logger.error(f"  ❌ Calendar API HTTP error: {e}")
        return False


def _verify_slack(access_token: str) -> bool:
    """Quick test: verify Slack token by calling auth.test."""
    try:
        with httpx.Client() as client:
            resp = client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            data = resp.json()
            if data.get("ok"):
                logger.info(f"  ✅ Slack API works! Team: {data.get('team', 'unknown')}, User: {data.get('user', 'unknown')}")
                return True
            else:
                logger.warning(f"  ⚠️  Slack auth.test failed: {data.get('error', 'unknown')}")
                return False
    except httpx.HTTPError as e:
        logger.error(f"  ❌ Slack API HTTP error: {e}")
        return False


# ── Main Login Flow ─────────────────────────────────────────────

def login_with_connection(connection_key: str) -> bool:
    """Run the full OAuth login flow for a specific connection.

    Args:
        connection_key: One of 'google', 'slack'

    Returns:
        True if login and Token Vault verification succeeded.
    """
    conn_config = CONNECTIONS[connection_key]
    connection = conn_config["connection"]
    connection_scope = conn_config["connection_scope"]
    display_name = conn_config["display_name"]

    print()
    print(f"  {'=' * 56}")
    print(f"  Auth0 Token Vault — {display_name} Login")
    print(f"  {'=' * 56}")
    print()

    # Generate PKCE
    code_verifier, code_challenge = _generate_pkce()

    # Build authorize URL
    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
        "scope": "openid profile email offline_access",
        "audience": AUTH0_AUDIENCE,
        "connection": connection,
        "connection_scope": connection_scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",  # Force consent to get fresh refresh token
    }

    authorize_url = f"https://{AUTH0_DOMAIN}/authorize?{urllib.parse.urlencode(params)}"

    logger.info(f"  Opening browser for {display_name} login...")
    logger.info(f"  Connection: {connection}")
    logger.info(f"  Scopes: {connection_scope}")
    logger.info(f"  Grant: offline_access (for Token Vault refresh token)")
    print()

    # Open browser
    webbrowser.open(authorize_url)

    # Wait for callback
    code = _wait_for_callback(timeout=120)
    if not code:
        logger.error(f"  ❌ No authorization code received. Login cancelled or timed out.")
        return False

    logger.info(f"  Authorization code received!")
    print()

    # Exchange code for tokens
    logger.info(f"  Exchanging code for tokens...")
    tokens = _exchange_code_for_tokens(code, code_verifier)
    if not tokens:
        return False

    # Save tokens
    _save_tokens(tokens)

    # Verify Token Vault exchange (3-strategy fallback)
    success, upstream_token, strategy = _verify_token_vault_exchange(
        connection, connection_scope, display_name, tokens=tokens
    )

    # Verify actual API access with the upstream token
    if success and upstream_token:
        if connection_key == "google":
            _verify_google_calendar(upstream_token)
        elif connection_key == "slack":
            _verify_slack(upstream_token)

    # For Slack: also check bot token fallback
    if not success and connection_key == "slack" and SLACK_BOT_TOKEN:
        logger.info(f"\n  → Slack Bot Token fallback available (Auth0-managed)")
        bot_ok = _verify_slack(SLACK_BOT_TOKEN)
        if bot_ok:
            success = True
            strategy = "Bot Token fallback"

    print()
    if success:
        logger.info(f"  ★ {display_name} Token Vault integration READY (via {strategy})")
    else:
        logger.warning(f"  ⚠️  {display_name}: all Token Vault strategies failed.")
        logger.info(f"     Check setup instructions above or enable M2M credentials.")

    return success


# ── Setup Instructions ──────────────────────────────────────────

def print_google_setup() -> None:
    """Print Auth0 dashboard setup instructions for Google Calendar."""
    print("""
  ┌────────────────────────────────────────────────────────────┐
  │  Google Calendar — Auth0 Setup Checklist                   │
  ├────────────────────────────────────────────────────────────┤
  │                                                            │
  │  1. Auth0 Dashboard > Authentication > Social              │
  │     → Google / Gmail connection                            │
  │     → Ensure "Calendar API" scope is enabled               │
  │     → Copy your Google Client ID & Secret from             │
  │       console.cloud.google.com > APIs & Services > Creds   │
  │                                                            │
  │  2. Google Cloud Console                                   │
  │     → Enable "Google Calendar API"                         │
  │     → OAuth consent screen: add calendar scopes            │
  │     → Add http://localhost:3456/callback to redirect URIs  │
  │                                                            │
  │  3. Auth0 Dashboard > Applications > ETMS Agent            │
  │     → Allowed Callback URLs: add http://localhost:3456/callback │
  │     → Grant Types: ensure "Authorization Code" enabled     │
  │     → Connections: Google enabled                          │
  │                                                            │
  │  4. Auth0 Dashboard > Applications > ETMS Agent > Settings │
  │     → Refresh Token Rotation: enable                       │
  │     → Absolute Lifetime: 2592000 (30 days)                 │
  │                                                            │
  └────────────────────────────────────────────────────────────┘
""")


def print_slack_setup() -> None:
    """Print Auth0 dashboard + Slack app setup instructions."""
    print("""
  ┌────────────────────────────────────────────────────────────┐
  │  Slack — Auth0 Setup Checklist                             │
  ├────────────────────────────────────────────────────────────┤
  │                                                            │
  │  STEP 1: Create Slack App                                  │
  │  ─────────────────────                                     │
  │  → Go to https://api.slack.com/apps > Create New App       │
  │  → Choose "From scratch"                                   │
  │  → Name: "ETMS Elderly Safety" (or similar)                │
  │  → Choose your Slack workspace                             │
  │                                                            │
  │  STEP 2: Configure OAuth & Permissions                     │
  │  ────────────────────────────────                          │
  │  → OAuth & Permissions > Redirect URLs:                    │
  │    Add: https://dev-pavankumar.us.auth0.com/login/callback │
  │  → Bot Token Scopes: chat:write, channels:read             │
  │  → User Token Scopes: chat:write, channels:read            │
  │                                                            │
  │  STEP 3: Create Auth0 Slack Connection                     │
  │  ─────────────────────────────────                         │
  │  → Auth0 Dashboard > Authentication > Social               │
  │  → Create Connection > Slack                               │
  │  → Paste Client ID & Client Secret from Slack app          │
  │  → Enable for ETMS Agent application                       │
  │                                                            │
  │  STEP 4: Add callback URL                                  │
  │  ── Auth0 Dashboard > Applications > ETMS Agent            │
  │     → Allowed Callback URLs: http://localhost:3456/callback │
  │                                                            │
  └────────────────────────────────────────────────────────────┘
""")


# ── CLI Entry Point ─────────────────────────────────────────────

def main() -> None:
    """CLI entry point for the login helper."""
    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║  ETMS × Auth0 — Token Vault Login Helper            ║")
    print("  ║  Re-login with scoped tokens for Calendar & Slack   ║")
    print("  ╚══════════════════════════════════════════════════════╝")

    if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID:
        logger.error("\n  ❌ AUTH0_DOMAIN and AUTH0_CLIENT_ID must be set in .env")
        sys.exit(1)

    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if not args or args[0] in ("-h", "--help", "help"):
        print("""
  Usage:
    python auth0/login_helper.py google    # Re-login with Google Calendar scope
    python auth0/login_helper.py slack     # Login with Slack scope
    python auth0/login_helper.py all       # Login with both (sequentially)
    python auth0/login_helper.py setup     # Show setup instructions

  Prerequisites:
    - Auth0 application has http://localhost:3456/callback in Allowed Callback URLs
    - Refresh Token Rotation enabled in Auth0 app settings
    - Google: Calendar API enabled in Google Cloud Console
    - Slack: Slack app created with OAuth scopes
        """)
        sys.exit(0)

    target = args[0].lower()

    if target == "setup":
        print_google_setup()
        print_slack_setup()
        sys.exit(0)

    if target in ("google", "all"):
        print_google_setup()
        input("  Press ENTER when Auth0 setup is ready, or Ctrl+C to cancel...")
        success = login_with_connection("google")
        if not success:
            logger.error("  Google login failed. Check setup instructions above.")

    if target in ("slack", "all"):
        print_slack_setup()
        input("  Press ENTER when Slack + Auth0 setup is ready, or Ctrl+C to cancel...")
        success = login_with_connection("slack")
        if not success:
            logger.error("  Slack login failed. Check setup instructions above.")

    if target not in ("google", "slack", "all"):
        logger.error(f"  Unknown target: {target}. Use 'google', 'slack', or 'all'.")
        sys.exit(1)

    # Final summary
    print()
    print("  " + "=" * 56)
    print("  After login, run the full test suite to verify:")
    print("    python3 auth0/test_full_flow.py")
    print("  " + "=" * 56)
    print()


if __name__ == "__main__":
    main()
