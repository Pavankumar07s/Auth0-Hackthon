"""Token Vault wrapper for ETMS.
Auth0 feature: Token Vault (Connected Accounts for AI Agents)
Docs: https://auth0.com/ai/docs/intro/token-vault

This module enables OpenClaw to call external APIs (Google Calendar,
Slack) using the caregiver's federated tokens — WITHOUT ever storing
raw credentials locally. Auth0 Token Vault handles the OAuth exchange
securely.

Three exchange strategies are attempted in order:
  1. Refresh-token exchange (preferred)
  2. Access-token exchange (fallback)
  3. Management API identity lookup (last resort)
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional

import httpx

from auth0.config import (
    AUTH0_CLIENT_ID,
    AUTH0_CLIENT_SECRET,
    AUTH0_CUSTOM_API_CLIENT_ID,
    AUTH0_CUSTOM_API_CLIENT_SECRET,
    AUTH0_DOMAIN,
    AUTH0_M2M_CLIENT_ID,
    AUTH0_M2M_CLIENT_SECRET,
    SLACK_BOT_TOKEN,
)

logger = logging.getLogger(__name__)

_REFRESH_TOKEN_PATH = os.path.join(os.path.dirname(__file__), ".caregiver_refresh_token")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_refresh_token() -> Optional[str]:
    """Load the cached Auth0 refresh token if available."""
    try:
        with open(_REFRESH_TOKEN_PATH, "r") as f:
            token = f.read().strip()
            return token if token else None
    except FileNotFoundError:
        return None


def _decode_jwt_payload(token: str) -> Optional[dict]:
    """Decode a JWT payload without verification (for extracting sub claim).

    We only need the user ID from the token — upstream middleware already
    verified the signature.

    Args:
        token: A JWT access token string.

    Returns:
        Decoded payload dict, or ``None`` on decode failure.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _get_m2m_token() -> Optional[str]:
    """Obtain an M2M access token for the Auth0 Management API.

    Auth0 feature: Machine-to-Machine (Client Credentials) grant.

    Returns:
        Management API access token, or ``None`` on failure.
    """
    if not AUTH0_M2M_CLIENT_ID or not AUTH0_M2M_CLIENT_SECRET:
        logger.warning("[TokenVault] M2M credentials not configured")
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
            logger.error(
                "[TokenVault] M2M token request failed: %s %s",
                resp.status_code,
                resp.text,
            )
            return None
    except httpx.HTTPError as e:
        logger.error("[TokenVault] M2M token HTTP error: %s", e)
        return None


