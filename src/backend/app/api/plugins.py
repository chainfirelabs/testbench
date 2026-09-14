import hmac
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..models import Device, DeviceType, DeviceTypePlugin, PluginArtifact, PluginResultReceipt, User
from ..services.audit import field_diff, log_action
from ..services.device_schema import (
    PluginPolicyError,
    fields_for_device,
    get_allowed_plugins,
    get_effective_fields,
    output_roles_blocker,
    missing_plugin_roles,
    plugin_payload_for,
    role_map,
    validate_plugin_invocation,
)
from ..services.plugin_configuration import resolve_plugin_configuration, validate_plugin_configuration
from ..services.plugin_host import registry
from .deps import get_current_user, require_admin, require_write

router = APIRouter(prefix="/plugins", tags=["plugins"])
host_router = APIRouter(prefix="/plugin-host", tags=["plugin-host"])


class InvokeIn(BaseModel):
    entity: str = "devices"
    entity_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)


class DevicePluginConfigurationIn(BaseModel):
    configuration: dict = Field(default_factory=dict)


class ArtifactIn(BaseModel):
    schema_version: int = 1
    payload: dict
    metadata: dict = Field(default_factory=dict)


class DiscoveryFinding(BaseModel):
    value: Any
    confidence: float = Field(ge=0, le=1)
    source: str
    starting_value: Any = None


class DiscoveryResultIn(BaseModel):
    device_id: str
    run_id: str
    observed_at: str | None = None
    model: dict = Field(default_factory=dict)
    findings: dict[str, DiscoveryFinding]
    artifacts: dict[str, ArtifactIn] = Field(default_factory=dict)


class ScanItemIn(BaseModel):
    device_id: str
    starting_addresses: dict[str, Any] = Field(default_factory=dict)
    probed: bool
    online: bool
    probes: list[dict] = Field(default_factory=list)


class ScanResultIn(BaseModel):
    run_id: str
    items: list[ScanItemIn]


class RebootResultIn(BaseModel):
    device_id: str
    run_id: str
    method: str
    verified_online: bool
    model: Any = None
    successful_steps: list[Any] = Field(default_factory=list)


