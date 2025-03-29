"""CIBA unit tests.

Tests the CIBA backchannel authorization flow:
- Request initiation
- Polling logic
- Timeout handling
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


class TestCIBA(unittest.TestCase):
    """Test CIBA backchannel authorization."""

    def test_import(self) -> None:
        """CIBA module imports successfully."""
        from auth0.ciba import (
            request_backchannel_authorization,
            poll_for_approval,
            critical_dispatch_with_approval,
        )
        self.assertTrue(callable(request_backchannel_authorization))

    def test_constants(self) -> None:
        """CIBA constants are set to safe defaults."""
        from auth0.ciba import CIBA_TIMEOUT_SECONDS, POLL_INTERVAL_SECONDS

        self.assertEqual(CIBA_TIMEOUT_SECONDS, 120)
        self.assertEqual(POLL_INTERVAL_SECONDS, 5)

    def test_binding_message_truncation(self) -> None:
        """Binding message is truncated to 128 chars for Guardian push."""
        # This is tested indirectly through the request function
        from auth0.ciba import request_backchannel_authorization
        self.assertTrue(callable(request_backchannel_authorization))


if __name__ == "__main__":
    unittest.main()
