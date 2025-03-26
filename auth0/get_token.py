"""Auth0 Login Flow — Get caregiver access token.

Opens browser for Auth0 Universal Login, captures the authorization code
via a local redirect server, and exchanges it for tokens.

Usage:
    python auth0/get_token.py
"""

import http.server
import json
import os
import sys
import urllib.parse
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import httpx

from auth0.config import AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_AUDIENCE

REDIRECT_URI = "http://localhost:8787/callback"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".caregiver_token")
REFRESH_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".caregiver_refresh_token")


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handle the OAuth callback from Auth0."""

    auth_code = None

    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        if "code" in params:
            CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Login successful!</h1><p>You can close this window.</p>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error = params.get("error_description", ["Unknown error"])[0]
            self.wfile.write(f"<h1>Error</h1><p>{error}</p>".encode())

    def log_message(self, format, *args) -> None:
        pass  # Suppress HTTP server logs


def main() -> None:
    """Run the Auth0 login flow."""
    if not AUTH0_DOMAIN or not AUTH0_CLIENT_ID:
        print("ERROR: AUTH0_DOMAIN and AUTH0_CLIENT_ID must be set in .env")
        sys.exit(1)

    # Build authorization URL
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": AUTH0_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "audience": AUTH0_AUDIENCE,
        "connection": "google-oauth2",
        "access_type": "offline",
        "approval_prompt": "force",
    })
    auth_url = f"https://{AUTH0_DOMAIN}/authorize?{params}"

    print(f"Opening browser for Auth0 login...")
    print(f"URL: {auth_url}")
    webbrowser.open(auth_url)

    # Start local server to capture callback
    server = http.server.HTTPServer(("localhost", 8787), CallbackHandler)
    print("Waiting for callback on http://localhost:8787/callback ...")
    server.handle_request()

    if not CallbackHandler.auth_code:
        print("ERROR: No authorization code received")
        sys.exit(1)

    # Exchange code for tokens
    print("Exchanging authorization code for tokens...")
    with httpx.Client() as client:
        resp = client.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "code": CallbackHandler.auth_code,
                "redirect_uri": REDIRECT_URI,
            },
        )

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    data = resp.json()
    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    id_token = data.get("id_token", "")

    # Save tokens
    with open(TOKEN_FILE, "w") as f:
        f.write(access_token)
    print(f"Access token saved to {TOKEN_FILE}")

    if refresh_token:
        with open(REFRESH_TOKEN_FILE, "w") as f:
            f.write(refresh_token)
        print(f"Refresh token saved to {REFRESH_TOKEN_FILE}")

    print(f"\nAccess token (first 50 chars): {access_token[:50]}...")
    print(f"Token type: {data.get('token_type', 'unknown')}")
    print(f"Expires in: {data.get('expires_in', 'unknown')}s")
    print(f"Scopes: {data.get('scope', 'none')}")


if __name__ == "__main__":
    main()