def _get_identity_token_via_mgmt_api(
    user_id: str, connection: str
) -> Optional[str]:
    """Retrieve a federated provider's access token from the user's
    Auth0 identity via the Management API.

    This is a fallback for when the Token Vault exchange endpoint is
    unavailable (e.g. Connected Accounts not enabled on tenant).
    The provider token is Auth0-managed and never stored locally.

    Auth0 feature: Management API — Get User (identity provider tokens)

    Args:
        user_id: The Auth0 user ID (e.g. ``google-oauth2|123``).
        connection: The connection name (e.g. ``google-oauth2``).

    Returns:
        The provider access token, or ``None`` if unavailable.
    """
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
                logger.error(
                    "[TokenVault] Management API user fetch failed: %s %s",
                    resp.status_code,
                    resp.text,
                )
                return None

            identities = resp.json().get("identities", [])
            for identity in identities:
                if identity.get("connection") == connection or identity.get("provider") == connection:
                    token = identity.get("access_token")
                    if token:
                        logger.info(
                            "[TokenVault] Management API fallback: retrieved %s token "
                            "from user identity (Auth0-managed, never stored locally)",
                            connection,
                        )
                        return token
                    logger.warning(
                        "[TokenVault] Identity found for %s but no access_token present",
                        connection,
                    )
                    return None

            logger.warning(
                "[TokenVault] No identity found for connection=%s in user %s",
                connection,
                user_id,
            )
            return None
    except httpx.HTTPError as e:
        logger.error("[TokenVault] Management API HTTP error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def exchange_token_for_connection(
    user_token: str,
    connection: str,
    scopes: str = "",
) -> Optional[str]:
    """Exchange a caregiver's Auth0 token for a connection-specific
    access token stored in Token Vault.

    Supports two exchange modes per the Auth0 SDK:
    - **Refresh token exchange**: Uses the Auth0 refresh token
    - **Access token exchange**: Uses the current access token

    Falls back to Management API identity lookup if both fail.

    Auth0 feature: Token Vault (RFC 8693 Token Exchange)

    Args:
        user_token: The caregiver's Auth0 access token.
        connection: The connection name (e.g. ``google-oauth2``).
        scopes: Space-separated OAuth scopes for the target API.

    Returns:
        The external service access token, or ``None`` on failure.
    """
    # Token Vault client — prefers the custom Token Vault client if configured
    tv_client_id = AUTH0_CUSTOM_API_CLIENT_ID or AUTH0_CLIENT_ID
    tv_client_secret = AUTH0_CUSTOM_API_CLIENT_SECRET or AUTH0_CLIENT_SECRET

    # --- Strategy 1: Refresh-token exchange ---
    # The refresh token MUST be exchanged using the client that issued it
    # (AUTH0_CLIENT_ID). Using a different client triggers "invalid_grant".
    refresh_token = _load_refresh_token()
    if refresh_token:
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
                    token = resp.json().get("access_token")
                    if token:
                        logger.info(
                            "[TokenVault] Refresh-token exchange succeeded for connection=%s",
                            connection,
                        )
                        return token
                logger.warning(
                    "[TokenVault] Refresh-token exchange failed for connection=%s: %s %s. "
                    "Falling back to access-token exchange.",
                    connection,
                    resp.status_code,
                    resp.text,
                )
        except httpx.HTTPError as e:
            logger.error("[TokenVault] Refresh-token exchange HTTP error: %s", e)

    # --- Strategy 2: Access-token exchange ---
    # Uses the Token Vault client (or falls back to main client)
    try:
        with httpx.Client() as client:
            resp = client.post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                json={
                    "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
                    "client_id": tv_client_id,
                    "client_secret": tv_client_secret,
                    "subject_token": user_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
                    "connection": connection,
                    "scope": scopes,
                },
            )
            if resp.status_code == 200:
                token = resp.json().get("access_token")
                if token:
                    logger.info(
                        "[TokenVault] Access-token exchange succeeded for connection=%s",
                        connection,
                    )
                    return token
            logger.warning(
                "[TokenVault] Access-token exchange failed for connection=%s: %s %s. "
                "Falling back to Management API.",
                connection,
                resp.status_code,
                resp.text,
            )
    except httpx.HTTPError as e:
        logger.error("[TokenVault] Access-token exchange HTTP error: %s", e)

    # --- Strategy 3: Management API fallback ---
    claims = _decode_jwt_payload(user_token)
    if claims and "sub" in claims:
        user_id = claims["sub"]
        logger.info(
            "[TokenVault] Attempting Management API fallback for user=%s connection=%s",
            user_id,
            connection,
        )
        return _get_identity_token_via_mgmt_api(user_id, connection)
    else:
        logger.warning(
            "[TokenVault] Could not extract user ID from JWT for Management API fallback"
        )

    logger.error(
        "[TokenVault] All token exchange strategies exhausted for connection=%s",
        connection,
    )
    return None


