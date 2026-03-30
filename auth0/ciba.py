"""CIBA (Client-Initiated Backchannel Authentication) for ETMS.

Auth0 feature: Asynchronous Authorization (CIBA + RAR)
Docs: https://auth0.com/ai/docs/intro/asynchronous-authorization

Used by OpenClaw when a CRITICAL incident occurs:
1. Send a Guardian push notification to the caregiver's phone
2. Include RAR (Rich Authorization Request) with incident context
3. Poll for approval / denial / timeout
4. Dispatch emergency services (approved) or auto-escalate (denied/timeout)

Safety-first design: if caregiver is unreachable, auto-escalates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Tuple

import httpx

from auth0.config import (
    AUTH0_CLIENT_ID,
    AUTH0_CLIENT_SECRET,
    AUTH0_DOMAIN,
    AUTH0_AUDIENCE,
)

logger = logging.getLogger(__name__)

CIBA_TIMEOUT_SECONDS: int = 120
POLL_INTERVAL_SECONDS: int = 5


def request_backchannel_authorization(
    caregiver_id: str,
    incident_context: dict,
) -> Optional[str]:
    """Initiate a CIBA request. Auth0 sends a push notification to
    caregiver's phone showing the RAR (Rich Authorization Request) payload.

    Auth0 feature: CIBA (Asynchronous Authorization)

    Args:
        caregiver_id: The caregiver's Auth0 user ID or email.
        incident_context: Dict with incident details:
            - elder_name: Name of the elderly person
            - location: Where the incident occurred
            - event_type: Type of emergency
            - vitals_summary: Current vital signs

    Returns:
        The ``auth_req_id`` for polling, or ``None`` on failure.
    """
    elder_name = incident_context.get("elder_name", "Unknown")
    location = incident_context.get("location", "")
    event_type = incident_context.get("event_type", "emergency")
    vitals = incident_context.get("vitals_summary", "not available")

    # Rich Authorization Request payload
    authorization_details = json.dumps([
        {
            "type": "etms_emergency_dispatch",
            "description": "Approve to dispatch emergency services",
            "elder_name": elder_name,
            "location": location,
            "event_type": event_type,
            "vitals": vitals,
            "timeout_action": f"If denied or timeout: auto-escalate in {CIBA_TIMEOUT_SECONDS}s",
        }
    ])

    # Binding message shown on Guardian push (Auth0 CIBA limit: 64 chars)
    binding_message = f"ETMS: {elder_name} - {event_type} at {location}"
    if len(binding_message) > 64:
        binding_message = binding_message[:61] + "..."

    # Auth0 CIBA expects login_hint as JSON
    if "@" in caregiver_id:
        login_hint = json.dumps({"format": "email", "email": caregiver_id})
    elif caregiver_id.startswith("auth0|") or "|" in caregiver_id:
        login_hint = json.dumps({
            "format": "iss_sub",
            "iss": f"https://{AUTH0_DOMAIN}/",
            "sub": caregiver_id,
        })
    else:
        login_hint = json.dumps({"format": "iss_sub", "iss": f"https://{AUTH0_DOMAIN}/", "sub": caregiver_id})

    try:
        with httpx.Client() as client:
            request_data = {
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "login_hint": login_hint,
                "scope": "openid",
                "audience": AUTH0_AUDIENCE,
                "binding_message": binding_message,
            }
            resp = client.post(
                f"https://{AUTH0_DOMAIN}/bc-authorize",
                data=request_data,
            )
            if resp.status_code == 200:
                auth_req_id = resp.json().get("auth_req_id")
                logger.info("[CIBA] Push sent to caregiver. auth_req_id=%s", auth_req_id)
                return auth_req_id
            logger.error(
                "[CIBA] Failed to initiate: %s %s",
                resp.status_code,
                resp.text,
            )
            return None
    except httpx.HTTPError as e:
        logger.error("[CIBA] HTTP error during initiation: %s", e)
        return None


def poll_for_approval(
    auth_req_id: str,
    timeout: int = CIBA_TIMEOUT_SECONDS,
) -> Tuple[bool, str, Optional[str]]:
    """Poll Auth0 /oauth/token until caregiver approves, denies, or timeout.

    Auth0 feature: CIBA polling (token endpoint with CIBA grant type)

    Args:
        auth_req_id: The CIBA request ID from ``request_backchannel_authorization``.
        timeout: Max seconds to wait for caregiver response.

    Returns:
        Tuple of (approved: bool, reason: str, access_token: Optional[str]).
    """
    start = time.time()
    poll_interval = POLL_INTERVAL_SECONDS

    while time.time() - start < timeout:
        time.sleep(poll_interval)

        try:
            with httpx.Client() as client:
                resp = client.post(
                    f"https://{AUTH0_DOMAIN}/oauth/token",
                    data={
                        "grant_type": "urn:openid:params:grant-type:ciba",
                        "client_id": AUTH0_CLIENT_ID,
                        "client_secret": AUTH0_CLIENT_SECRET,
                        "auth_req_id": auth_req_id,
                    },
                )

                if resp.status_code == 200:
                    logger.info("[CIBA] Caregiver APPROVED emergency dispatch.")
                    return True, "approved", resp.json().get("access_token")

                error_data = resp.json()
                error = error_data.get("error", "")

                if error == "authorization_pending":
                    continue
                elif error == "slow_down":
                    logger.info("[CIBA] Rate limited — slowing down polling interval")
                    poll_interval = poll_interval * 2
                    continue
                elif error == "access_denied":
                    logger.warning("[CIBA] Caregiver DENIED. Auto-escalating.")
                    return False, "denied", None
                elif error == "expired_token":
                    logger.warning("[CIBA] CIBA request expired. Auto-escalating.")
                    return False, "expired", None
                else:
                    logger.error("[CIBA] Unexpected error: %s. Auto-escalating.", error)
                    return False, error, None

        except httpx.HTTPError as e:
            logger.error("[CIBA] Polling HTTP error: %s. Retrying...", e)
            continue

    logger.warning("[CIBA] Timeout reached (%ds). Auto-escalating.", timeout)
    return False, "timeout", None


def critical_dispatch_with_approval(
    caregiver_id: str,
    incident_context: dict,
    dispatch_callback: Callable[[dict, bool], Any],
) -> dict:
    """Full CIBA flow for CRITICAL incidents.

    1. Send Guardian push with RAR context
    2. Poll for caregiver approval
    3. Dispatch (approved) or auto-escalate (denied/timeout)

    Safety-first: emergency is ALWAYS dispatched eventually.
    - If approved: normal dispatch
    - If denied/timeout/error: auto-escalation (dispatch anyway)

    Auth0 feature: CIBA + RAR (end-to-end)

    Args:
        caregiver_id: The caregiver's Auth0 user ID or email.
        incident_context: Dict with elder_name, location, event_type, vitals_summary.
        dispatch_callback: Function called with (incident_context, approved) to
            perform the actual dispatch.

    Returns:
        Dict with status, approved flag, and reason.
    """
    elder_name = incident_context.get("elder_name", "Unknown")
    logger.info(
        "[CIBA] CRITICAL incident — initiating caregiver approval flow for %s",
        elder_name,
    )

    auth_req_id = request_backchannel_authorization(caregiver_id, incident_context)

    if not auth_req_id:
        logger.error("[CIBA] Could not reach Auth0. Auto-escalating for safety.")
        dispatch_callback(incident_context, False)
        return {
            "status": "ciba_unavailable",
            "approved": False,
            "reason": "escalated",
        }

    approved, reason, _token = poll_for_approval(auth_req_id)

    dispatch_callback(incident_context, approved)

    status = "caregiver_approved" if approved else "auto_escalated"
    logger.info(
        "[CIBA] CRITICAL dispatch complete: approved=%s reason=%s",
        approved,
        reason,
    )

    return {
        "status": status,
        "approved": approved,
        "reason": reason,
    }