def _plugin_identity(
    x_testbench_plugin: str = Header(alias="X-TestBench-Plugin"),
    x_testbench_plugin_secret: str = Header(alias="X-TestBench-Plugin-Secret"),
) -> str:
    if not settings.plugin_shared_secret or not hmac.compare_digest(
        x_testbench_plugin_secret, settings.plugin_shared_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid plugin credential")
    if x_testbench_plugin not in settings.plugin_endpoints:
        raise HTTPException(status_code=403, detail="Plugin is not enabled")
    return x_testbench_plugin


@router.get("/actions")
def list_actions(
    entity: str = "devices",
    device_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Plugin actions, each carrying the device types it is enabled for.

    A page renders from this: a type page keeps the actions its own type
    allows, and the all-devices page keeps every action and checks each row
    against `device_types`. Nothing here is an authorisation — the invocation
    endpoint decides that — it is what the UI needs in order not to offer a
    button that would be refused.
    """
    # Plugin Deployments can become ready after the backend. Startup discovery
    # may therefore cache a connection-refused result; retry missing endpoints
    # on the page's action request instead of making the UI wait for the
    # background refresh interval (or an unrelated device scan).
    if len(registry.manifests()) < len(settings.plugin_endpoints):
        registry.refresh()
    types = list(db.scalars(select(DeviceType).where(DeviceType.enabled.is_(True))))
    allowed_by_type = {item.key: get_allowed_plugins(db, item.id) for item in types}
    fields_by_type = {item.key: get_effective_fields(db, item.id) for item in types}

    result = []
    for manifest in registry.manifests():
        plugin_id = manifest["id"]
        enabled_for = sorted(key for key, allowed in allowed_by_type.items() if plugin_id in allowed)
        if device_type is not None and device_type not in enabled_for:
            continue
        scoped_types = [device_type] if device_type is not None else enabled_for
        reasons_by_type = {
            key: reason for key in scoped_types
            if (reason := output_roles_blocker(manifest, fields_by_type[key]))
        }
        for action in manifest.get("actions", []):
            if action.get("entity") != entity:
                continue
            contributed = {
                **action,
                "plugin_id": plugin_id,
                "risk": action.get("risk", "normal"),
                "device_types": enabled_for,
                "unavailable_reasons_by_type": reasons_by_type,
            }
            if not enabled_for:
                contributed["unavailable_reason"] = (
                    "this plugin is not enabled for any device type"
                )
            elif scoped_types and len(reasons_by_type) == len(scoped_types):
                contributed["unavailable_reason"] = "; ".join(sorted(set(reasons_by_type.values())))
            result.append(contributed)
    return result


@router.get("/status")
def plugin_status(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Installed plugins, their health, and the device types each may act on."""
    types = list(db.scalars(select(DeviceType)))
    assignments = {item.key: get_allowed_plugins(db, item.id) for item in types}
    return [
        {
            **entry,
            "device_types": sorted(key for key, allowed in assignments.items() if entry["id"] in allowed),
            "missing_roles": sorted({
                role
                for item in types if entry["id"] in assignments.get(item.key, {})
                for role in missing_plugin_roles(db, entry["id"], item.id)
            }),
        }
        for entry in registry.status()
    ]


@router.post("/refresh")
def refresh_plugins(user: User = Depends(require_admin)):
    registry.refresh()
    return registry.status()


@router.post("/{plugin_id}/actions/{action_id}/invoke", status_code=202)
def invoke_action(
    plugin_id: str,
    action_id: str,
    body: InvokeIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    """Run one plugin action, after re-deciding whether it is allowed to run.

    The authorisation is repeated here in full. The listing endpoints exist so
    a UI can avoid offering an impossible button; they are not what makes an
    action safe. A direct request to reboot a phone is refused here even though
    no interface would ever have sent it.
    """
    ids = [i for i in ([body.entity_id] if body.entity_id else body.entity_ids) if i]
    manifest = registry.manifest(plugin_id)
    scope = next(
        (a.get("scope") for a in (manifest or {}).get("actions", []) if a.get("id") == action_id),
        None,
    )
    if scope == "collection":
        devices = list(db.scalars(select(Device)).all())
    else:
        devices = list(db.scalars(select(Device).where(Device.id.in_(ids))).all())
        if len(devices) != len(set(ids)):
            missing = sorted(set(ids) - {device.id for device in devices})
            raise HTTPException(status_code=404, detail=f"Device not found: {', '.join(missing)}")
    try:
        action, eligible = validate_plugin_invocation(db, plugin_id, action_id, devices, user)
    except PluginPolicyError as exc:
        log_action(db, user, "plugin.action.denied", body.entity, body.entity_id, {
            "plugin_id": plugin_id, "action_id": action_id,
            "reason": exc.message, "rejected": exc.reasons,
        }, request)
        db.commit()
        detail = {"message": exc.message, "rejected": exc.reasons} if exc.reasons else exc.message
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc

    entities = [
        plugin_payload_for(
            device, fields_for_device(db, device), action,
            output_roles=(manifest or {}).get("required_output_roles", [])
            + (manifest or {}).get("optional_output_roles", []),
        )
        for device in eligible
    ]
    for device, entity in zip(eligible, entities):
        assignment = db.scalar(select(DeviceTypePlugin).where(
            DeviceTypePlugin.device_type_id == device.device_type_id,
            DeviceTypePlugin.plugin_id == plugin_id,
        ))
        stored_configuration = assignment.configuration if (
            assignment is not None and isinstance(assignment.configuration, dict)
        ) else {}
        configuration, source = resolve_plugin_configuration(
            stored_configuration, device,
        )
        entity["_plugin_configuration"] = configuration
        entity["_plugin_configuration_source"] = source
    payload = {
        "action_id": action_id,
        "actor": {"id": user.id, "username": user.username, "role": user.role},
        "entities": entities,
    }
    try:
        result = registry.request(plugin_id, "POST", f"/plugin/v1/actions/{action_id}/invoke", payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Plugin invocation failed: {exc}") from exc
    log_action(db, user, "plugin.action.invoke", body.entity, body.entity_id, {
        "plugin_id": plugin_id, "action_id": action_id, "run_id": result.get("run_id"),
        "devices": [device.unique_id for device in eligible[:50]], "count": len(eligible),
    }, request)
    db.commit()
    return result


def _device_plugin_assignment(db: Session, plugin_id: str, device_id: str) -> tuple[Device, DeviceTypePlugin]:
    device = db.get(Device, device_id) or db.scalar(select(Device).where(Device.unique_id == device_id))
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    assignment = db.scalar(select(DeviceTypePlugin).where(
        DeviceTypePlugin.device_type_id == device.device_type_id,
        DeviceTypePlugin.plugin_id == plugin_id,
        DeviceTypePlugin.enabled.is_(True),
    ))
    if assignment is None:
        raise HTTPException(status_code=409, detail="Plugin is not enabled for this device type")
    return device, assignment


@router.get("/{plugin_id}/devices/{device_id}/configuration")
def get_device_plugin_configuration(
    plugin_id: str, device_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    device, assignment = _device_plugin_assignment(db, plugin_id, device_id)
    direct = (assignment.configuration or {}).get("_device_overrides", {}).get(device.id, {})
    effective, source = resolve_plugin_configuration(assignment.configuration or {}, device)
    return {"configuration": direct, "effective_configuration": effective, "source": source}


@router.put("/{plugin_id}/devices/{device_id}/configuration")
def put_device_plugin_configuration(
    plugin_id: str, device_id: str, body: DevicePluginConfigurationIn, request: Request,
    db: Session = Depends(get_db), user: User = Depends(require_admin),
):
    device, assignment = _device_plugin_assignment(db, plugin_id, device_id)
    if assignment.configuration_source == "yaml":
        raise HTTPException(status_code=409, detail="This plugin assignment is owned by YAML")
    if any(str(key).startswith("_") for key in body.configuration):
        raise HTTPException(status_code=422, detail="A device override may contain only plugin settings")
    validate_plugin_configuration(plugin_id, body.configuration)
    configuration = dict(assignment.configuration or {})
    overrides = dict(configuration.get("_device_overrides", {}))
    before = overrides.get(device.id)
    if body.configuration:
        overrides[device.id] = body.configuration
    else:
        overrides.pop(device.id, None)
    configuration["_device_overrides"] = overrides
    assignment.configuration = configuration
    log_action(db, user, "plugin.configuration.device", "device", device.id, {
        "plugin_id": plugin_id, "before": before, "after": body.configuration,
    }, request)
    db.commit()
    effective, source = resolve_plugin_configuration(configuration, device)
    return {"configuration": body.configuration, "effective_configuration": effective, "source": source}


@router.delete("/{plugin_id}/devices/{device_id}/configuration")
def delete_device_plugin_configuration(
    plugin_id: str, device_id: str, request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    device, assignment = _device_plugin_assignment(db, plugin_id, device_id)
    if assignment.configuration_source == "yaml":
        raise HTTPException(status_code=409, detail="This plugin assignment is owned by YAML")
    configuration = dict(assignment.configuration or {})
    overrides = dict(configuration.get("_device_overrides", {}))
    before = overrides.pop(device.id, None)
    configuration["_device_overrides"] = overrides
    assignment.configuration = configuration
    log_action(db, user, "plugin.configuration.device", "device", device.id, {
        "plugin_id": plugin_id, "before": before, "after": None,
    }, request)
    db.commit()
    effective, source = resolve_plugin_configuration(configuration, device)
    return {"configuration": {}, "effective_configuration": effective, "source": source}


@router.get("/runs/active")
def active_runs(user: User = Depends(get_current_user)):
    runs = []
    for manifest in registry.manifests():
        plugin_id = manifest["id"]
        try:
            payload = registry.request(plugin_id, "GET", "/plugin/v1/runs")
            runs.extend({**run, "plugin_id": plugin_id} for run in payload.get("runs", []))
        except Exception:  # A temporarily unavailable plugin must not hide the others.
            continue
    return runs


@router.get("/{plugin_id}/runs/{run_id}")
def run_status(plugin_id: str, run_id: str, user: User = Depends(get_current_user)):
    try:
        return registry.request(plugin_id, "GET", f"/plugin/v1/runs/{run_id}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Plugin status failed: {exc}") from exc


@router.delete("/{plugin_id}/runs/{run_id}")
def cancel_run(plugin_id: str, run_id: str, user: User = Depends(require_write)):
    try:
        return registry.request(plugin_id, "DELETE", f"/plugin/v1/runs/{run_id}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Plugin cancellation failed: {exc}") from exc


def _store_artifact(db: Session, plugin_id: str, device_id: str, artifact_type: str, body: ArtifactIn):
    latest = db.scalar(select(func.max(PluginArtifact.version)).where(
        PluginArtifact.plugin_id == plugin_id,
        PluginArtifact.entity_type == "devices",
        PluginArtifact.entity_id == device_id,
        PluginArtifact.artifact_type == artifact_type,
    )) or 0
    artifact = PluginArtifact(
        plugin_id=plugin_id, entity_type="devices", entity_id=device_id,
        artifact_type=artifact_type, version=latest + 1, schema_version=body.schema_version,
        payload=body.payload, metadata_json=body.metadata,
    )
    db.add(artifact)
    return artifact


def _artifact_exists(db: Session, plugin_id: str, device_id: str, artifact_type: str) -> bool:
    return db.scalar(select(PluginArtifact.id).where(
        PluginArtifact.plugin_id == plugin_id,
        PluginArtifact.entity_type == "devices",
        PluginArtifact.entity_id == device_id,
        PluginArtifact.artifact_type == artifact_type,
    ).limit(1)) is not None


def _preserve_repeat_artifact(selection: str | None, artifact_type: str, exists: bool) -> bool:
    return selection == "saved_recipe" and artifact_type == "discovery_recipe" and exists


@host_router.post("/device-info/device-results")
def device_info_results(
    body: DiscoveryResultIn,
    db: Session = Depends(get_db),
    plugin_id: str = Depends(_plugin_identity),
):
    if plugin_id != "device-info-agent":
        raise HTTPException(status_code=403, detail="Plugin lacks device.discovery.write")
    prior = db.scalar(select(PluginResultReceipt).where(
        PluginResultReceipt.plugin_id == plugin_id, PluginResultReceipt.run_id == body.run_id
    ))
    if prior:
        return prior.result
    device = db.get(Device, body.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    by_role = role_map(field for field in fields_for_device(db, device) if field.visible)
    accepted, conflicts, rejected = {}, {}, {}
    old = dict(device._data or {})
    for role, finding in body.findings.items():
        field = by_role.get(role)
        if role not in {
            "discovery_hardware", "discovery_firmware",
            "discovery_lan_mac", "discovery_wan_mac",
        } or not field:
            rejected[role] = "role is not configured"
            continue
        current = (device._data or {}).get(field.key)
        if current != finding.starting_value:
            conflicts[role] = {"current": current, "starting": finding.starting_value}
            continue
        value = str(finding.value).strip()
        if role in {"discovery_lan_mac", "discovery_wan_mac"}:
            compact = "".join(ch for ch in value.lower() if ch in "0123456789abcdef")
            if len(compact) != 12:
                rejected[role] = "invalid MAC address"
                continue
            value = ":".join(compact[i:i + 2] for i in range(0, 12, 2))
        if not value or len(value) > 500:
            rejected[role] = "invalid value"
            continue
        document = dict(device._data or {})
        document[field.key] = value
        device._data = document
        accepted[role] = {"field": field.key, "value": value, "confidence": finding.confidence, "source": finding.source}
    preserved_artifacts = []
    repeat_run = body.model.get("selection") == "saved_recipe"
    for artifact_type, artifact in body.artifacts.items():
        exists = (
            repeat_run
            and artifact_type == "discovery_recipe"
            and _artifact_exists(db, plugin_id, device.id, artifact_type)
        )
        if _preserve_repeat_artifact(body.model.get("selection"), artifact_type, exists):
            preserved_artifacts.append(artifact_type)
            continue
        _store_artifact(db, plugin_id, device.id, artifact_type, ArtifactIn(
            schema_version=artifact.schema_version,
            payload=artifact.payload,
            metadata={**artifact.metadata, "model": body.model, "run_id": body.run_id},
        ))
    device.updated_at = utcnow()
    result = {
        "accepted": accepted,
        "conflicts": conflicts,
        "rejected": rejected,
        "preserved_artifacts": preserved_artifacts,
    }
    db.add(PluginResultReceipt(plugin_id=plugin_id, run_id=body.run_id, device_id=device.id, result=result))
    log_action(db, None, "plugin.device_info.result", "device", device.id, {
        **result, "plugin_id": plugin_id, "run_id": body.run_id, "model": body.model,
        "diff": field_diff(old, device._data or {}),
    })
    db.commit()
    return result


@host_router.post("/network-scan/device-results")
def network_scan_results(
    body: ScanResultIn,
    db: Session = Depends(get_db),
    plugin_id: str = Depends(_plugin_identity),
):
    if plugin_id != "network-scan":
        raise HTTPException(status_code=403, detail="Plugin lacks device.connectivity.write")
    prior = db.scalar(select(PluginResultReceipt).where(
        PluginResultReceipt.plugin_id == plugin_id, PluginResultReceipt.run_id == body.run_id
    ))
    if prior:
        return prior.result
    updated, stale = [], []
    for item in body.items:
        device = db.get(Device, item.device_id)
        if not device:
            continue
        roles = role_map(fields_for_device(db, device))
        address_fields = {
            roles[role].key for role in ("scan_address_wan", "scan_address_lan") if role in roles
        }
        current = {key: (device._data or {}).get(key) for key in address_fields}
        if current != {key: item.starting_addresses.get(key) for key in address_fields}:
            stale.append(item.device_id)
            continue
        if item.probed:
            device.online_status = item.online
            device.last_scanned_at = utcnow()
            if item.online:
                device.last_seen_online = utcnow()
            updated.append(item.device_id)
    result = {"updated": updated, "stale": stale}
    db.add(PluginResultReceipt(plugin_id=plugin_id, run_id=body.run_id, result=result))
    log_action(db, None, "plugin.network_scan.result", "device", None, {**result, "run_id": body.run_id})
    db.commit()
    return result


@host_router.post("/device-reboot/device-results")
def reboot_results(
    body: RebootResultIn,
    db: Session = Depends(get_db),
    plugin_id: str = Depends(_plugin_identity),
):
    if plugin_id != "device-reboot":
        raise HTTPException(status_code=403, detail="Plugin lacks device reboot write access")
    prior = db.scalar(select(PluginResultReceipt).where(
        PluginResultReceipt.plugin_id == plugin_id, PluginResultReceipt.run_id == body.run_id
    ))
    if prior:
        return prior.result
    device = db.get(Device, body.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if not body.verified_online:
        raise HTTPException(status_code=422, detail="Reboot was not verified")
    _store_artifact(db, plugin_id, device.id, "reboot_recipe", ArtifactIn(
        payload={"steps": body.successful_steps},
        metadata={"method": body.method, "model": body.model, "run_id": body.run_id},
    ))
    result = {"stored": True, "verified_online": True, "method": body.method}
    db.add(PluginResultReceipt(
        plugin_id=plugin_id, run_id=body.run_id, device_id=device.id, result=result,
    ))
    log_action(db, None, "plugin.device_reboot.result", "device", device.id, {
        **result, "plugin_id": plugin_id, "run_id": body.run_id, "model": body.model,
    })
    db.commit()
    return result


@host_router.get("/network-scan/devices")
def network_scan_devices(
    db: Session = Depends(get_db), plugin_id: str = Depends(_plugin_identity),
):
    if plugin_id != "network-scan":
        raise HTTPException(status_code=403, detail="Plugin lacks scan snapshot access")
    # The scheduled sweep reads this instead of being handed a selection, so
    # the allowlist has to be applied here too — otherwise a timer would reach
    # devices a person could not.
    manifest = registry.manifest(plugin_id) or {}
    action = next((item for item in manifest.get("actions", []) if item.get("scope") == "collection"), {})
    allowed_types = {
        item.id for item in db.scalars(select(DeviceType))
        if plugin_id in get_allowed_plugins(db, item.id)
    }
    snapshot = []
    for device in db.scalars(select(Device)).all():
        if device.device_type_id not in allowed_types:
            continue
        snapshot.append(plugin_payload_for(device, fields_for_device(db, device), action,
                           output_roles=(manifest or {}).get("required_output_roles", [])))
    return snapshot


@host_router.get("/artifacts/{plugin_id}/devices/{device_id}/{artifact_type}")
def get_artifact(
    plugin_id: str, device_id: str, artifact_type: str,
    db: Session = Depends(get_db), identity: str = Depends(_plugin_identity),
):
    if identity != plugin_id:
        raise HTTPException(status_code=403, detail="Artifact namespace denied")
    artifact = db.scalar(select(PluginArtifact).where(
        PluginArtifact.plugin_id == plugin_id, PluginArtifact.entity_type == "devices",
        PluginArtifact.entity_id == device_id, PluginArtifact.artifact_type == artifact_type,
    ).order_by(PluginArtifact.version.desc()))
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"schema_version": artifact.schema_version, "version": artifact.version,
            "payload": artifact.payload, "metadata": artifact.metadata_json}


MANAGEABLE_DEVICE_ARTIFACTS = {
    ("device-info-agent", "discovery_recipe"),
    ("device-reboot", "reboot_recipe"),
}


@router.get("/device-artifacts/{device_id}")
def list_device_artifacts(
    device_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    if not db.get(Device, device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    artifacts = db.scalars(select(PluginArtifact).where(
        PluginArtifact.entity_type == "devices", PluginArtifact.entity_id == device_id,
    ).order_by(PluginArtifact.plugin_id, PluginArtifact.artifact_type, PluginArtifact.version.desc())).all()
    latest = {}
    for artifact in artifacts:
        key = (artifact.plugin_id, artifact.artifact_type)
        if key in MANAGEABLE_DEVICE_ARTIFACTS and key not in latest:
            latest[key] = artifact
    return [{
        "plugin_id": artifact.plugin_id,
        "artifact_type": artifact.artifact_type,
        "schema_version": artifact.schema_version,
        "version": artifact.version,
        "payload": artifact.payload,
        "metadata": artifact.metadata_json,
        "updated_at": artifact.updated_at,
    } for artifact in latest.values()]


def _managed_artifact(plugin_id: str, artifact_type: str):
    if (plugin_id, artifact_type) not in MANAGEABLE_DEVICE_ARTIFACTS:
        raise HTTPException(status_code=404, detail="Plugin artifact is not user-manageable")


@router.put("/device-artifacts/{device_id}/{plugin_id}/{artifact_type}")
def update_device_artifact(
    device_id: str, plugin_id: str, artifact_type: str, body: ArtifactIn,
    db: Session = Depends(get_db), user: User = Depends(require_write),
):
    _managed_artifact(plugin_id, artifact_type)
    if not db.get(Device, device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    artifact = _store_artifact(db, plugin_id, device_id, artifact_type, body)
    log_action(db, user, "plugin.artifact.update", "device", device_id, {
        "plugin_id": plugin_id, "artifact_type": artifact_type, "version": artifact.version,
    })
    db.commit()
    db.refresh(artifact)
    return {
        "plugin_id": plugin_id, "artifact_type": artifact_type,
        "schema_version": artifact.schema_version, "version": artifact.version,
        "payload": artifact.payload, "metadata": artifact.metadata_json,
        "updated_at": artifact.updated_at,
    }


@router.delete("/device-artifacts/{device_id}/{plugin_id}/{artifact_type}", status_code=204)
def delete_device_artifact(
    device_id: str, plugin_id: str, artifact_type: str,
    db: Session = Depends(get_db), user: User = Depends(require_write),
):
    _managed_artifact(plugin_id, artifact_type)
    if not db.get(Device, device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    db.execute(delete(PluginArtifact).where(
        PluginArtifact.plugin_id == plugin_id,
        PluginArtifact.entity_type == "devices",
        PluginArtifact.entity_id == device_id,
        PluginArtifact.artifact_type == artifact_type,
    ))
    log_action(db, user, "plugin.artifact.delete", "device", device_id, {
        "plugin_id": plugin_id, "artifact_type": artifact_type,
    })
    db.commit()
