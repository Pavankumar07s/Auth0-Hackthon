"""FGA authorization check helper for ETMS.

Auth0 feature: Fine-Grained Authorization (OpenFGA)
Docs: https://auth0.com/ai/docs/intro/authorization-for-rag

Used by Vision Agent to check if it's allowed to access specific
data streams before assembling context for the LLM reasoning chain.
Prevents unauthorized access to health records, location data, etc.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List

import openfga_sdk

from auth0.config import (
    FGA_API_URL,
    FGA_CLIENT_ID,
    FGA_CLIENT_SECRET,
    FGA_STORE_ID,
)

logger = logging.getLogger(__name__)


def _get_fga_config() -> openfga_sdk.ClientConfiguration:
    """Build OpenFGA client configuration.

    Auth0 feature: FGA SDK configuration with client credentials.

    Returns:
        Configured ``ClientConfiguration`` for OpenFGA API calls.
    """
    config = openfga_sdk.ClientConfiguration(
        api_url=FGA_API_URL,
        store_id=FGA_STORE_ID,
        credentials=openfga_sdk.Credentials(
            method="client_credentials",
            configuration=openfga_sdk.CredentialConfiguration(
                api_issuer="fga.us.auth0.com",
                api_audience="https://api.us1.fga.dev/",
                client_id=FGA_CLIENT_ID,
                client_secret=FGA_CLIENT_SECRET,
            ),
        ),
    )
    return config


async def is_authorized(user: str, relation: str, object_name: str) -> bool:
    """Check if 'user' has 'relation' access to 'object_name' in FGA.

    Auth0 feature: Fine-Grained Authorization (permission check)

    Args:
        user: FGA user identifier (e.g. ``user:vision_agent``).
        relation: The relationship type (e.g. ``viewer``).
        object_name: The object to check (e.g. ``data_stream:fall_events``).

    Returns:
        ``True`` if authorized, ``False`` otherwise.
    """
    config = _get_fga_config()
    try:
        async with openfga_sdk.OpenFgaClient(config) as client:
            body = openfga_sdk.ClientCheckRequest(
                user=user,
                relation=relation,
                object=object_name,
            )
            response = await client.check(body)
            allowed = response.allowed or False
            logger.info("[FGA] Check: %s %s %s → %s", user, relation, object_name, allowed)
            return allowed
    except Exception as e:
        logger.error("[FGA] Authorization check failed for %s %s %s: %s", user, relation, object_name, e)
        return False


async def filter_streams_by_permission(
    user: str,
    relation: str,
    streams: List[str],
) -> List[str]:
    """Filter a list of data streams to only those the requester can access.

    Used by Vision Agent's ContextSnapshot builder to enforce
    FGA-based data boundaries before context assembly.

    Auth0 feature: FGA (batch authorization for RAG filtering)

    Args:
        user: FGA user identifier.
        relation: The required relationship (e.g. ``viewer``).
        streams: List of data stream names to check.

    Returns:
        Filtered list of authorized stream names.
    """
    config = _get_fga_config()
    authorized = []

    try:
        async with openfga_sdk.OpenFgaClient(config) as client:
            for stream in streams:
                body = openfga_sdk.ClientCheckRequest(
                    user=user,
                    relation=relation,
                    object=f"data_stream:{stream}",
                )
                response = await client.check(body)
                if response.allowed:
                    authorized.append(stream)

        logger.info(
            "[FGA] Filtered %d/%d streams for %s: %s",
            len(authorized),
            len(streams),
            user,
            authorized,
        )
    except Exception as e:
        logger.error("[FGA] Batch filter failed: %s", e)

    return authorized


async def batch_check_permissions(
    user: str,
    checks: List[dict],
) -> dict:
    """Check multiple permissions at once for a user.

    Useful for Guardian Dashboard to show full permission matrix.

    Auth0 feature: FGA batch authorization check

    Args:
        user: FGA user identifier.
        checks: List of dicts with ``relation`` and ``object`` keys.

    Returns:
        Dict mapping ``"relation:object"`` to bool.
    """
    config = _get_fga_config()
    results = {}

    try:
        async with openfga_sdk.OpenFgaClient(config) as client:
            for check in checks:
                body = openfga_sdk.ClientCheckRequest(
                    user=user,
                    relation=check["relation"],
                    object=check["object"],
                )
                response = await client.check(body)
                key = f"{check['relation']}:{check['object']}"
                results[key] = response.allowed or False
    except Exception as e:
        logger.error("[FGA] Batch check failed: %s", e)

    return results
