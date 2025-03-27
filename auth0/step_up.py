"""Step-up authentication for CRITICAL emergency dispatch.

Auth0 feature: Step-Up Authentication (ACR/AMR claims verification)

Before dispatching emergency services, verify caregiver re-authenticated
with MFA. If not, redirect to Auth0 Universal Login with acr_values
to force multi-factor authentication.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

from jose import jwt

from auth0.config import AUTH0_AUDIENCE, AUTH0_CLIENT_ID, AUTH0_DOMAIN

logger = logging.getLogger(__name__)

MFA_ACR = "http://schemas.openid.net/pape/policies/2007/06/multi-factor"


def verify_step_up_satisfied(access_token: str) -> bool:
    """Verify the caregiver's JWT contains the required ACR claim,
    confirming MFA was satisfied for this session.

    If not satisfied, the caller should either:
    1. Redirect to Auth0 Universal Login with acr_values (interactive)
    2. Auto-escalate the incident (non-interactive agent context)

    Auth0 feature: Step-Up Authentication (ACR claim verification)

    Args:
        access_token: The caregiver's Auth0 JWT.

    Returns:
        ``True`` if MFA is verified, ``False`` otherwise.
    """
    if not access_token:
        return False

    try:
        claims = jwt.get_unverified_claims(access_token)
        acr = claims.get("acr", "")
        amr = claims.get("amr", [])

        if acr == MFA_ACR or "mfa" in amr:
            logger.info("[StepUp] MFA verification satisfied.")
            return True

        logger.warning("[StepUp] MFA not satisfied. ACR=%s AMR=%s", acr, amr)
        return False
    except Exception as e:
        logger.error("[StepUp] Token decode error: %s", e)
        return False


def get_step_up_authorization_url(
    redirect_uri: str,
    state: str = "",
) -> str:
    """Build the Auth0 Universal Login URL that forces MFA step-up.

    Redirect caregiver here if step-up is not satisfied and
    interactive re-authentication is possible (e.g., from Guardian Dashboard).

    Auth0 feature: Step-Up Authentication (authorization URL with ACR)

    Args:
        redirect_uri: Where Auth0 redirects after MFA.
        state: Optional opaque state value for CSRF protection.

    Returns:
        Full authorization URL string.
    """
    params = {
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email write:emergency_dispatch",
        "audience": AUTH0_AUDIENCE,
        "acr_values": MFA_ACR,
        "prompt": "login",
    }
    if state:
        params["state"] = state

    url = f"https://{AUTH0_DOMAIN}/authorize?" + urllib.parse.urlencode(params)
    logger.info("[StepUp] Generated step-up URL for redirect.")
    return url


def get_token_claims(access_token: str) -> Optional[dict]:
    """Extract claims from a JWT without verification.

    Useful for UI display (e.g., Guardian Dashboard showing auth context).

    Args:
        access_token: Any Auth0 JWT.

    Returns:
        Decoded claims dict, or ``None`` on failure.
    """
    try:
        return jwt.get_unverified_claims(access_token)
    except Exception as e:
        logger.error("[StepUp] Failed to decode token: %s", e)
        return None
