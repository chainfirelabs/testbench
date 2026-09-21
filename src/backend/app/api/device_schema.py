"""Administering the device schema: field definitions, layouts, plugin policy.

Two routers live here. `/device-fields` is the catalog of reusable definitions;
`/device-schema` is where those definitions are assigned — globally or to one
device type — and where the published configuration is validated and released.

Every mutation publishes a revision, which is what invalidates the caches every
reader keys off. Reading a published schema needs only an authenticated user;
changing one needs an administrator, and every change is audited with what it
was before and what it became.
"""

from typing import Literal

import yaml

from ..services.plugin_configuration import validate_plugin_configuration
from ..services.ai_configuration import validate_ai_profile_references

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import (
    Device,
    DeviceFieldAssignment,
    DeviceFieldDefinition,
    DeviceSchemaRevision,
    DeviceType,
    DeviceTypePlugin,
    User,
)
from ..services.audit import log_action
from ..services.device_schema import (
    FIELD_TYPES,
    KEY_RE,
    LOCKED_VISIBLE,
    PROTECTED_SYSTEM_FIELDS,
    WEB_ADDRESS_ROLES,
    RESERVED_DOCUMENT_KEYS,
    analyze_schema,
    assignment_payload,
    current_revision,
    definition_payload,
    get_effective_fields,
    index_status,
    invalidate_cache,
    missing_plugin_roles,
    plugin_assignments,
    publish_revision,
    refresh_managed_indexes,
    schema_payload,
    storage_for,
)
from ..services.device_schema_yaml import (
    DocumentError,
    additive_import_plan,
    apply_additive_import,
    export_document,
    parse_uploaded_document,
    reconcile_from_settings,
    status as yaml_status,
)
from ..services.entity_fields import ensure_entity_field_indexes
from ..services.io import download_response
from ..services.plugin_host import registry
from .deps import get_current_user, require_schema_manage

fields_router = APIRouter(prefix="/device-fields", tags=["device schema"])
router = APIRouter(prefix="/device-schema", tags=["device schema"])


# ------------------------------------------------------------ request models

class FieldDefinitionIn(BaseModel):
    key: str
    label: str
    field_type: str = "text"
    description: str | None = None
    options: list[str] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)
    default_value: object | None = None
    sensitive: bool = False
    indexed: bool = False
    unique_value: bool = False
    # Unset means "decide from the role": a field carrying a scan address is a
    # web page often enough to link by default, and an administrator adding
    # `lan_ip` from the schema editor should not have to tick a second box to
    # get what every other installation already has. Pass it explicitly to
    # override in either direction.
    opens_web_page: bool | None = None
    # The installation's default for that link. A device may override both in
    # its own `link_overrides`; these are what it overrides.
    link_scheme: Literal["http", "https"] = "http"
    link_port: int | None = Field(default=None, ge=1, le=65535)
    plugin_role: str | None = None
    # Convenience: create the field and make it global in one call, which is
    # what "add a column everyone should have" actually means.
    add_to_global: bool = True


class FieldDefinitionUpdate(BaseModel):
    label: str | None = None
    field_type: str | None = None
    description: str | None = None
    options: list[str] | None = None
    validation: dict | None = None
    default_value: object | None = None
    sensitive: bool | None = None
    indexed: bool | None = None
    unique_value: bool | None = None
    opens_web_page: bool | None = None
    link_scheme: Literal["http", "https"] | None = None
    # Explicit null clears the port back to the scheme's own, so the update
    # distinguishes "leave it alone" (absent) from "no port" (null) the way
    # every other nullable override on this model does.
    link_port: int | None = Field(default=None, ge=1, le=65535)
    plugin_role: str | None = None
    enabled: bool | None = None


class AssignmentIn(BaseModel):
    field_key: str
    visible: bool | None = None
    list_visible: bool | None = None
    required: bool | None = None
    writable: bool | None = None
    position: int | None = None
    label_override: str | None = None
    description_override: str | None = None
    validation_override: dict | None = None
    excluded: bool = False


