"""Versioned, bounded record exchange for optional TestBench replication.

UUIDs are stable across instances. Users, API keys, local configuration and
device fields marked sensitive are deliberately outside this exchange.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from ..db import as_utc, utcnow
from ..models import (
    Device, DeviceType, ReplicationTombstone, Software, SoftwareComponent, Test,
    TestRun, VendorDevice, VendorDeviceComponentSupport,
)
from .device_schema import union_field_map
from .entity_fields import get_entity_fields

MODELS = {"devices": Device, "software": Software, "vendor_claims": VendorDevice, "tests": Test}
PAGE_SIZE = 250


def stamp(value: datetime | None) -> str:
    return as_utc(value or utcnow()).isoformat()


def parse_stamp(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value))


def changed_at(row: Any) -> datetime:
    return as_utc(row.updated_at or row.created_at)


def rank(item: dict) -> tuple[datetime, str]:
    digest = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return parse_stamp(item["changed_at"]), digest


@event.listens_for(Session, "before_flush")
def remember_deletions(db: Session, _context, _instances) -> None:
    if db.info.get("applying_replication"):
        return
    seen: set[tuple[str, str]] = set()
    def mark(entity: str, identity: str, when: datetime) -> None:
        if (entity, identity) in seen:
            return
        seen.add((entity, identity))
        marker = db.get(ReplicationTombstone, (entity, identity))
        if marker is None:
            db.add(ReplicationTombstone(entity=entity, identity=identity, deleted_at=when))
        else:
            marker.deleted_at = when

    when = utcnow()
    for row in tuple(db.deleted):
        entity = next((name for name, model in MODELS.items() if isinstance(row, model)), None)
        if entity is None:
            continue
        mark(entity, row.id, when)
        # Software and device deletion can cascade inside PostgreSQL, without
        # putting child tests in Session.deleted. Keep their markers too.
        if entity in {"devices", "software"}:
            column = Test.device_id if entity == "devices" else Test.software_id
            for test_id in db.scalars(select(Test.id).where(column == row.id)):
                mark("tests", test_id, when)
        if entity == "software":
            for claim_id in db.scalars(select(VendorDevice.id).where(VendorDevice.software_id == row.id)):
                mark("vendor_claims", claim_id, when)


def _sensitive_keys(db: Session, entity: str) -> set[str]:
    if entity == "devices":
        return {key for key, field in union_field_map(db).items() if field.sensitive} | {
            "password", "api_key", "secret", "token",
        }
    return {field.key for field in get_entity_fields(db, entity) if field.sensitive}


def _safe_document(document: dict, hidden: set[str]) -> dict:
    cleaned = {key: value for key, value in document.items() if key not in hidden}
    if isinstance(cleaned.get("__extra_values"), dict):
        cleaned["__extra_values"] = {
            key: value for key, value in cleaned["__extra_values"].items() if key not in hidden
        }
    return cleaned


def _merge_document(previous: dict, incoming: dict, hidden: set[str]) -> dict:
    preserved = {key: value for key, value in previous.items() if key in hidden}
    result = preserved | _safe_document(incoming, hidden)
    old_extras = previous.get("__extra_values") or {}
    if isinstance(old_extras, dict):
        secret_extras = {key: value for key, value in old_extras.items() if key in hidden}
        if secret_extras:
            result["__extra_values"] = secret_extras | result.get("__extra_values", {})
    return result


def serialize(db: Session, entity: str, row: Any, sensitive: set[str] | None = None) -> dict:
    if entity == "devices":
        hidden = sensitive if sensitive is not None else _sensitive_keys(db, entity)
        data = {
            "unique_id": row.unique_id,
            "data": _safe_document(row.data, hidden),
            "link_overrides": row.link_overrides or {},
            "device_type_key": row.device_type.key if row.device_type else None,
        }
    elif entity == "software":
        hidden = sensitive if sensitive is not None else _sensitive_keys(db, entity)
        components = db.scalars(select(SoftwareComponent).where(
            SoftwareComponent.software_id == row.id
        ).order_by(SoftwareComponent.position, SoftwareComponent.id)).all()
        data = {"data": _safe_document(row._data or {}, hidden), "components": [
            {"id": part.id, "name": part.name, "version": part.version,
             "required": part.required, "position": part.position}
            for part in components
        ]}
    elif entity == "vendor_claims":
        hidden = sensitive if sensitive is not None else _sensitive_keys(db, "vendor_devices")
        data = {
            "software_id": row.software_id, "make": row.make, "model": row.model,
            "firmware_version": row.firmware_version, "hardware_version": row.hardware_version,
            "architecture": row.architecture, "support_status": row.support_status,
            "source": row.source, "notes": row.notes,
            "misc_data": _safe_document(row.misc_data or {}, hidden),
            "match_key": row.match_key,
            "component_support": [
                {"component_id": link.component_id, "support_status": link.support_status}
                for link in row.component_support
            ],
        }
    else:
        hidden = sensitive if sensitive is not None else _sensitive_keys(db, entity)
        data = {
            "data": _safe_document(row._data or {}, hidden), "device_id": row.device_id,
            "software_id": row.software_id, "component_id": row.component_id,
            # The run travels with each of its tests rather than as a record of
            # its own. Its shared files, like a test's, stay where they are.
            "test_run": _run_payload(row.test_run),
        }
    return {
        "entity": entity, "id": row.id, "deleted": False,
        "changed_at": stamp(changed_at(row)), "created_at": stamp(row.created_at),
        "data": data,
    }


def snapshot_page(db: Session, entity: str, after: str = "", tombstones: bool = False) -> dict:
    if entity not in MODELS:
        raise ValueError("Unknown replication entity")
    if tombstones:
        rows = db.scalars(select(ReplicationTombstone).where(
            ReplicationTombstone.entity == entity,
            ReplicationTombstone.identity > after,
        ).order_by(ReplicationTombstone.identity).limit(PAGE_SIZE + 1)).all()
        items = [{"entity": entity, "id": row.identity, "deleted": True,
                  "changed_at": stamp(row.deleted_at)} for row in rows[:PAGE_SIZE]]
        cursor = rows[PAGE_SIZE - 1].identity if len(rows) > PAGE_SIZE else None
    else:
        model = MODELS[entity]
        rows = db.scalars(select(model).where(model.id > after).order_by(model.id).limit(PAGE_SIZE + 1)).all()
        hidden = _sensitive_keys(db, "vendor_devices" if entity == "vendor_claims" else entity)
        items = [serialize(db, entity, row, hidden) for row in rows[:PAGE_SIZE]]
        cursor = rows[PAGE_SIZE - 1].id if len(rows) > PAGE_SIZE else None
    return {"items": items, "next_cursor": cursor}


def _existing_row(db: Session, entity: str, item: dict):
    return db.get(MODELS[entity], item["id"])


def _apply_device(db: Session, row: Device | None, item: dict) -> Device:
    payload = item["data"]
    type_key = payload.get("device_type_key")
    device_type = db.scalar(select(DeviceType).where(DeviceType.key == type_key)) if type_key else None
    if type_key and device_type is None:
        raise ValueError(f"Device type '{type_key}' is missing on the destination")
    duplicate = db.scalar(select(Device).where(Device.unique_id == payload["unique_id"], Device.id != item["id"]))
    if duplicate is not None:
        raise ValueError(f"Device unique ID '{payload['unique_id']}' belongs to a different record")
    hidden = _sensitive_keys(db, "devices")
    if row is None:
        row = Device(id=item["id"])
        db.add(row)
    row.unique_id = payload["unique_id"]
    row._data = _merge_document(row.data, payload["data"], hidden)
    row.link_overrides = payload.get("link_overrides") or {}
    row.device_type_id = device_type.id if device_type else None
    return row


def _apply_software(db: Session, row: Software | None, item: dict) -> Software:
    payload = item["data"]
    document = payload["data"]
    name, version = document.get("name"), document.get("version") or ""
    if not name:
        raise ValueError("Replicated software has no name")
    duplicate = db.scalar(select(Software.id).where(
        func.lower(Software.name) == name.casefold(), func.coalesce(Software.version, "") == version,
        Software.id != item["id"],
    ))
    if duplicate is not None:
        raise ValueError(f"Software '{name}' version '{version}' belongs to a different record")
    if row is None:
        row = Software(id=item["id"])
        db.add(row)
    row._data = _merge_document(row._data or {}, document, _sensitive_keys(db, "software"))
    db.flush()
    existing = {part.id: part for part in db.scalars(select(SoftwareComponent).where(
        SoftwareComponent.software_id == row.id
    ))}
    existing_by_name = {(part.name.casefold(), part.version): part for part in existing.values()}
    wanted = set()
    for raw in payload.get("components", []):
        part = existing.get(raw["id"])
        if part is None:
            duplicate = existing_by_name.get((raw["name"].casefold(), raw.get("version") or ""))
            if duplicate is not None:
                raise ValueError("Software component identity belongs to a different record")
            part = SoftwareComponent(id=raw["id"], software_id=row.id)
            db.add(part)
        part.name = raw["name"]
        part.version = raw.get("version") or ""
        part.required = bool(raw.get("required", True))
        part.position = int(raw.get("position", 0))
        wanted.add(part.id)
    for part in existing.values():
        if part.id not in wanted:
            db.delete(part)
    return row


def _run_payload(run: TestRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id, "name": run.name, "external_id": run.external_id, "url": run.url,
        "started_at": stamp(run.started_at) if run.started_at else None,
        "finished_at": stamp(run.finished_at) if run.finished_at else None,
        "notes": run.notes, "storage_folder": run.storage_folder,
    }


def _apply_run(db: Session, payload: dict) -> TestRun:
    run = db.get(TestRun, payload["id"])
    external_id = payload.get("external_id") or None
    if external_id and db.scalar(select(TestRun.id).where(
            TestRun.external_id == external_id, TestRun.id != payload["id"])):
        raise ValueError(f"Test run external ID '{external_id}' belongs to a different record")
    if run is None:
        run = TestRun(id=payload["id"], storage_folder=payload.get("storage_folder")
                      or f"test-runs/run_{payload['id']}")
        db.add(run)
    run.name = payload.get("name") or run.name or "Test run"
    run.external_id = external_id
    run.url = payload.get("url")
    run.notes = payload.get("notes")
    for name in ("started_at", "finished_at"):
        value = payload.get(name)
        setattr(run, name, parse_stamp(value) if value else None)
    db.flush()
    return run


def _apply_test(db: Session, row: Test | None, item: dict) -> Test:
    payload = item["data"]
    if db.get(Device, payload["device_id"]) is None or db.get(Software, payload["software_id"]) is None:
        raise ValueError("Replicated test references a device or software version not present on the destination")
    component_id = payload.get("component_id")
    if component_id and db.get(SoftwareComponent, component_id) is None:
        raise ValueError("Replicated test references a component not present on the destination")
    if row is None:
        row = Test(id=item["id"])
        db.add(row)
    row.device_id = payload["device_id"]
    row.software_id = payload["software_id"]
    row.component_id = component_id
    # Absent from a source older than test runs: leave the test's run alone.
    if "test_run" in payload:
        row.test_run_id = _apply_run(db, payload["test_run"]).id if payload["test_run"] else None
    row._data = _merge_document(row._data or {}, payload["data"], _sensitive_keys(db, "tests"))
    return row


def _apply_vendor_claim(db: Session, row: VendorDevice | None, item: dict) -> VendorDevice:
    payload = item["data"]
    if db.get(Software, payload["software_id"]) is None:
        raise ValueError("Replicated vendor claim references missing software")
    duplicate = db.scalar(select(VendorDevice.id).where(
        VendorDevice.software_id == payload["software_id"],
        VendorDevice.match_key == payload["match_key"],
        VendorDevice.id != item["id"],
    ))
    if duplicate is not None:
        raise ValueError("Vendor claim identity belongs to a different record")
    if row is None:
        row = VendorDevice(id=item["id"])
        db.add(row)
    row.software_id = payload["software_id"]
    for name in ("make", "model", "firmware_version", "hardware_version", "architecture",
                 "support_status", "source", "notes", "match_key"):
        setattr(row, name, payload.get(name))
    row.misc_data = _merge_document(row.misc_data or {}, payload.get("misc_data") or {},
                                    _sensitive_keys(db, "vendor_devices"))
    db.flush()
    current = {link.component_id: link for link in row.component_support}
    wanted = set()
    for link in payload.get("component_support", []):
        component_id = link["component_id"]
        component = db.get(SoftwareComponent, component_id)
        if component is None or component.software_id != row.software_id:
            raise ValueError("Vendor claim references a missing software component")
        support = current.get(component_id)
        if support is None:
            support = VendorDeviceComponentSupport(
                vendor_device_id=row.id, component_id=component_id,
                support_status=link["support_status"],
            )
            db.add(support)
        else:
            support.support_status = link["support_status"]
        wanted.add(component_id)
    for component_id, support in current.items():
        if component_id not in wanted:
            db.delete(support)
    return row


def apply_items(db: Session, items: list[dict], *, force: bool = False) -> dict:
    """Apply one dependency-ordered batch atomically; preserve source clocks."""
    counts = {"applied": 0, "skipped": 0}
    db.info["applying_replication"] = True
    try:
        for item in items:
            entity = item.get("entity")
            identity = item.get("id")
            if entity not in MODELS or not isinstance(identity, str) or not identity:
                raise ValueError("Unknown replication record")
            incoming = parse_stamp(item["changed_at"])
            marker = db.get(ReplicationTombstone, (entity, identity))
            row = _existing_row(db, entity, item)
            local_items = []
            if marker:
                local_items.append({"entity": entity, "id": identity, "deleted": True,
                                    "changed_at": stamp(marker.deleted_at)})
            if row:
                local_items.append(serialize(db, entity, row))
            if not force and local_items and max(rank(local) for local in local_items) >= rank(item):
                counts["skipped"] += 1
                continue
            if item.get("deleted"):
                if row is not None:
                    db.delete(row)
                if marker is None:
                    marker = ReplicationTombstone(entity=entity, identity=identity, deleted_at=incoming)
                    db.add(marker)
                else:
                    marker.deleted_at = incoming
            else:
                if entity == "devices":
                    row = _apply_device(db, row, item)
                elif entity == "software":
                    row = _apply_software(db, row, item)
                elif entity == "vendor_claims":
                    row = _apply_vendor_claim(db, row, item)
                else:
                    row = _apply_test(db, row, item)
                row.created_at = parse_stamp(item["created_at"])
                row.updated_at = incoming
                if marker is not None:
                    db.delete(marker)
            counts["applied"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.info.pop("applying_replication", None)
    return counts
