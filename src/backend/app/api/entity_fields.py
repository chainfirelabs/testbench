import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EntityField, User
from ..services.device_schema import field_payload as device_field_payload, get_global_fields
from ..services.entity_fields import (
    FIELD_TYPES, REQUIRED_FIELDS, drop_entity_field_indexes,
    ensure_entity_field_indexes, field_payload, get_entity_fields,
)
from .deps import get_current_user, require_admin

router = APIRouter(prefix="/entity-fields", tags=["schema"])
EDITABLE_ENTITIES = ("software", "tests")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class EntityFieldCreate(BaseModel):
    key: str
    label: str
    field_type: str = "text"
    description: str | None = None
    options: list[str] = Field(default_factory=list)
    required: bool = False
    sensitive: bool = False
    indexed: bool = False
    unique_value: bool = False


class EntityFieldUpdate(BaseModel):
    label: str | None = None
    field_type: str | None = None
    description: str | None = None
    options: list[str] | None = None
    required: bool | None = None
    sensitive: bool | None = None
    indexed: bool | None = None
    unique_value: bool | None = None
    visible: bool | None = None
    position: int | None = None


class EntityFieldLayoutItem(BaseModel):
    id: str
    list_visible: bool


class EntityFieldLayoutUpdate(BaseModel):
    fields: list[EntityFieldLayoutItem]


def _entity(value: str) -> str:
    if value not in EDITABLE_ENTITIES:
        raise HTTPException(status_code=404, detail="Schema entity not found")
    return value


def _field(db: Session, entity: str, field_id: str) -> EntityField:
    item = db.scalar(select(EntityField).where(
        EntityField.entity == _entity(entity),
        (EntityField.id == field_id) | (EntityField.key == field_id),
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Field not found")
    return item


def _protected(item: EntityField) -> bool:
    return item.key in REQUIRED_FIELDS[item.entity] or item.storage != "data"


def _validate_type(field_type: str, options: list[str]) -> None:
    if field_type not in FIELD_TYPES:
        raise HTTPException(status_code=422, detail=f"Unsupported field type: {field_type}")
    if field_type == "select" and not options:
        raise HTTPException(status_code=422, detail="A select field needs at least one option")


@router.get("")
def list_entity_fields(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict[str, list[dict]]:
    """The logical columns each entity exposes.

    Software and tests read the catalog frozen when the database was created.
    Devices are configurable per type, so their entry here is the global device
    schema — the fields every type inherits. A page that renders one device type
    asks `/device-schema/types/{key}` instead, which is the same shape with that
    type's own fields and overrides merged in.
    """
    return {
        "devices": [device_field_payload(field) for field in get_global_fields(db) if field.visible],
        **{
            entity: [field_payload(field) for field in get_entity_fields(db, entity, visible=True)]
            for entity in ("software", "tests")
        },
    }


@router.get("/{entity}")
def fields_for_entity(entity: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [field_payload(item) for item in get_entity_fields(db, _entity(entity))]


@router.put("/{entity}")
def update_entity_field_layout(
    body: EntityFieldLayoutUpdate, entity: str, db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Atomically replace an entity's field order and visibility."""
    entity = _entity(entity)
    existing = get_entity_fields(db, entity)
    by_id = {item.id: item for item in existing}
    supplied = [item.id for item in body.fields]
    if len(supplied) != len(set(supplied)) or set(supplied) != set(by_id):
        raise HTTPException(status_code=422, detail="Layout must contain every field exactly once")
    for position, update in enumerate(body.fields):
        item = by_id[update.id]
        if not update.list_visible and item.key in REQUIRED_FIELDS[entity]:
            raise HTTPException(status_code=409, detail=f"{item.label} is required by the application and cannot be hidden from lists")
        item.list_visible = update.list_visible
        item.position = position * 10
    db.commit()
    return [field_payload(item) for item in get_entity_fields(db, entity)]


@router.post("/{entity}", status_code=201)
def create_entity_field(body: EntityFieldCreate, entity: str, db: Session = Depends(get_db),
                        user: User = Depends(require_admin)):
    entity = _entity(entity)
    key = body.key.strip().lower()
    if not KEY_RE.fullmatch(key):
        raise HTTPException(status_code=422, detail="Field key must be lowercase letters, numbers, or underscores")
    if db.scalar(select(EntityField).where(EntityField.entity == entity, EntityField.key == key)):
        raise HTTPException(status_code=409, detail=f"Field '{key}' already exists")
    _validate_type(body.field_type, body.options)
    position = (db.scalar(select(func.max(EntityField.position)).where(EntityField.entity == entity)) or 0) + 10
    item = EntityField(
        entity=entity, key=key, label=body.label.strip(), field_type=body.field_type,
        description=body.description, options=body.options, required=body.required,
        visible=True, sensitive=body.sensitive, writable=True, storage="data",
        position=position, indexed=body.indexed or body.unique_value,
        unique_value=body.unique_value,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    ensure_entity_field_indexes(db)
    return field_payload(item)


@router.patch("/{entity}/{field_id}")
def update_entity_field(body: EntityFieldUpdate, entity: str, field_id: str,
                        db: Session = Depends(get_db), user: User = Depends(require_admin)):
    item = _field(db, entity, field_id)
    changes = body.model_dump(exclude_unset=True)
    if _protected(item):
        forbidden = set(changes) - {"label", "description", "visible", "position"}
        if forbidden:
            raise HTTPException(status_code=409, detail=f"{item.label} is required by the application")
    if changes.get("visible") is False and item.key in REQUIRED_FIELDS[item.entity]:
        raise HTTPException(status_code=409, detail=f"{item.label} is required by the application and cannot be hidden")
    drop_entity_field_indexes(db, item)
    field_type = changes.get("field_type", item.field_type)
    options = changes.get("options", item.options or [])
    _validate_type(field_type, options)
    for key, value in changes.items():
        if key == "indexed" and changes.get("unique_value"):
            value = True
        setattr(item, key, value)
    if item.unique_value:
        item.indexed = True
    db.commit()
    ensure_entity_field_indexes(db)
    return field_payload(item)


@router.delete("/{entity}/{field_id}", status_code=204)
def delete_entity_field(entity: str, field_id: str, db: Session = Depends(get_db),
                        user: User = Depends(require_admin)):
    item = _field(db, entity, field_id)
    if _protected(item):
        raise HTTPException(status_code=409, detail=f"{item.label} is required by the application and cannot be deleted")
    drop_entity_field_indexes(db, item)
    db.delete(item)
    db.commit()
