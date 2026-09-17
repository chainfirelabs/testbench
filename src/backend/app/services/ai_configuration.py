import base64
import hashlib
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AiPluginDefault, AiProviderProfile

AI_PLUGINS = {"device-info-agent", "device-reboot"}
PROVIDER_TYPES = {"openai", "openai-compatible", "azure-openai", "ollama", "litellm"}


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("AI endpoint must be an HTTP(S) URL without embedded credentials")
    return value


def runtime_base_url(provider_type: str, base_url: str) -> str:
    """Return the OpenAI-compatible URL consumed by the browser worker."""
    if provider_type == "azure-openai" and not base_url.endswith("/openai/v1"):
        return f"{base_url}/openai/v1"
    if provider_type in {"openai", "ollama"} and not base_url.endswith("/v1"):
        return f"{base_url}/v1"
    return base_url


def _fernet() -> Fernet:
    if not settings.credential_encryption_key:
        raise ValueError("TB_CREDENTIAL_ENCRYPTION_KEY is required to store an AI API key")
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.credential_encryption_key.encode()).digest())
    return Fernet(key)


def encrypt_api_key(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_api_key(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("AI credential cannot be decrypted with the configured encryption key") from exc


def helm_ai_configuration(manifest: dict | None) -> dict:
    return dict((manifest or {}).get("ai_configuration") or {})


def validate_ai_profile_references(db: Session, configuration: dict) -> None:
    def collect(value) -> set[str]:
        if isinstance(value, dict):
            found = {value["ai_profile_id"]} if value.get("ai_profile_id") else set()
            return found.union(*(collect(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(collect(item) for item in value))
        return set()
    for profile_id in collect(configuration):
        if db.get(AiProviderProfile, profile_id) is None:
            raise ValueError(f"Unknown AI provider profile: {profile_id}")


def resolve_ai_configuration(
    db: Session, plugin_id: str, plugin_configuration: dict, manifest: dict | None,
) -> dict:
    helm = helm_ai_configuration(manifest)
    if helm.get("locked"):
        return {**helm, "source": "helm", "api_key": None}

    default = db.get(AiPluginDefault, plugin_id)
    profile_id = plugin_configuration.get("ai_profile_id") or (default.profile_id if default else None)
    profile = db.get(AiProviderProfile, profile_id) if profile_id else None
    if profile is None or not profile.enabled:
        raise ValueError(f"No enabled AI provider profile is configured for {plugin_id}")
    model = plugin_configuration.get("ai_model") or (default.model if default else None)
    repeat = plugin_configuration.get("ai_repeat_model") or (default.repeat_model if default else None) or model
    if not model:
        raise ValueError(f"No AI model is configured for {plugin_id}")
    return {
        "locked": False,
        "source": "gui",
        "profile_id": profile.id,
        "provider_type": profile.provider_type,
        "url": runtime_base_url(profile.provider_type, profile.base_url),
        "model": model,
        "repeat_model": repeat,
        "api_key": decrypt_api_key(profile.encrypted_api_key),
        "custom_headers": profile.custom_headers or {},
    }