class AssignmentsIn(BaseModel):
    """The complete assignment set for one scope. Anything absent is removed."""

    assignments: list[AssignmentIn]
    note: str | None = None


class PluginAssignmentIn(BaseModel):
    plugin_id: str
    enabled: bool = True
    configuration: dict = Field(default_factory=dict)


class PluginAssignmentsIn(BaseModel):
    plugins: list[PluginAssignmentIn]


# ------------------------------------------------------------------ helpers

def _definition_or_404(db: Session, field_id: str) -> DeviceFieldDefinition:
    definition = db.get(DeviceFieldDefinition, field_id) or db.scalar(
        select(DeviceFieldDefinition).where(DeviceFieldDefinition.key == field_id)
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="Device field not found")
    return definition


def _type_or_404(db: Session, key_or_id: str) -> DeviceType:
    item = db.get(DeviceType, key_or_id) or db.scalar(select(DeviceType).where(DeviceType.key == key_or_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Device type not found")
    return item


def _require_gui_owned(source: str, what: str) -> None:
    if source == "yaml":
        raise HTTPException(
            status_code=409,
            detail=f"{what} is owned by the DeviceSchema document and cannot be edited here",
        )


def _usage(db: Session, definition: DeviceFieldDefinition) -> dict:
    """Where a definition is assigned, and how many devices hold a value.

    Shown before a destructive edit, because "delete this field" and "delete
    this field, which forty devices have data in" are different decisions.
    """
    scopes = db.execute(
        select(DeviceType.key)
        .join(DeviceFieldAssignment, DeviceFieldAssignment.device_type_id == DeviceType.id)
        .where(DeviceFieldAssignment.field_definition_id == definition.id)
    ).scalars().all()
    is_global = db.scalar(select(DeviceFieldAssignment.id).where(
        DeviceFieldAssignment.field_definition_id == definition.id,
        DeviceFieldAssignment.device_type_id.is_(None),
    )) is not None
    populated = 0
    if storage_for(definition.key) == "data":
        populated = db.scalar(
            select(func.count()).select_from(Device).where(
                Device._data[definition.key].astext.isnot(None),
                Device._data[definition.key].astext != "",
            )
        ) or 0
    return {"global": is_global, "device_types": sorted(scopes), "devices_with_values": populated}


def _apply_assignments(
    db: Session,
    device_type: DeviceType | None,
    body: AssignmentsIn,
) -> dict:
    """Replace one scope's assignment set, keeping what must not be removed.

    A whole-set PUT rather than a row at a time: the editor shows a layout and
    saves a layout, and reordering fields one PATCH at a time would leave the
    schema briefly saying something nobody asked for.
    """
    type_id = device_type.id if device_type else None
    definitions = {d.key: d for d in db.scalars(select(DeviceFieldDefinition))}
    key_of = {definition.id: key for key, definition in definitions.items()}
    existing = {
        row.field_definition_id: row
        for row in db.scalars(select(DeviceFieldAssignment).where(
            DeviceFieldAssignment.device_type_id.is_(None) if type_id is None
            else DeviceFieldAssignment.device_type_id == type_id
        ))
    }
    before = {
        key_of[row.field_definition_id]: assignment_payload(row, key_of[row.field_definition_id])
        for row in existing.values() if row.field_definition_id in key_of
    }

    seen: set[str] = set()
    for index, item in enumerate(body.assignments):
        definition = definitions.get(item.field_key)
        if definition is None:
            raise HTTPException(status_code=422, detail=f"Unknown device field: {item.field_key}")
        if item.excluded:
            if type_id is None:
                raise HTTPException(status_code=422, detail="A global assignment cannot exclude itself")
            if definition.protected_system_field:
                raise HTTPException(
                    status_code=422,
                    detail=f"{definition.label} is a protected system field and cannot be excluded",
                )
            if not settings.device_schema_allow_global_exclusions:
                raise HTTPException(
                    status_code=422,
                    detail="Excluding a global field is disabled on this installation",
                )
        if item.required and item.writable is False:
            raise HTTPException(
                status_code=422,
                detail=f"{definition.label} cannot be both read-only and required",
            )
        if item.visible is False and definition.key in LOCKED_VISIBLE:
            raise HTTPException(
                status_code=422, detail=f"{definition.label} cannot be hidden",
            )
        row = existing.get(definition.id)
        if row is None:
            row = DeviceFieldAssignment(field_definition_id=definition.id, device_type_id=type_id)
            db.add(row)
        _require_gui_owned(row.configuration_source, f"The {definition.label} assignment")
        row.visible = item.visible
        row.list_visible = item.list_visible
        row.required = item.required
        row.writable = item.writable
        row.position = item.position if item.position is not None else index * 10
        row.label_override = item.label_override or None
        row.description_override = item.description_override or None
        row.validation_override = item.validation_override or None
        row.excluded = item.excluded
        row.configuration_source = "gui"
        seen.add(definition.id)

    for definition_id, row in existing.items():
        if definition_id in seen:
            continue
        definition = definitions.get(key_of.get(definition_id, ""))
        if type_id is None and definition is not None and definition.protected_system_field:
            # A protected field always has a global assignment. Dropping it
            # would remove a column the application itself reads.
            continue
        if row.configuration_source == "yaml":
            continue
        db.delete(row)
    db.flush()
    return before


# ------------------------------------------------------- field definitions

@fields_router.get("")
def list_device_fields(
    include_disabled: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every reusable device field definition, with where it is in use."""
    q = select(DeviceFieldDefinition)
    if not include_disabled:
        q = q.where(DeviceFieldDefinition.enabled.is_(True))
    return [
        definition_payload(definition, _usage(db, definition))
        for definition in db.scalars(q.order_by(DeviceFieldDefinition.key))
    ]


@fields_router.post("", status_code=201)
def create_device_field(
    body: FieldDefinitionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    key = body.key.strip().lower()
    if not KEY_RE.fullmatch(key):
        raise HTTPException(
            status_code=422,
            detail="Field key must start with a letter and contain lowercase letters, numbers, or underscores",
        )
    if key in RESERVED_DOCUMENT_KEYS:
        # These name parts of the request envelope — identity, the device type,
        # ownership, timestamps. A field by the same name could never be read
        # back, because the envelope wins everywhere it is parsed.
        raise HTTPException(
            status_code=422,
            detail=f"'{key}' is reserved by the device envelope and cannot be a field key",
        )
    if body.field_type not in FIELD_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported field type: {body.field_type}")
    if db.scalar(select(DeviceFieldDefinition.id).where(DeviceFieldDefinition.key == key)):
        raise HTTPException(status_code=409, detail=f"Device field '{key}' already exists")
    if not body.label.strip():
        raise HTTPException(status_code=422, detail="Label must not be blank")
    definition = DeviceFieldDefinition(
        key=key, label=body.label.strip(), field_type=body.field_type,
        description=body.description, options=body.options, validation=body.validation,
        default_value=body.default_value, sensitive=body.sensitive,
        # A unique field is always indexed: the index is what enforces it.
        indexed=body.indexed or body.unique_value, unique_value=body.unique_value,
        opens_web_page=(
            body.plugin_role in WEB_ADDRESS_ROLES
            if body.opens_web_page is None
            else body.opens_web_page
        ),
        link_scheme=body.link_scheme, link_port=body.link_port,
        plugin_role=body.plugin_role or None, protected_system_field=False,
        enabled=True, configuration_source="gui",
    )
    db.add(definition)
    db.flush()
    if body.add_to_global:
        position = (db.scalar(select(func.max(DeviceFieldAssignment.position)).where(
            DeviceFieldAssignment.device_type_id.is_(None))) or 0) + 10
        db.add(DeviceFieldAssignment(
            field_definition_id=definition.id, device_type_id=None,
            visible=True, required=False, writable=True, position=position,
            configuration_source="gui",
        ))
    publish_revision(db, source="gui", note=f"Created device field {key}", user=user)
    log_action(db, user, "device_field.create", "device_field", definition.id,
               body.model_dump(mode="json"), request)
    db.commit()
    refresh_managed_indexes(db)
    return definition_payload(definition, _usage(db, definition))


@fields_router.patch("/{field_id}")
def update_device_field(
    field_id: str,
    body: FieldDefinitionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    definition = _definition_or_404(db, field_id)
    _require_gui_owned(definition.configuration_source, f"The {definition.label} field")
    updates = body.model_dump(exclude_unset=True)
    if definition.protected_system_field:
        # Presentation is the administrator's; meaning is the application's.
        forbidden = sorted({"field_type", "plugin_role", "enabled"} & set(updates))
        if forbidden:
            raise HTTPException(
                status_code=422,
                detail=f"{definition.label} is a protected system field; {', '.join(forbidden)} cannot be changed",
            )
    if updates.get("field_type") and updates["field_type"] not in FIELD_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported field type: {updates['field_type']}")
    if "label" in updates and not (updates["label"] or "").strip():
        raise HTTPException(status_code=422, detail="Label must not be blank")

    before = {key: getattr(definition, key) for key in updates}
    for key, value in updates.items():
        setattr(definition, key, value.strip() if key == "label" and value else value)
    if definition.unique_value:
        definition.indexed = True
    db.flush()

    # Analysed against the data that already exists, before it becomes the
    # published truth: narrowing a type or adding a uniqueness rule that the
    # stored values contradict is refused here rather than at the next save.
    report = analyze_schema(db, uncached=True)
    if report["errors"]:
        db.rollback()
        invalidate_cache()
        raise HTTPException(status_code=422, detail={
            "message": f"{definition.label} cannot be changed as asked", **report,
        })
    publish_revision(db, source="gui", note=f"Updated device field {definition.key}", user=user)
    log_action(db, user, "device_field.update", "device_field", definition.id,
               {"before": before, "after": updates}, request)
    db.commit()
    refresh_managed_indexes(db)
    return definition_payload(definition, _usage(db, definition))


@fields_router.delete("/{field_id}", status_code=204)
def delete_device_field(
    field_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    """Remove a definition entirely. Stored values are not touched.

    Refused while devices still hold values for it: disabling a field takes it
    off every layout without destroying anything, and that is nearly always
    what was actually wanted.
    """
    definition = _definition_or_404(db, field_id)
    _require_gui_owned(definition.configuration_source, f"The {definition.label} field")
    if definition.protected_system_field:
        raise HTTPException(
            status_code=422, detail=f"{definition.label} is a protected system field and cannot be deleted"
        )
    usage = _usage(db, definition)
    if usage["devices_with_values"]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{definition.label} holds values on {usage['devices_with_values']} device(s). "
                "Disable it instead — hiding a field never deletes what devices already store."
            ),
        )
    publish_revision(db, source="gui", note=f"Deleted device field {definition.key}", user=user)
    log_action(db, user, "device_field.delete", "device_field", definition.id,
               {"snapshot": definition_payload(definition, usage)}, request)
    db.delete(definition)
    db.commit()
    refresh_managed_indexes(db)


# ------------------------------------------------------------ published views

@router.get("")
def schema_overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Everything a client needs to render every device page in one request."""
    types = list(db.scalars(select(DeviceType).order_by(DeviceType.position, DeviceType.label)))
    return {
        "revision": current_revision(db),
        "reconciliation": settings.device_schema_reconciliation,
        "yaml_configured": bool(settings.device_schema_path),
        "allow_global_exclusions": settings.device_schema_allow_global_exclusions,
        "global": schema_payload(db, None),
        "types": {item.key: schema_payload(db, item) for item in types if item.enabled},
    }


@router.get("/global")
def get_global_schema(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    payload = schema_payload(db, None)
    payload["assignments"] = _scope_assignments(db, None)
    return payload


@router.get("/types/{type_key}")
def get_type_schema(type_key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if type_key == "uncategorized":
        # Devices with no type see the global schema and no plugins at all.
        payload = schema_payload(db, None)
        payload["device_type"] = {"id": None, "key": "uncategorized", "label": "Uncategorized"}
        return payload
    item = _type_or_404(db, type_key)
    payload = schema_payload(db, item)
    payload["assignments"] = _scope_assignments(db, item.id)
    return payload


def _scope_assignments(db: Session, type_id: str | None) -> list[dict]:
    rows = db.execute(
        select(DeviceFieldAssignment, DeviceFieldDefinition.key)
        .join(DeviceFieldDefinition, DeviceFieldDefinition.id == DeviceFieldAssignment.field_definition_id)
        .where(DeviceFieldAssignment.device_type_id.is_(None) if type_id is None
               else DeviceFieldAssignment.device_type_id == type_id)
        .order_by(DeviceFieldAssignment.position)
    ).all()
    return [assignment_payload(assignment, key) for assignment, key in rows]


@router.put("/global")
def put_global_schema(
    body: AssignmentsIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    """Set the fields every device type inherits, now and in future."""
    before = _apply_assignments(db, None, body)
    return _publish(db, user, request, "device_schema.global", None, before, body.note)


@router.put("/types/{type_id}")
def put_type_schema(
    type_id: str,
    body: AssignmentsIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    item = _type_or_404(db, type_id)
    _require_gui_owned(item.configuration_source, f"Device type '{item.key}'")
    before = _apply_assignments(db, item, body)
    return _publish(db, user, request, "device_schema.type", item, before, body.note)


def _publish(db: Session, user: User, request: Request, action: str,
             device_type: DeviceType | None, before: dict, note: str | None):
    report = analyze_schema(db, uncached=True)
    if report["errors"]:
        db.rollback()
        invalidate_cache()
        raise HTTPException(status_code=422, detail={"message": "The schema cannot be published", **report})
    scope = device_type.id if device_type else None
    revision = publish_revision(
        db, source="gui", user=user,
        note=note or ("Updated global fields" if device_type is None else f"Updated {device_type.key} fields"),
        summary={"scope": device_type.key if device_type else "global"},
    )
    after = {item["field_key"]: item for item in _scope_assignments(db, scope)}
    log_action(db, user, action, "device_schema", scope,
               {"before": before, "after": after, "revision": revision}, request)
    db.commit()
    refresh_managed_indexes(db)
    payload = schema_payload(db, device_type)
    payload["assignments"] = _scope_assignments(db, scope)
    payload["warnings"] = report["warnings"]
    return payload


# ------------------------------------------------------------ plugin policy

@router.get("/types/{type_id}/plugins")
def get_type_plugins(type_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Installed plugins and this type's decision about each one.

    Missing semantic roles are reported alongside, so an administrator finds
    out that Reboot needs a username and a password while they are enabling it
    rather than when somebody presses the button.
    """
    item = _type_or_404(db, type_id)
    assigned = {row.plugin_id: row for row in plugin_assignments(db, item.id)}
    installed = {manifest["id"]: manifest for manifest in registry.manifests()}
    result = []
    for plugin_id in sorted(set(installed) | set(assigned)):
        manifest = installed.get(plugin_id, {})
        row = assigned.get(plugin_id)
        actions = [action for action in manifest.get("actions", []) if action.get("entity") == "devices"]
        result.append({
            "plugin_id": plugin_id,
            "label": manifest.get("label", plugin_id),
            "version": manifest.get("version"),
            "installed": plugin_id in installed,
            "enabled": bool(row and row.enabled),
            "configuration": (row.configuration if row else {}) or {},
            "configuration_defaults": manifest.get("configuration_defaults", {}),
            "ai_configuration": manifest.get("ai_configuration", {}),
            "configuration_source": row.configuration_source if row else None,
            "missing_roles": missing_plugin_roles(db, plugin_id, item.id),
            "actions": [
                {
                    "id": action.get("id"), "label": action.get("label"),
                    "risk": action.get("risk", "normal"),
                    "scope": action.get("scope"),
                    "allow_global_assignment": action.get("allow_global_assignment", True),
                }
                for action in actions
            ],
        })
    return {
        "device_type": item.key,
        "rule_fields": [
            {"key": field.key, "label": field.label}
            for field in get_effective_fields(db, item.id)
        ],
        "plugins": result,
    }


def _assign_recommended_plugin_fields(
    db: Session, device_type: DeviceType | None, manifest: dict, *, source: str = "gui",
) -> list[str]:
    """Add a manifest's preferred fields without changing existing choices."""
    added: list[str] = []
    type_id = device_type.id if device_type else None
    effective_fields = get_effective_fields(db, type_id)
    visible_definition_ids = {field.definition_id for field in effective_fields if field.visible}
    position = max((field.position for field in effective_fields), default=0) + 10
    for raw in manifest.get("recommended_fields", []):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", "")).strip()
        role = str(raw.get("role", "")).strip() or None
        definition = None
        if role:
            definition = db.scalar(select(DeviceFieldDefinition).where(
                DeviceFieldDefinition.plugin_role == role,
            ))
        if definition is None and key:
            definition = db.scalar(select(DeviceFieldDefinition).where(
                DeviceFieldDefinition.key == key,
            ))
        if definition is None:
            field_type = str(raw.get("type", "text"))
            if not KEY_RE.fullmatch(key) or field_type not in FIELD_TYPES:
                continue
            definition = DeviceFieldDefinition(
                key=key,
                label=str(raw.get("label") or key.replace("_", " ").title()),
                field_type=field_type,
                description=raw.get("description"),
                options=list(raw.get("options") or []),
                validation=dict(raw.get("validation") or {}),
                sensitive=bool(raw.get("sensitive", False)),
                indexed=bool(raw.get("indexed", False) or raw.get("unique", False)),
                unique_value=bool(raw.get("unique", False)),
                opens_web_page=bool(raw.get("opens_web_page", role in WEB_ADDRESS_ROLES)),
                link_scheme="http", link_port=None,
                plugin_role=role,
                protected_system_field=key in PROTECTED_SYSTEM_FIELDS,
                enabled=True,
                configuration_source="gui",
            )
            db.add(definition)
            db.flush()
        elif not definition.enabled:
            # A disabled catalog entry is an explicit administrator choice.
            # Do not resurrect it or attempt to create a duplicate key.
            continue
        if definition.id in visible_definition_ids:
            continue
        assignment = db.scalar(select(DeviceFieldAssignment).where(
            DeviceFieldAssignment.field_definition_id == definition.id,
            DeviceFieldAssignment.device_type_id.is_(None) if type_id is None
            else DeviceFieldAssignment.device_type_id == type_id,
        ))
        if assignment is not None:
            # Explicit visibility is an administrator's decision. Null merely
            # inherited a hidden global field, so it is safe to make useful.
            if assignment.visible is None or (
                assignment.visible is False and assignment.configuration_source == "system"
            ):
                assignment.visible = True
                if assignment.list_visible is None:
                    assignment.list_visible = True
                added.append(definition.key)
            continue
        db.add(DeviceFieldAssignment(
            field_definition_id=definition.id,
            device_type_id=type_id,
            visible=True,
            list_visible=True,
            required=False,
            writable=bool(raw.get("writable", True)),
            position=position,
            configuration_source=source,
        ))
        position += 10
        added.append(definition.key)
    return added


def reconcile_installed_plugin_fields(db: Session) -> dict[str, list[str]]:
    """Put installed plugins' declared fields in the global database layout."""
    if settings.device_schema_path and settings.device_schema_reconciliation == "authoritative":
        return {}
    # Backend replicas start together. Serialize this small catalog mutation so
    # they reuse one definition instead of racing on its unique key.
    db.execute(select(func.pg_advisory_xact_lock(84726319)))
    added = {}
    for manifest in registry.manifests():
        fields = _assign_recommended_plugin_fields(db, None, manifest, source="system")
        if fields:
            added[manifest["id"]] = fields
    if not added:
        return {}
    db.flush()
    publish_revision(db, source="system", note="Added installed plugin fields",
                     summary={"installed_plugin_fields_added": added})
    db.commit()
    refresh_managed_indexes(db)
    return added


@router.put("/types/{type_id}/plugins")
def put_type_plugins(
    type_id: str,
    body: PluginAssignmentsIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    """Set which plugins may act on this device type.

    An allowlist: whatever is not here is denied, and the invocation endpoint
    repeats the check rather than trusting that the UI hid the button.
    """
    item = _type_or_404(db, type_id)
    _require_gui_owned(item.configuration_source, f"Device type '{item.key}'")
    existing = {row.plugin_id: row for row in plugin_assignments(db, item.id)}
    before = {plugin_id: {"enabled": row.enabled, "configuration": row.configuration}
              for plugin_id, row in existing.items()}
    seen: set[str] = set()
    recommended_fields: dict[str, list[str]] = {}
    for entry in body.plugins:
        try:
            validate_plugin_configuration(entry.plugin_id, entry.configuration)
            validate_ai_profile_references(db, entry.configuration)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        valid_field_keys = {field.key for field in get_effective_fields(db, item.id)}
        unknown_rule_fields = sorted({
            rule.get("field_key") for rule in entry.configuration.get("_rules", [])
            if rule.get("field_key") not in valid_field_keys
        })
        if unknown_rule_fields:
            raise HTTPException(status_code=422, detail=(
                "Plugin rules reference fields outside this device type: "
                + ", ".join(unknown_rule_fields)
            ))
        manifest = registry.manifest(entry.plugin_id)
        if manifest is None and entry.plugin_id not in existing:
            raise HTTPException(status_code=422, detail=f"Plugin '{entry.plugin_id}' is not installed")
        if manifest and manifest.get("ai_configuration", {}).get("locked") and any(
            key in entry.configuration for key in ("ai_profile_id", "ai_model", "ai_repeat_model")
        ):
            raise HTTPException(status_code=409, detail=(
                f"{manifest.get('label', entry.plugin_id)} AI settings are managed by Helm"
            ))
        if entry.enabled and manifest is not None:
            added = _assign_recommended_plugin_fields(db, item, manifest)
            if added:
                recommended_fields[entry.plugin_id] = added
        row = existing.get(entry.plugin_id)
        if row is None:
            row = DeviceTypePlugin(device_type_id=item.id, plugin_id=entry.plugin_id)
            db.add(row)
        _require_gui_owned(row.configuration_source, f"The {entry.plugin_id} assignment")
        row.enabled = entry.enabled
        row.configuration = entry.configuration
        row.configuration_source = "gui"
        seen.add(entry.plugin_id)
    for plugin_id, row in existing.items():
        if plugin_id not in seen and row.configuration_source != "yaml":
            db.delete(row)
    db.flush()
    revision = publish_revision(db, source="gui", user=user,
                                note=f"Updated {item.key} plugin assignments")
    after = {entry.plugin_id: entry.model_dump() for entry in body.plugins}
    log_action(db, user, "device_schema.plugins", "device_schema", item.id,
               {"before": before, "after": after, "recommended_fields_added": recommended_fields,
                "revision": revision}, request)
    db.commit()
    if recommended_fields:
        refresh_managed_indexes(db)
    return get_type_plugins(item.id, db, user)


# ---------------------------------------------------- validation and status

@router.post("/validate")
def validate_schema(db: Session = Depends(get_db), user: User = Depends(require_schema_manage)):
    """Check the published configuration against the devices already stored."""
    return {"revision": current_revision(db), **analyze_schema(db)}


@router.get("/export")
def export_schema(db: Session = Depends(get_db), user: User = Depends(require_schema_manage)):
    """Download a ConfigMap that can bootstrap another TestBench deployment."""
    document = yaml.safe_dump(export_document(db), sort_keys=False, allow_unicode=True)
    indented = "".join(f"    {line}" if line.strip() else line for line in document.splitlines(keepends=True))
    config_map = (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
        "  name: testbench-device-schema\ndata:\n  device-schema.yaml: |\n"
        + indented
    )
    return download_response(config_map, "testbench-device-schema.yaml", "application/yaml")


@router.post("/import")
async def import_schema(
    request: Request,
    file: UploadFile,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    """Preview or apply a GUI-owned, strictly additive DeviceSchema import."""
    content = await file.read(1_048_577)
    if len(content) > 1_048_576:
        raise HTTPException(status_code=413, detail="Bootstrap YAML must be 1 MB or smaller")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Bootstrap YAML must be UTF-8") from exc
    try:
        document = parse_uploaded_document(text)
    except DocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if dry_run:
        return additive_import_plan(db, document)

    try:
        result = apply_additive_import(db, document, user=user)
        validation = analyze_schema(db)
        if validation["errors"]:
            db.rollback()
            raise HTTPException(status_code=422, detail={
                "message": "The imported additions would make the schema invalid",
                **validation,
            })
        log_action(db, user, "device_schema.import", "device_schema", None, result, request)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    if result["revision"] is not None:
        invalidate_cache()
        refresh_managed_indexes(db)
        ensure_entity_field_indexes(db)
    return result


@router.post("/publish")
def publish_schema(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    """Re-validate, bump the revision, and reconcile the managed indexes.

    Individual edits already publish. This exists for the case where something
    outside them changed what the schema means — a plugin appearing, an index
    that failed and can now succeed — and for a deliberate "apply it all".
    """
    report = analyze_schema(db)
    if report["errors"]:
        raise HTTPException(status_code=422, detail={"message": "The schema cannot be published", **report})
    revision = publish_revision(db, source="gui", note="Republished", user=user)
    log_action(db, user, "device_schema.publish", "device_schema", None,
               {"revision": revision, "warnings": report["warnings"]}, request)
    db.commit()
    refresh_managed_indexes(db)
    return {"revision": revision, **report, "indexes": index_status(db)}


@router.get("/reconciliation")
def reconciliation_status(user: User = Depends(get_current_user)):
    """What the last DeviceSchema reconciliation did.

    Exposed for operators and for Flux-style health checking: it reports the
    generation of the document that was seen, the revision it produced, and any
    conflict that stopped part of it applying.
    """
    return yaml_status()


@router.post("/reconcile")
def reconcile_now(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    """Re-read the mounted document and reconcile it again.

    A ConfigMap update reaches the pod as a changed file, not as an event, so
    an administrator (or a controller with an admin key) needs a way to say
    "look again" without a restart.
    """
    result = reconcile_from_settings(db)
    log_action(db, user, "device_schema.reconcile", "device_schema", None, result, request)
    db.commit()
    if result["state"] == "error":
        raise HTTPException(status_code=422, detail=result)
    return result


@router.get("/indexes")
def list_indexes(db: Session = Depends(get_db), user: User = Depends(require_schema_manage)):
    """Desired and applied state of the catalog's managed expression indexes."""
    return index_status(db)


@router.get("/revisions")
def list_revisions(limit: int = 25, db: Session = Depends(get_db), user: User = Depends(require_schema_manage)):
    rows = db.scalars(
        select(DeviceSchemaRevision).order_by(DeviceSchemaRevision.id.desc()).limit(min(limit, 200))
    ).all()
    return [
        {"revision": row.id, "created_at": row.created_at, "created_by": row.created_by,
         "source": row.source, "note": row.note, "summary": row.summary}
        for row in rows
    ]
