"""Device type records: the categories an installation's inventory is split by.

The type holds identity and presentation. Which fields it shows and which
plugins may act on it are separate resources under `/device-schema`, because a
field is defined once and assigned to many types rather than copied into each.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Device, DeviceFieldAssignment, DeviceFieldDefinition, DeviceType, DeviceTypePlugin, User
from ..schemas import DeviceTypeCreate, DeviceTypeOut, DeviceTypeUpdate
from ..services.audit import log_action
from ..services.device_schema import (
    TYPE_KEY_RE,
    get_allowed_plugins,
    get_effective_fields,
    publish_revision,
)
from .deps import get_current_user, require_admin

router = APIRouter(prefix="/device-types", tags=["device types"])


def _require_gui_owned(item: DeviceType) -> None:
    """Refuse to edit a row a YAML reconciler owns.

    Silently accepting the edit would be worse than refusing it: the change
    would apply, look saved, and be reverted on the next reconciliation with no
    explanation.
    """
    if item.configuration_source == "yaml":
        raise HTTPException(
            status_code=409,
            detail=f"Device type '{item.key}' is owned by the DeviceSchema document and cannot be edited here",
        )


def _out(db: Session, item: DeviceType) -> DeviceTypeOut:
    count = db.scalar(select(func.count()).select_from(Device).where(Device.device_type_id == item.id)) or 0
    fields = [field for field in get_effective_fields(db, item.id) if field.visible]
    payload = DeviceTypeOut.model_validate(item).model_dump()
    payload.update(
        device_count=count,
        field_count=len(fields),
        required_field_keys=[field.key for field in fields if field.required],
        plugin_ids=sorted(get_allowed_plugins(db, item.id)),
    )
    return DeviceTypeOut(**payload)


@router.get("", response_model=list[DeviceTypeOut])
def list_device_types(
    include_disabled: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(DeviceType)
    if not include_disabled:
        q = q.where(DeviceType.enabled.is_(True))
    items = db.scalars(q.order_by(DeviceType.position, DeviceType.label)).all()
    return [_out(db, item) for item in items]


@router.post("", response_model=DeviceTypeOut, status_code=201)
def create_device_type(
    body: DeviceTypeCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    key = body.key.strip().lower()
    if not TYPE_KEY_RE.fullmatch(key):
        raise HTTPException(
            status_code=422,
            detail="Type key must start with a letter and contain lowercase letters, numbers, or hyphens",
        )
    if key == "uncategorized":
        # The URL /devices/type/uncategorized already means "no device type".
        raise HTTPException(status_code=422, detail="'uncategorized' is reserved")
    if db.scalar(select(DeviceType.id).where(DeviceType.key == key)):
        raise HTTPException(status_code=409, detail=f"Device type '{key}' already exists")
    if not body.label.strip():
        raise HTTPException(status_code=422, detail="Label must not be blank")
    # No field rows are created: a new type inherits every global assignment
    # immediately, including ones added long after it.
    item = DeviceType(**{**body.model_dump(), "key": key, "label": body.label.strip()})
    db.add(item)
    db.flush()
    publish_revision(db, source="gui", note=f"Created device type {key}", user=user)
    log_action(db, user, "device_type.create", "device_type", item.id, body.model_dump(mode="json"), request)
    db.commit()
    return _out(db, item)


@router.patch("/{type_id}", response_model=DeviceTypeOut)
def update_device_type(
    type_id: str,
    body: DeviceTypeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    item = db.get(DeviceType, type_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Device type not found")
    _require_gui_owned(item)
    updates = body.model_dump(exclude_unset=True)
    if "label" in updates and not (updates["label"] or "").strip():
        raise HTTPException(status_code=422, detail="Label must not be blank")
    before = {key: getattr(item, key) for key in updates}
    for key, value in updates.items():
        setattr(item, key, value.strip() if key == "label" else value)
    publish_revision(db, source="gui", note=f"Updated device type {item.key}", user=user)
    log_action(db, user, "device_type.update", "device_type", item.id,
               {"before": before, "after": updates}, request)
    db.commit()
    return _out(db, item)


@router.delete("/{type_id}", status_code=204)
def delete_device_type(
    type_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    item = db.get(DeviceType, type_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Device type not found")
    _require_gui_owned(item)
    count = db.scalar(select(func.count()).select_from(Device).where(Device.device_type_id == item.id)) or 0
    if count:
        raise HTTPException(status_code=409, detail=f"Device type is assigned to {count} device(s); disable it instead")
    snapshot = {
        "key": item.key,
        "label": item.label,
        "fields": [
            {"key": key, "visible": assignment.visible, "list_visible": assignment.list_visible,
             "required": assignment.required}
            for assignment, key in db.execute(
                select(DeviceFieldAssignment, DeviceFieldDefinition.key)
                .join(DeviceFieldDefinition, DeviceFieldDefinition.id == DeviceFieldAssignment.field_definition_id)
                .where(DeviceFieldAssignment.device_type_id == item.id)
            )
        ],
        "plugins": [row.plugin_id for row in db.scalars(
            select(DeviceTypePlugin).where(DeviceTypePlugin.device_type_id == item.id)
        )],
    }
    publish_revision(db, source="gui", note=f"Deleted device type {item.key}", user=user)
    log_action(db, user, "device_type.delete", "device_type", item.id, {"snapshot": snapshot}, request)
    db.delete(item)
    db.commit()
