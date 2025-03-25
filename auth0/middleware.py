"""Auth0 JWT middleware for FastAPI endpoints.

Auth0 feature: User Authentication (JWT verification middleware)
SDK: auth0-fastapi-api

Provides FastAPI dependency injection for JWT-protected routes.
Used by OpenClaw API and new Auth0-protected endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth0.config import AUTH0_AUDIENCE, AUTH0_DOMAIN

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """FastAPI dependency: verify Auth0 JWT and return user claims.

    Auth0 feature: User Authentication (JWT middleware for FastAPI)

    Args:
        credentials: Injected by FastAPI from Authorization header.

    Returns:
        Decoded JWT claims dict.

    Raises:
        HTTPException 401: If the token is invalid, expired, or missing.
    """
    token = credentials.credentials

    try:
        # Use PyJWT + JWKS to verify RS256 token
        import jwt
        from jwt import PyJWKClient

        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/",
        )
        return claims

    except Exception as e:
        logger.warning("Invalid or expired Auth0 token: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Auth0 token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_scope(scope: str, claims: dict) -> bool:
    """Check if JWT claims contain the required scope.

    Auth0 feature: Scoped API authorization

    Args:
        scope: Required OAuth scope.
        claims: Decoded JWT claims.

    Returns:
        ``True`` if scope is present.

    Raises:
        HTTPException 403: If the required scope is missing.
    """
    token_scopes = claims.get("scope", "").split()
    permissions = claims.get("permissions", [])

    if scope in token_scopes or scope in permissions:
        return True

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing required scope: {scope}",
    )


def require_auth(scopes: Optional[list] = None):
    """Factory for FastAPI dependency that requires auth + optional scopes.

    Auth0 feature: User Authentication + scoped authorization

    Args:
        scopes: Optional list of required OAuth scopes.

    Usage:
        @app.get("/protected", dependencies=[Depends(require_auth(["read:data"]))])
        async def protected_route(): ...
    """

    async def _verify(
        credentials: HTTPAuthorizationCredentials = Depends(security),
    ) -> dict:
        claims = await get_current_user(credentials)
        if scopes:
            for scope in scopes:
                require_scope(scope, claims)
        return claims

    return Depends(_verify)
