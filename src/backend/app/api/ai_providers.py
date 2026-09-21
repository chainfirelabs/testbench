from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AiPluginDefault, AiProviderProfile, DeviceTypePlugin, User
from ..services.ai_configuration import (
    AI_PLUGINS, PROVIDER_TYPES, decrypt_api_key, encrypt_api_key,
    helm_ai_configuration, validate_base_url,
)
from ..services.audit import log_action
from ..services.plugin_host import registry
from .deps import require_settings_manage

router = APIRouter(prefix="/ai", tags=["ai-providers"])


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    provider_type: str = "openai-compatible"
    base_url: str
    api_key: str | None = None
    clear_api_key: bool = False
    custom_headers: dict[str, str] = Field(default_factory=dict)
    manual_models: list[str] = Field(default_factory=list)
    enabled: bool = True


class DefaultIn(BaseModel):
    profile_id: str | None = None
    model: str | None = None
    repeat_model: str | None = None


def _out(item: AiProviderProfile) -> dict:
    return {
        "id": item.id, "name": item.name, "provider_type": item.provider_type,
        "base_url": item.base_url, "credential_configured": bool(item.encrypted_api_key),
        "custom_headers": item.custom_headers or {}, "manual_models": item.manual_models or [],
        "models": sorted(set((item.manual_models or []) + (item.discovered_models or []))),
        "enabled": item.enabled, "last_error": item.last_error,
        "last_refreshed_at": item.last_refreshed_at,
    }


def _apply(item: AiProviderProfile, body: ProfileIn) -> None:
    if body.provider_type not in PROVIDER_TYPES:
        raise HTTPException(422, f"Unsupported provider type: {body.provider_type}")
    try:
        item.base_url = validate_base_url(body.base_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    item.name = body.name.strip()
    item.provider_type = body.provider_type
    item.custom_headers = dict(body.custom_headers)
    item.manual_models = sorted(set(model.strip() for model in body.manual_models if model.strip()))
    item.enabled = body.enabled
    if body.clear_api_key:
        item.encrypted_api_key = None
    if body.api_key is not None and body.api_key != "":
        try:
            item.encrypted_api_key = encrypt_api_key(body.api_key)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/providers")
def list_profiles(db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    return [_out(item) for item in db.scalars(select(AiProviderProfile).order_by(AiProviderProfile.name))]


@router.post("/providers", status_code=201)
def create_profile(body: ProfileIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    item = AiProviderProfile()
    _apply(item, body)
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409, "AI provider name already exists") from exc
    log_action(db, user, "ai_provider.create", "ai_provider", item.id, {"name": item.name, "url": item.base_url}, request)
    db.commit(); db.refresh(item)
    return _out(item)


@router.put("/providers/{profile_id}")
def update_profile(profile_id: str, body: ProfileIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    item = db.get(AiProviderProfile, profile_id)
    if item is None: raise HTTPException(404, "AI provider not found")
    _apply(item, body)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback(); raise HTTPException(409, "AI provider name already exists") from exc
    log_action(db, user, "ai_provider.update", "ai_provider", item.id, {"name": item.name, "url": item.base_url}, request)
    db.commit(); db.refresh(item)
    return _out(item)


@router.delete("/providers/{profile_id}", status_code=204)
def delete_profile(profile_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    item = db.get(AiProviderProfile, profile_id)
    if item is None: raise HTTPException(404, "AI provider not found")
    if db.scalar(select(AiPluginDefault.plugin_id).where(AiPluginDefault.profile_id == profile_id)):
        raise HTTPException(409, "AI provider is still selected as a plugin default")
    def references(value: Any) -> bool:
        if isinstance(value, dict):
            return value.get("ai_profile_id") == profile_id or any(references(v) for v in value.values())
        if isinstance(value, list): return any(references(v) for v in value)
        return False
    if any(references(row.configuration or {}) for row in db.scalars(select(DeviceTypePlugin))):
        raise HTTPException(409, "AI provider is still used by a schema, rule, or device override")
    log_action(db, user, "ai_provider.delete", "ai_provider", item.id, {"name": item.name}, request)
    db.delete(item); db.commit()


def _model_request(item: AiProviderProfile) -> tuple[str, dict[str, str]]:
    headers = {"Accept": "application/json", **(item.custom_headers or {})}
    key = decrypt_api_key(item.encrypted_api_key)
    if item.provider_type == "ollama":
        return f"{item.base_url}/api/tags", headers
    if key:
        if item.provider_type == "azure-openai": headers["api-key"] = key
        else: headers["Authorization"] = f"Bearer {key}"
    if item.base_url.endswith(("/v1", "/openai/v1")):
        suffix = "/models"
    elif item.provider_type == "azure-openai":
        suffix = "/openai/v1/models"
    else:
        suffix = "/v1/models"
    return item.base_url + suffix, headers


@router.post("/providers/{profile_id}/refresh-models")
def refresh_models(profile_id: str, db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    item = db.get(AiProviderProfile, profile_id)
    if item is None: raise HTTPException(404, "AI provider not found")
    try:
        url, headers = _model_request(item)
        response = httpx.get(url, headers=headers, timeout=10, follow_redirects=False)
        response.raise_for_status()
        payload: Any = response.json()
        rows = payload.get("models", []) if item.provider_type == "ollama" else payload.get("data", [])
        models = sorted(set(str(row.get("model") or row.get("name") or row.get("id")) for row in rows if isinstance(row, dict) and (row.get("model") or row.get("name") or row.get("id"))))
        item.discovered_models, item.last_error = models, None
    except Exception as exc:  # noqa: BLE001
        item.last_error = str(exc)[:1000]
        db.commit()
        raise HTTPException(502, f"Model discovery failed: {item.last_error}") from exc
    item.last_refreshed_at = datetime.now(timezone.utc).isoformat()
    db.commit(); db.refresh(item)
    return _out(item)


@router.get("/plugin-defaults")
def list_defaults(db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    result = []
    for plugin_id in sorted(AI_PLUGINS):
        helm = helm_ai_configuration(registry.manifest(plugin_id))
        row = db.get(AiPluginDefault, plugin_id)
        result.append({
            "plugin_id": plugin_id, "locked": bool(helm.get("locked")),
            "helm": helm,
            "profile_id": None if helm.get("locked") else (row.profile_id if row else None),
            "model": helm.get("model") if helm.get("locked") else (row.model if row else None),
            "repeat_model": helm.get("repeat_model") if helm.get("locked") else (row.repeat_model if row else None),
        })
    return result


@router.put("/plugin-defaults/{plugin_id}")
def put_default(plugin_id: str, body: DefaultIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_settings_manage)):
    if plugin_id not in AI_PLUGINS: raise HTTPException(404, "AI plugin not found")
    if helm_ai_configuration(registry.manifest(plugin_id)).get("locked"):
        raise HTTPException(409, "This plugin's AI settings are managed by Helm")
    if body.profile_id and db.get(AiProviderProfile, body.profile_id) is None:
        raise HTTPException(422, "AI provider not found")
    row = db.get(AiPluginDefault, plugin_id) or AiPluginDefault(plugin_id=plugin_id)
    row.profile_id, row.model = body.profile_id, body.model or None
    row.repeat_model = body.repeat_model or None
    db.add(row)
    log_action(db, user, "ai_plugin_default.update", "plugin", plugin_id, {"profile_id": body.profile_id, "model": body.model, "repeat_model": body.repeat_model}, request)
    db.commit()
    return {"plugin_id": plugin_id, "locked": False, "profile_id": row.profile_id, "model": row.model, "repeat_model": row.repeat_model}
