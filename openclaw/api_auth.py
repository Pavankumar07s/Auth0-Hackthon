"""
JWT-protected Flask routes for OpenClaw REST API.

Adds Auth0 JWT validation to existing Flask endpoints using
the require_auth decorator. Protects incident, policy, and
dispatch endpoints with scope-based access control.

Auth0 Features Used: JWT Validation, Scope Enforcement
"""

import logging
import functools
from typing import Optional, Callable
from flask import Flask, request, jsonify, g
import jwt as pyjwt
import httpx

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("openclaw.api_auth")

# Auth0 configuration from environment
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-pavankumar.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://etms-api.example.com")
AUTH0_ALGORITHMS = ["RS256"]

# JWKS cache
_jwks_client: Optional[pyjwt.PyJWKClient] = None


def _get_jwks_client() -> pyjwt.PyJWKClient:
    """
    Get or create the JWKS client for Auth0 token validation.
    
    Uses PyJWT's built-in JWKS client with caching.
    Auth0 Feature: JWT RS256 validation via JWKS endpoint.
    """
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        _jwks_client = pyjwt.PyJWKClient(jwks_url, cache_keys=True)
        logger.info(f"JWKS client initialized for {AUTH0_DOMAIN}")
    return _jwks_client


def validate_token(token: str) -> dict:
    """
    Validate an Auth0 JWT access token.
    
    Verifies:
    - RS256 signature against Auth0 JWKS
    - Token issuer matches AUTH0_DOMAIN
    - Token audience matches AUTH0_AUDIENCE
    - Token is not expired
    
    Args:
        token: Raw JWT string (without 'Bearer ' prefix)
        
    Returns:
        Decoded token payload as dict
        
    Raises:
        jwt.InvalidTokenError: If token validation fails
        
    Auth0 Feature: JWT validation with RS256 + JWKS
    """
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    
    payload = pyjwt.decode(
        token,
        signing_key.key,
        algorithms=AUTH0_ALGORITHMS,
        issuer=f"https://{AUTH0_DOMAIN}/",
        audience=AUTH0_AUDIENCE,
    )
    
    return payload


def require_auth(f: Optional[Callable] = None, scopes: Optional[list[str]] = None):
    """
    Flask decorator to require Auth0 JWT authentication.
    
    Extracts Bearer token from Authorization header, validates it,
    and stores claims in Flask's g.auth0_claims. Optionally enforces
    required scopes.
    
    Usage:
        @app.route('/api/incidents')
        @require_auth
        def get_incidents():
            user = g.auth0_claims['sub']
            ...
        
        @app.route('/api/dispatch', methods=['POST'])
        @require_auth(scopes=['dispatch:emergency'])
        def dispatch():
            ...
    
    Args:
        f: The Flask view function (when used without arguments)
        scopes: Optional list of required scopes
        
    Auth0 Feature: JWT middleware with scope-based access control
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Extract token from Authorization header
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return jsonify({
                    "error": "missing_token",
                    "message": "Authorization header with Bearer token required",
                }), 401
            
            token = auth_header[7:]  # Remove 'Bearer '
            
            try:
                claims = validate_token(token)
            except pyjwt.ExpiredSignatureError:
                return jsonify({
                    "error": "token_expired",
                    "message": "Access token has expired",
                }), 401
            except pyjwt.InvalidAudienceError:
                return jsonify({
                    "error": "invalid_audience",
                    "message": f"Token audience does not match {AUTH0_AUDIENCE}",
                }), 401
            except pyjwt.InvalidIssuerError:
                return jsonify({
                    "error": "invalid_issuer",
                    "message": f"Token issuer does not match {AUTH0_DOMAIN}",
                }), 401
            except pyjwt.InvalidTokenError as e:
                return jsonify({
                    "error": "invalid_token",
                    "message": str(e),
                }), 401
            except Exception as e:
                logger.error(f"Token validation error: {e}")
                return jsonify({
                    "error": "auth_error",
                    "message": "Authentication failed",
                }), 500
            
            # Check required scopes
            if scopes:
                token_scopes = claims.get("scope", "").split()
                missing = [s for s in scopes if s not in token_scopes]
                if missing:
                    return jsonify({
                        "error": "insufficient_scope",
                        "message": f"Missing required scopes: {', '.join(missing)}",
                        "required_scopes": scopes,
                    }), 403
            
            # Store claims in Flask's g object
            g.auth0_claims = claims
            
            return func(*args, **kwargs)
        
        return wrapper
    
    # Support both @require_auth and @require_auth(scopes=[...])
    if f is not None:
        return decorator(f)
    return decorator


def register_auth_routes(app: Flask) -> None:
    """
    Register Auth0-protected API routes on the Flask app.
    
    Adds JWT-protected endpoints for:
    - GET /api/auth0/status — Auth0 connection status
    - GET /api/incidents — List incidents (requires read:incidents scope)
    - POST /api/dispatch — Trigger dispatch (requires dispatch:emergency scope)
    - GET /api/consent — List FGA consent items (requires read:consent scope)
    - POST /api/consent — Update FGA consent (requires write:consent scope)
    
    Auth0 Feature: JWT middleware on Flask routes
    """
    
    @app.route("/api/auth0/status")
    def auth0_status():
        """Public endpoint — Auth0 configuration status check."""
        return jsonify({
            "auth0_domain": AUTH0_DOMAIN,
            "auth0_audience": AUTH0_AUDIENCE,
            "status": "configured",
            "features": [
                "jwt_validation",
                "token_vault",
                "ciba",
                "fga",
                "step_up_auth",
            ],
        })
    
    @app.route("/api/incidents")
    @require_auth(scopes=["read:incidents"])
    def get_incidents():
        """List incidents — requires read:incidents scope."""
        user = g.auth0_claims.get("sub", "unknown")
        logger.info(f"Incidents requested by {user}")
        return jsonify({
            "user": user,
            "incidents": [],
            "auth0_protected": True,
        })
    
    @app.route("/api/dispatch", methods=["POST"])
    @require_auth(scopes=["dispatch:emergency"])
    def trigger_dispatch():
        """Trigger emergency dispatch — requires dispatch:emergency scope."""
        user = g.auth0_claims.get("sub", "unknown")
        data = request.get_json() or {}
        
        logger.info(f"Dispatch triggered by {user}: {data}")
        return jsonify({
            "status": "dispatch_initiated",
            "triggered_by": user,
            "auth0_features": ["jwt", "scope_check", "ciba_pending"],
        })
    
    @app.route("/api/consent")
    @require_auth(scopes=["read:consent"])
    def get_consent():
        """List FGA consent items — requires read:consent scope."""
        return jsonify({
            "consent_items": [],
            "auth0_feature": "fga",
        })
    
    @app.route("/api/consent", methods=["POST"])
    @require_auth(scopes=["write:consent"])
    def update_consent():
        """Update FGA consent — requires write:consent scope."""
        data = request.get_json() or {}
        logger.info(f"Consent update: {data}")
        return jsonify({
            "status": "updated",
            "auth0_feature": "fga",
        })
    
    logger.info("Auth0-protected routes registered on Flask app")
