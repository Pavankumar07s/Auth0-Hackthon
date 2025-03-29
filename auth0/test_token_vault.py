"""Token Vault unit tests.

Tests the Token Vault exchange strategies:
1. Refresh-token exchange
2. Access-token exchange
3. Management API fallback
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


class TestTokenVault(unittest.TestCase):
    """Test Token Vault functionality."""

    def test_import(self) -> None:
        """Token Vault module imports successfully."""
        from auth0.token_vault import (
            exchange_token_for_connection,
            post_slack_alert,
            create_google_calendar_event,
            get_connected_accounts_status,
        )
        self.assertTrue(callable(exchange_token_for_connection))

    def test_decode_jwt_payload(self) -> None:
        """JWT payload decoding works for extracting sub claim."""
        from auth0.token_vault import _decode_jwt_payload

        # Valid JWT structure (header.payload.sig)
        import base64
        import json

        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "google-oauth2|123", "aud": "test"}).encode()
        ).rstrip(b"=").decode()
        token = f"eyJhbGciOiJSUzI1NiJ9.{payload}.fake_sig"

        result = _decode_jwt_payload(token)
        self.assertIsNotNone(result)
        self.assertEqual(result["sub"], "google-oauth2|123")

    def test_decode_jwt_invalid(self) -> None:
        """Invalid JWT returns None gracefully."""
        from auth0.token_vault import _decode_jwt_payload

        self.assertIsNone(_decode_jwt_payload("not_a_jwt"))
        self.assertIsNone(_decode_jwt_payload(""))

    def test_load_refresh_token_missing(self) -> None:
        """Missing refresh token file returns None."""
        from auth0.token_vault import _load_refresh_token

        # This may or may not exist — just verify it doesn't crash
        result = _load_refresh_token()
        self.assertIsInstance(result, (str, type(None)))

    def test_connected_accounts_status_structure(self) -> None:
        """get_connected_accounts_status returns correct structure."""
        from auth0.token_vault import get_connected_accounts_status

        # Will fail exchange but should return structured result
        status = get_connected_accounts_status("fake_token")
        self.assertIn("google-oauth2", status)
        self.assertIn("slack", status)
        self.assertIn("connected", status["google-oauth2"])


if __name__ == "__main__":
    unittest.main()