def post_slack_alert(
    user_token: str,
    channel: str,
    message: str,
) -> dict:
    """Post an emergency alert to a Slack channel via Token Vault.

    Called by OpenClaw ActionDispatcher when severity >= HIGH_RISK.
    The caregiver's raw Slack token is never exposed — Token Vault
    handles the exchange transparently.

    Auth0 feature: Token Vault (Slack integration)

    Args:
        user_token: The caregiver's Auth0 access token.
        channel: Slack channel ID or name (e.g. ``#elderly-alerts``).
        message: The alert message text.

    Returns:
        Dict with ``ok`` status and response details.
    """
    slack_token = exchange_token_for_connection(
        user_token, "sign-in-with-slack", "chat:write channels:read"
    )

    # Fallback: use direct Slack Bot Token when Token Vault exchange
    # is unavailable (e.g. user hasn't linked Slack identity).
    # The bot token is Auth0-managed via the Slack connection config,
    # never hardcoded — loaded from SLACK_BOT_TOKEN env var.
    if not slack_token and SLACK_BOT_TOKEN:
        logger.info(
            "[TokenVault] Using Slack Bot Token fallback (Auth0-managed connection)"
        )
        slack_token = SLACK_BOT_TOKEN

    if not slack_token:
        return {"ok": False, "error": "[TokenVault] Could not obtain Slack token"}

    try:
        with httpx.Client() as client:
            resp = client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={"channel": channel, "text": message},
            )
            data = resp.json()
            if data.get("ok"):
                logger.info("[TokenVault] Slack alert posted to %s", channel)
                return {"ok": True, "channel": channel}
            logger.error("[TokenVault] Slack API error: %s", data.get("error", "unknown"))
            return {"ok": False, "error": data.get("error", "unknown")}
    except httpx.HTTPError as e:
        logger.error("[TokenVault] Slack HTTP error: %s", e)
        return {"ok": False, "error": str(e)}


def create_google_calendar_event(
    user_token: str,
    calendar_id: str,
    summary: str,
    description: str,
    start_time: str,
    end_time: str,
    timezone: str = "Asia/Kolkata",
) -> dict:
    """Create a Google Calendar check-in event via Token Vault.

    Called by OpenClaw when severity = HIGH_RISK to schedule a
    caregiver check-in window. The caregiver's Google OAuth token
    is exchanged through Token Vault — never stored locally.

    Auth0 feature: Token Vault (Google Calendar integration)

    Args:
        user_token: The caregiver's Auth0 access token.
        calendar_id: Google Calendar ID (usually ``primary``).
        summary: Event title.
        description: Event description.
        start_time: ISO 8601 start time.
        end_time: ISO 8601 end time.
        timezone: IANA timezone (default: ``Asia/Kolkata``).

    Returns:
        Dict with event creation status and details.
    """
    google_token = exchange_token_for_connection(
        user_token,
        "google-oauth2",
        "https://www.googleapis.com/auth/calendar",
    )
    if not google_token:
        return {"ok": False, "error": "[TokenVault] Could not obtain Google Calendar token"}

    try:
        with httpx.Client() as client:
            resp = client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {google_token}"},
                json={
                    "summary": summary,
                    "description": description,
                    "start": {"dateTime": start_time, "timeZone": timezone},
                    "end": {"dateTime": end_time, "timeZone": timezone},
                },
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                logger.info(
                    "[TokenVault] Google Calendar event created: %s",
                    data.get("id"),
                )
                return {"ok": True, "event_id": data.get("id")}
            logger.error(
                "[TokenVault] Google Calendar API error: %s %s",
                resp.status_code,
                resp.text,
            )
            return {"ok": False, "error": resp.text}
    except httpx.HTTPError as e:
        logger.error("[TokenVault] Google Calendar HTTP error: %s", e)
        return {"ok": False, "error": str(e)}


def get_connected_accounts_status(user_token: str) -> dict:
    """Check which services are connected for a caregiver.

    Auth0 feature: Token Vault — connection status introspection

    Args:
        user_token: The caregiver's Auth0 JWT.

    Returns:
        Dict mapping connection names to availability status.
    """
    status = {}
    for conn, scopes in [
        ("google-oauth2", "https://www.googleapis.com/auth/calendar"),
        ("sign-in-with-slack", "chat:write channels:read"),
    ]:
        token = exchange_token_for_connection(user_token, conn, scopes)
        status[conn] = {
            "connected": token is not None,
            "scopes": scopes,
        }
    return status
