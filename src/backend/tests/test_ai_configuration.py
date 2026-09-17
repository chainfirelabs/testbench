from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from app.services import ai_configuration as ai


def test_gui_credentials_are_encrypted_and_decrypted():
    with patch.object(ai.settings, "credential_encryption_key", "test-key"):
        encrypted = ai.encrypt_api_key("secret-value")
        assert encrypted != "secret-value"
        assert ai.decrypt_api_key(encrypted) == "secret-value"


def test_helm_configuration_is_authoritative():
    manifest = {"ai_configuration": {
        "locked": True, "url": "https://helm.example/v1",
        "model": "discover", "repeat_model": "repeat",
    }}
    result = ai.resolve_ai_configuration(Mock(), "device-reboot", {
        "ai_profile_id": "ignored", "ai_model": "ignored",
    }, manifest)
    assert result["source"] == "helm"
    assert result["url"] == "https://helm.example/v1"
    assert result["repeat_model"] == "repeat"
    assert result["api_key"] is None


def test_gui_configuration_resolves_plugin_defaults_and_overrides():
    profile = NS(
        id="profile", enabled=True, provider_type="openai-compatible",
        base_url="https://gui.example/v1", encrypted_api_key=None, custom_headers={},
    )
    default = NS(profile_id="profile", model="discovery", repeat_model="repeat")
    db = Mock()
    db.get.side_effect = lambda model, key: default if model.__name__ == "AiPluginDefault" else profile
    result = ai.resolve_ai_configuration(db, "device-info-agent", {"ai_model": "type-model"}, {})
    assert result["source"] == "gui"
    assert result["model"] == "type-model"
    assert result["repeat_model"] == "repeat"
    assert result["url"] == "https://gui.example/v1"


def test_ai_url_rejects_embedded_credentials_and_non_http_protocols():
    with pytest.raises(ValueError): ai.validate_base_url("https://user:pass@example.test/v1")
    with pytest.raises(ValueError): ai.validate_base_url("file:///etc/passwd")


@pytest.mark.parametrize(("provider", "source", "expected"), [
    ("openai", "https://api.openai.com", "https://api.openai.com/v1"),
    ("openai", "https://api.openai.com/v1", "https://api.openai.com/v1"),
    ("azure-openai", "https://example.openai.azure.com", "https://example.openai.azure.com/openai/v1"),
    ("ollama", "http://ollama:11434", "http://ollama:11434/v1"),
    ("litellm", "https://litellm.example/custom", "https://litellm.example/custom"),
])
def test_runtime_base_url(provider, source, expected):
    assert ai.runtime_base_url(provider, source) == expected
