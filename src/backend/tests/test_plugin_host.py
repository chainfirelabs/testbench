import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.config import settings
from app.api.deps import get_current_user, require_session, require_write
from app.api.plugins import _preserve_repeat_artifact
from app.services.plugin_host import PluginRegistry


class PluginConfigurationTests(unittest.TestCase):
    def test_bundled_mcp_token_is_a_readonly_non_session_identity(self):
        request = SimpleNamespace(state=SimpleNamespace())
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="internal-secret")

        with patch.object(settings, "mcp_internal_token", "internal-secret"):
            user = get_current_user(request=request, credentials=credentials, db=None)

        self.assertEqual(user.username, "testbench-mcp")
        self.assertEqual(user.role, "readonly")
        self.assertEqual(request.state.auth_method, "mcp_internal")
        with self.assertRaises(HTTPException) as write_error:
            require_write(user)
        self.assertEqual(write_error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as session_error:
            require_session(request, user)
        self.assertEqual(session_error.exception.status_code, 403)

    def test_repeat_model_preserves_an_existing_discovery_recipe(self):
        self.assertTrue(_preserve_repeat_artifact("saved_recipe", "discovery_recipe", True))
        self.assertFalse(_preserve_repeat_artifact("discovery", "discovery_recipe", True))
        self.assertFalse(_preserve_repeat_artifact("saved_recipe", "discovery_recipe", False))
        self.assertFalse(_preserve_repeat_artifact("saved_recipe", "other_artifact", True))

    def test_endpoint_map_ignores_empty_and_malformed_entries(self):
        with patch.object(settings, "plugins", "network-scan=http://scan:8080,bad, device-info-agent=http://info:8080/"):
            self.assertEqual(settings.plugin_endpoints, {
                "network-scan": "http://scan:8080",
                "device-info-agent": "http://info:8080",
            })

    @patch("app.services.plugin_host.httpx.get")
    def test_registry_accepts_matching_protocol_v1_manifest(self, get):
        response = Mock()
        response.json.return_value = {
            "id": "network-scan", "protocol_version": 1, "version": "1.0.0", "actions": []
        }
        response.raise_for_status.return_value = None
        get.return_value = response
        registry = PluginRegistry()
        with (
            patch.object(settings, "plugins", "network-scan=http://scan:8080"),
            patch.object(settings, "plugin_shared_secret", "secret"),
        ):
            registry.refresh()
            self.assertEqual(registry.status()[0]["state"], "loaded")

    @patch("app.services.plugin_host.httpx.get")
    def test_registry_rejects_manifest_identity_mismatch(self, get):
        response = Mock()
        response.json.return_value = {"id": "other", "protocol_version": 1, "actions": []}
        response.raise_for_status.return_value = None
        get.return_value = response
        registry = PluginRegistry()
        with patch.object(settings, "plugins", "network-scan=http://scan:8080"):
            registry.refresh()
            self.assertEqual(registry.status()[0]["state"], "unhealthy")


if __name__ == "__main__":
    unittest.main()
