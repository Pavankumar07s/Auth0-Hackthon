"""Auth0 configuration loader.

Centralizes all Auth0 credential loading from environment variables.
Never hardcode secrets — always use .env via python-dotenv.

Auth0 features configured here:
- Core tenant credentials (domain, client ID/secret)
- Token Vault (audience)
- FGA (store ID, API URL, credentials)
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- Core Auth0 Tenant ---
AUTH0_DOMAIN: str = os.getenv("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID: str = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET: str = os.getenv("AUTH0_CLIENT_SECRET", "")
AUTH0_AUDIENCE: str = os.getenv("AUTH0_AUDIENCE", "https://etms-api.example.com")

# --- Token Vault / AI Agent ---
AUTH0_AI_SECRET: str = os.getenv("AUTH0_AI_SECRET", "")
AUTH0_CUSTOM_API_CLIENT_ID: str = os.getenv("AUTH0_CUSTOM_API_CLIENT_ID", "")
AUTH0_CUSTOM_API_CLIENT_SECRET: str = os.getenv("AUTH0_CUSTOM_API_CLIENT_SECRET", "")

# --- M2M (Machine-to-Machine) for Management API ---
AUTH0_M2M_CLIENT_ID: str = os.getenv("AUTH0_M2M_CLIENT_ID", "")
AUTH0_M2M_CLIENT_SECRET: str = os.getenv("AUTH0_M2M_CLIENT_SECRET", "")

# --- Social Connections ---
AUTH0_CONNECTION_GOOGLE: str = os.getenv("AUTH0_CONNECTION_GOOGLE", "google-oauth2")
AUTH0_CONNECTION_SLACK: str = os.getenv("AUTH0_CONNECTION_SLACK", "sign-in-with-slack")

# --- FGA (Fine-Grained Authorization) ---
FGA_STORE_ID: str = os.getenv("FGA_STORE_ID", "")
FGA_API_URL: str = os.getenv("FGA_API_URL", "https://api.us1.fga.dev")
FGA_CLIENT_ID: str = os.getenv("FGA_CLIENT_ID", "")
FGA_CLIENT_SECRET: str = os.getenv("FGA_CLIENT_SECRET", "")

# --- External Service Scopes ---
GOOGLE_CALENDAR_SCOPE: str = os.getenv(
    "GOOGLE_CALENDAR_SCOPE", "https://www.googleapis.com/auth/calendar"
)
SLACK_SCOPE: str = os.getenv("SLACK_SCOPE", "chat:write,channels:read")


def validate_config() -> list:
    """Validate that required Auth0 config is present.

    Returns:
        List of missing configuration keys (empty if all present).
    """
    required = [
        ("AUTH0_DOMAIN", AUTH0_DOMAIN),
        ("AUTH0_CLIENT_ID", AUTH0_CLIENT_ID),
        ("AUTH0_CLIENT_SECRET", AUTH0_CLIENT_SECRET),
        ("AUTH0_AUDIENCE", AUTH0_AUDIENCE),
        ("FGA_STORE_ID", FGA_STORE_ID),
        ("FGA_API_URL", FGA_API_URL),
        ("FGA_CLIENT_ID", FGA_CLIENT_ID),
        ("FGA_CLIENT_SECRET", FGA_CLIENT_SECRET),
    ]
    return [name for name, value in required if not value]
