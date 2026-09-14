import unittest
from unittest.mock import patch

from app.api.auth import _oidc_callback_url
from app.config import settings


class OidcConfigurationTests(unittest.TestCase):
    def test_callback_uses_public_https_frontend_url(self):
        with patch.object(settings, "frontend_url", "https://testbench.joedoes.tech"):
            self.assertEqual(
                _oidc_callback_url(),
                "https://testbench.joedoes.tech/api/v1/auth/oidc/callback",
            )

    def test_callback_normalizes_frontend_trailing_slash(self):
        with patch.object(settings, "frontend_url", "https://testbench.example.com/"):
            self.assertEqual(
                _oidc_callback_url(),
                "https://testbench.example.com/api/v1/auth/oidc/callback",
            )


if __name__ == "__main__":
    unittest.main()
