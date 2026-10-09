"""Vendor devices: the hardware a vendor claims their software works against.

Scoped to one software version and imported separately from the device inventory — a
vendor device is a claim on a compatibility list, not something we own. What
the software has actually been run against comes from `tests` instead
(see `/software/{software_id}/tested-devices`).
"""

import re

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import Boolean, Numeric, and_, cast, delete, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import (EntityField, Software, SoftwareComponent, User, VendorDevice,
                      VendorDeviceComponentSupport, VendorDeviceFieldOverride)
from ..models.vendor_device import VENDOR_SUPPORT_STATUSES, build_match_key
from ..schemas import (
    BulkIds,
    ImportResult,
    Page,
    VendorDeviceCatalogCreate,
    VendorDeviceCatalogOut,
    VendorDeviceCreate,
    VendorDeviceOut,
    VendorDeviceUpdate,
    VendorComponentSupportIn,
    VendorComponentMatchOut,
)
from ..services.audit import field_diff, log_action
from ..services.io import (
    database_export_rows,
    download_response,
    parse_import,
    safe_filename,
    streaming_export_response,
    strip_nulls,
    template_csv,
)
from ..services.list_filters import checklist_query, exclude_clause, excluded_values, include_clause
from ..services.query import order_by, row_error
from ..services.versions import newest_version
from ..services.entity_fields import (
    coerce_query_value, field_payload, get_entity_fields, merge_extra_columns, project_fields,
    validate_custom_values,
)
from .deps import get_current_user, require_schema_manage, require_software_edit

router = APIRouter(prefix="/software/{software_id}/vendor-devices", tags=["vendor-devices"])

# The same rows, addressed as one catalogue rather than one software's list.
# A vendor device belongs to a software version, but the question people
# actually arrive with — "does anything claim to support this box?" — is not
# about a version, and cannot be asked of an endpoint that needs one first.
catalog_router = APIRouter(prefix="/vendor-devices", tags=["vendor-devices"])

EXPORT_COLUMNS = [
    "id",
    "make",
    "model",
    "firmware_version",
    "hardware_version",
    "architecture",
    "support_status",
    "component_support",
    "source",
    "notes",
    "misc_data",
    "created_at",
    "updated_at",
]

EDITABLE_FIELDS = [
    "make",
    "model",
    "firmware_version",
    "hardware_version",
    "architecture",
    "support_status",
    "source",
    "notes",
    "misc_data",
]

COMPONENT_SUPPORT_FIELD = "component_support"
FLAT_COMPONENT_FIELDS = ("component_name", "component_version", "component_status")

# Fields the API owns; an import file carrying them is ignored rather than rejected.
IMPORT_IGNORED = {"id", "software_id", "vendor", "match_key", "created_at", "updated_at", "created_by", "updated_by"}

# The columns an import template offers. None is required on its own, but a row
# has to say something about which device it describes (see build_match_key).
TEMPLATE_COLUMNS = EDITABLE_FIELDS + list(FLAT_COMPONENT_FIELDS) + [COMPONENT_SUPPORT_FIELD]

SEARCH_FIELDS = ("make", "model", "firmware_version", "hardware_version", "architecture", "source", "notes")


class VendorFieldOverrideIn(BaseModel):
    id: str
    visible: bool = True
    required: bool = False


class VendorFieldLayoutIn(BaseModel):
    fields: list[VendorFieldOverrideIn]


def _vd_dict(vd: VendorDevice) -> dict:
    return VendorDeviceOut.model_validate(vd).model_dump(mode="json")


def _effective_fields(db: Session, software: Software) -> list[tuple[EntityField, dict]]:
    fields = get_entity_fields(db, "vendor_devices")
    overrides = {
        item.field_id: item for item in db.scalars(select(VendorDeviceFieldOverride).where(
            VendorDeviceFieldOverride.software_id == software.id,
        )).all()
    }
    effective = []
    for field in fields:
        override = overrides.get(field.id)
        payload = field_payload(field)
        if override:
            payload.update(
                visible=override.visible,
                list_visible=override.visible,
                required=override.required,
                position=override.position,
                inherited=False,
            )
        else:
            payload["inherited"] = True
        effective.append((field, payload))
    return sorted(effective, key=lambda item: item[1]["position"])


def _validate_values(
    db: Session, software: Software, values: dict, *, partial: bool = False,
) -> dict:
    effective = _effective_fields(db, software)
    overrides = {
        field.key: {"visible": payload["visible"], "required": payload["required"]}
        for field, payload in effective
    }
    normalized = validate_custom_values(
        db, "vendor_devices", values, "misc_data", partial=partial,
        field_overrides=overrides,
    )
    for field, payload in effective:
        if field.storage == "data" or not payload["visible"] or not payload["required"]:
            continue
        if partial and field.key not in normalized:
            continue
        value = normalized.get(field.key)
        if value is None or value == "":
            raise ValueError(f"{field.label} is required")
    return normalized


def _get_software(db: Session, key: str) -> Software:
    """Resolve software by its UUID or, failing that, its name (used in URLs).

    Several rows can share a name, one per version, and each version owns its
    own vendor devices. A bare name means the current version; a specific
    version is addressed by its id. Names match case-insensitively.
    """
    software = db.get(Software, key) or db.scalar(
        select(Software)
        .where(func.lower(Software.name) == (key or "").lower())
        .order_by(Software.created_at.desc())
        .limit(1)
    )
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    return software


def _get_vd(db: Session, software: Software, vd_id: str) -> VendorDevice:
    vd = db.get(VendorDevice, vd_id)
    if vd is None or vd.software_id != software.id:
        raise HTTPException(status_code=404, detail="Vendor device not found")
    return vd


def _apply(vd: VendorDevice, values: dict) -> None:
    """Write the given fields onto the row and refresh its dedupe key."""
    for field, value in values.items():
        setattr(vd, field, value)
    vd.match_key = build_match_key({f: getattr(vd, f) for f in EDITABLE_FIELDS})


def _resolve_component_support(
    db: Session, software: Software, entries: list[VendorComponentSupportIn],
) -> list[tuple[SoftwareComponent, str]]:
    """Validate links before writing a claim; components must belong to its version."""
    resolved = []
    seen = set()
    for entry in entries:
        component = db.get(SoftwareComponent, entry.component_id) if entry.component_id else None
        if (component is None or component.software_id != software.id) and entry.component_name:
            component = db.scalar(select(SoftwareComponent).where(
                SoftwareComponent.software_id == software.id,
                func.lower(SoftwareComponent.name) == entry.component_name.strip().lower(),
                SoftwareComponent.version == (entry.component_version or "").strip(),
            ))
        if component is None or component.software_id != software.id:
            raise ValueError("component must belong to the selected software version")
        if component.id in seen:
            raise ValueError("component support contains a duplicate component")
        seen.add(component.id)
        resolved.append((component, entry.support_status))
    return resolved


def _import_component_support(row: dict) -> list[VendorComponentSupportIn] | None:
    """Accept a single component in flat CSV columns or the existing JSON list."""
    name = row.pop("component_name", None)
    version = row.pop("component_version", None)
    status = row.pop("component_status", None)
    supplied = row.get(COMPONENT_SUPPORT_FIELD) is not None
    entries = list(row.get(COMPONENT_SUPPORT_FIELD) or [])
    if name is not None:
        entries.append({
            "component_name": name,
            "component_version": version or "",
            "support_status": status or row.get("support_status") or "supported",
        })
    elif version is not None or status is not None:
        raise ValueError("component_name is required with component_version or component_status")
    if not supplied and name is None:
        return None
    return [VendorComponentSupportIn.model_validate(entry) for entry in entries]


def _replace_component_support(
    vd: VendorDevice, entries: list[tuple[SoftwareComponent, str]],
) -> None:
    existing = {row.component_id: row for row in vd.component_support}
    updated = []
    for component, status in entries:
        row = existing.get(component.id)
        if row is None:
            row = VendorDeviceComponentSupport(component_id=component.id, support_status=status)
        row.support_status = status
        updated.append(row)
    vd.component_support = updated


def _merge_component_support(
    vd: VendorDevice, entries: list[tuple[SoftwareComponent, str]],
) -> None:
    """Add or update named component versions without dropping other claims."""
    existing = {row.component_id: row for row in vd.component_support}
    for component, status in entries:
        row = existing.get(component.id)
        if row is None:
            row = VendorDeviceComponentSupport(component_id=component.id, support_status=status)
            vd.component_support.append(row)
            existing[component.id] = row
        else:
            row.support_status = status


def _find_duplicate(db: Session, software: Software, match_key: str, exclude_id: str | None = None) -> VendorDevice | None:
    q = select(VendorDevice).where(
        VendorDevice.software_id == software.id, VendorDevice.match_key == match_key
    )
    if exclude_id:
        q = q.where(VendorDevice.id != exclude_id)
    return db.scalar(q)


def _describe(values: dict) -> str:
    """Human-readable label for a row, for conflict messages."""
    parts: list[str] = []
    for f in ("make", "model"):
        part = str(values[f]).strip() if values.get(f) else ""
        # Vendor and make are usually the same word; say it once.
        if part and part.lower() not in (p.lower() for p in parts):
            parts.append(part)
    return " ".join(parts) or "(blank)"


COMPONENT_FILTER_KEYS = ("component_name", "component_version", "component_status")


def _component_filters(params: dict) -> dict[str, str]:
    values = {key: str(params.get(key) or "").strip() for key in COMPONENT_FILTER_KEYS}
    if values["component_status"] and values["component_status"] not in VENDOR_SUPPORT_STATUSES:
        raise HTTPException(status_code=422, detail=f"component_status must be one of {VENDOR_SUPPORT_STATUSES}")
    return values


def _component_exists(filters: dict[str, str], *, search: str | None = None):
    """Match all component predicates against one version without multiplying claim rows."""
    conditions = [SoftwareComponent.software_id == VendorDevice.software_id]
    if filters["component_name"]:
        conditions.append(SoftwareComponent.name.ilike(f"%{filters['component_name']}%"))
    if filters["component_version"]:
        conditions.append(SoftwareComponent.version == filters["component_version"])
    if filters["component_status"]:
        conditions.append(func.coalesce(
            VendorDeviceComponentSupport.support_status, VendorDevice.support_status,
        ) == filters["component_status"])
    if search:
        like = f"%{search}%"
        conditions.append(or_(SoftwareComponent.name.ilike(like), SoftwareComponent.version.ilike(like)))
    return select(SoftwareComponent.id).outerjoin(
        VendorDeviceComponentSupport,
        and_(VendorDeviceComponentSupport.component_id == SoftwareComponent.id,
             VendorDeviceComponentSupport.vendor_device_id == VendorDevice.id),
    ).where(*conditions).correlate(VendorDevice).exists()


def _matching_components(db: Session, rows: list[VendorDevice], filters: dict[str, str],
                         search: str | None = None) -> dict[str, list[dict]]:
    has_filters = any(filters.values())
    if not rows or (not has_filters and not search):
        return {}
    search_key = (search or "").casefold()
    components = db.scalars(select(SoftwareComponent).where(
        SoftwareComponent.software_id.in_({row.software_id for row in rows}),
    )).all()
    by_software: dict[str, list[SoftwareComponent]] = {}
    for component in components:
        by_software.setdefault(component.software_id, []).append(component)
    matches = {}
    for row in rows:
        explicit = {link.component_id: link.support_status for link in row.component_support}
        found = []
        for component in by_software.get(row.software_id, []):
            status = explicit.get(component.id, row.support_status)
            if filters["component_name"] and filters["component_name"].casefold() not in component.name.casefold():
                continue
            if filters["component_version"] and filters["component_version"] != component.version:
                continue
            if filters["component_status"] and filters["component_status"] != status:
                continue
            if not has_filters and search_key and not (
                search_key in component.name.casefold()
                or search_key in component.version.casefold()
            ):
                continue
            found.append({"component_id": component.id, "component_name": component.name,
                          "component_version": component.version, "support_status": status,
                          "inherited": component.id not in explicit})
        matches[row.id] = found
    return matches


def _query(db: Session, software: Software, search: str | None, component_filters: dict[str, str] | None = None):
    q = select(VendorDevice).where(VendorDevice.software_id == software.id)
    filters = component_filters or _component_filters({})
    if any(filters.values()):
        q = q.where(_component_exists(filters))
    if search:
        like = f"%{search}%"
        custom = [
            VendorDevice.misc_data[field.key].astext.ilike(like)
            for field, _payload in _effective_fields(db, software)
            if field.storage == "data" and not field.sensitive
        ]
        q = q.where(or_(
            *[getattr(VendorDevice, f).ilike(like) for f in SEARCH_FIELDS],
            _component_exists(_component_filters({}), search=search),
            *custom,
        ))
    return q


def _group_value(column):
    return func.lower(func.trim(func.coalesce(column, "")))


def _firmware_key(item: dict):
    version = str(item.get("firmware_version") or "")
    natural = tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                    for part in re.findall(r"\d+|\D+", version))
    return natural, str(item.get("updated_at") or item.get("created_at") or "")


@router.get("/grouped")
@router.post("/grouped/query")
def list_grouped_vendor_devices(
    software_id: str,
    search: str | None = None,
    component_name: str | None = None,
    component_version: str | None = None,
    component_status: str | None = None,
    sort: str = "make",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    filters_body: Annotated[dict[str, str] | None, Body()] = None,
):
    """Page the presentation groups while retaining every firmware member."""
    software = _get_software(db, software_id)
    filters = _component_filters(locals())
    source_query = _query(db, software, search, filters)
    standard = {key for key in EDITABLE_FIELDS if key != "misc_data"}
    custom = {field.key: field for field, _payload in _effective_fields(db, software) if field.storage == "data"}
    for key, value in checklist_query(request, filters_body).items():
        if key in {"search", "sort", "order", "page", "page_size", *COMPONENT_FILTER_KEYS} or not value:
            continue
        excluded = key.startswith("exclude__")
        included = key.startswith("include__")
        field = key.removeprefix("exclude__" if excluded else "include__") if excluded or included else key
        expression = (getattr(VendorDevice, field) if field in standard else
                      _custom_filter_expression(custom[field]) if field in custom else None)
        if expression is None:
            continue
        if excluded or included:
            values = excluded_values(value)
            if field in custom:
                values = [coerce_query_value(custom[field], item) for item in values]
            clause = (exclude_clause if excluded else include_clause)(expression, values)
            if clause is not None:
                source_query = source_query.where(clause)
        else:
            source_query = source_query.where(expression.ilike(f"%{value}%"))
    source = source_query.subquery()
    keys = [
        _group_value(source.c.make).label("make_key"),
        _group_value(source.c.model).label("model_key"),
        _group_value(source.c.hardware_version).label("hardware_key"),
    ]
    grouped = select(*keys).group_by(*keys)
    total = db.scalar(select(func.count()).select_from(grouped.subquery())) or 0
    sort_index = {"make": 0, "model": 1, "hardware_version": 2}.get(sort, 0)
    primary = keys[sort_index].desc() if order == "desc" else keys[sort_index].asc()
    group_rows = db.execute(
        grouped.order_by(primary, *keys).offset((page - 1) * page_size).limit(page_size)
    ).all()
    if not group_rows:
        return {"items": [], "total": total, "page": page, "page_size": page_size}
    clauses = [and_(
        _group_value(VendorDevice.make) == row.make_key,
        _group_value(VendorDevice.model) == row.model_key,
        _group_value(VendorDevice.hardware_version) == row.hardware_key,
    ) for row in group_rows]
    members = db.scalars(source_query.where(or_(*clauses))).all()
    matched = _matching_components(db, members, filters, search)
    by_key: dict[tuple[str, str, str], list[dict]] = {}
    for member in members:
        payload = _vd_dict(member)
        payload["matching_components"] = matched.get(member.id, [])
        key = tuple(str(payload.get(name) or "").strip().lower()
                    for name in ("make", "model", "hardware_version"))
        by_key.setdefault(key, []).append(payload)
    items = []
    for row in group_rows:
        key = (row.make_key, row.model_key, row.hardware_key)
        firmware = sorted(by_key.get(key, []), key=_firmware_key, reverse=True)
        if not firmware:
            continue
        items.append({**firmware[0], "_groupKey": "\0".join(key), "_firmwareMembers": firmware})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/schema")
def vendor_device_schema(
    software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    software = _get_software(db, software_id)
    return [payload for _field, payload in _effective_fields(db, software)]


@router.put("/schema")
def update_vendor_device_schema(
    software_id: str, body: VendorFieldLayoutIn, db: Session = Depends(get_db),
    user: User = Depends(require_schema_manage),
):
    software = _get_software(db, software_id)
    fields = get_entity_fields(db, "vendor_devices")
    by_id = {field.id: field for field in fields}
    supplied = [item.id for item in body.fields]
    if len(supplied) != len(set(supplied)) or set(supplied) != set(by_id):
        raise HTTPException(status_code=422, detail="Layout must contain every vendor-device field exactly once")
    db.execute(delete(VendorDeviceFieldOverride).where(
        VendorDeviceFieldOverride.software_id == software.id,
    ))
    for position, item in enumerate(body.fields):
        field = by_id[item.id]
        if item.required and not field.writable:
            raise HTTPException(status_code=422, detail=f"{field.label} is read-only and cannot be required")
        if item.required and not item.visible:
            raise HTTPException(status_code=422, detail=f"{field.label} cannot be required while hidden")
        db.add(VendorDeviceFieldOverride(
            software_id=software.id, field_id=field.id, visible=item.visible,
            required=item.required, position=position * 10,
        ))
    db.commit()
    return [payload for _field, payload in _effective_fields(db, software)]


@router.get("", response_model=Page[VendorDeviceOut])
def list_vendor_devices(
    software_id: str,
    search: str | None = None,
    component_name: str | None = None,
    component_version: str | None = None,
    component_status: str | None = None,
    sort: str = "make",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    software = _get_software(db, software_id)
    filters = _component_filters(locals())
    q = _query(db, software, search, filters)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    # Secondary sort keeps paging stable when the primary column repeats.
    q = q.order_by(order_by(VendorDevice, sort, order, "make"), VendorDevice.match_key.asc())
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    matched = _matching_components(db, items, filters, search)
    out = []
    for item in items:
        payload = VendorDeviceOut.model_validate(item)
        payload.matching_components = [VendorComponentMatchOut.model_validate(match)
                                       for match in matched.get(item.id, [])]
        out.append(payload)
    return Page(
        items=out,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/export")
def export_vendor_devices(
    software_id: str,
    format: str = "json",
    search: str | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    software = _get_software(db, software_id)
    filters = _component_filters(dict(request.query_params))
    count = db.scalar(select(func.count()).select_from(_query(db, software, search, filters).subquery())) or 0
    fields = [field for field, payload in _effective_fields(db, software)
              if payload["visible"] and field.key != COMPONENT_SUPPORT_FIELD]
    log_action(
        db, user, "software.vendor_devices.export", "software", software.id,
        {"format": format, "count": count}, request,
    )
    db.commit()
    def rows(export_db: Session):
        export_software = _get_software(export_db, software_id)
        result = export_db.scalars(_query(export_db, export_software, search, filters)
                                   .order_by(VendorDevice.match_key)
                                   .execution_options(yield_per=100))
        for item in result:
            payload = _vd_dict(item)
            yield {**project_fields(payload, fields, "misc_data"),
                   COMPONENT_SUPPORT_FIELD: payload[COMPONENT_SUPPORT_FIELD]}
    # The stem carries a software name, which is free text: safe_filename keeps a
    # quote or newline in it from breaking out of the Content-Disposition header.
    return streaming_export_response(
        database_export_rows(rows), [field.key for field in fields] + [COMPONENT_SUPPORT_FIELD],
        format, f"{software.name}-vendor-devices",
    )


@router.get("/template")
def vendor_device_template(software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A blank CSV with the columns an import accepts — fill it in and import it."""
    software = _get_software(db, software_id)
    name = safe_filename(f"{software.name}-vendor-devices-template", "csv")
    columns = [
        field.key for field, payload in _effective_fields(db, software)
        if payload["visible"] and field.writable and field.key != "misc_data"
    ]
    return download_response(
        template_csv(columns + list(FLAT_COMPONENT_FIELDS) + [COMPONENT_SUPPORT_FIELD]),
        name, "text/csv",
    )


@router.post("/import", response_model=ImportResult)
async def import_vendor_devices(
    software_id: str,
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    """Load a vendor compatibility list.

    Rows are matched on the identity fields (make/model/firmware/
    hardware/architecture), so re-importing an updated list refreshes the
    existing rows instead of duplicating them.
    """
    software = _get_software(db, software_id)
    rows = parse_import(await file.read(), file.filename or "")
    result = ImportResult(created=0, updated=0, errors=[])

    # Validate the whole file before touching the database: rolling a bad row
    # back mid-file would also undo the rows already applied from it, while the
    # counts kept claiming they had landed.
    parsed: list[tuple[str, dict, list[tuple[SoftwareComponent, str]] | None]] = []
    fields = get_entity_fields(db, "vendor_devices")
    # JSON-backed catalog fields (and still-unknown spreadsheet columns) are
    # folded into misc_data; only physical columns remain top-level.
    known = {
        field.key for field in fields if field.storage != "data"
    } | IMPORT_IGNORED | {"misc_data", COMPONENT_SUPPORT_FIELD} | set(FLAT_COMPONENT_FIELDS)
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            row = merge_extra_columns(row, known, "misc_data")
            component_entries = _import_component_support(row)
            body = VendorDeviceCreate(
                **strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
            )
            links = _resolve_component_support(db, software, component_entries) if component_entries is not None else None
            data = _validate_values(db, software, body.model_dump(exclude={COMPONENT_SUPPORT_FIELD}))
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        parsed.append((build_match_key(data), data, links))

    existing = {
        vd.match_key: vd
        for vd in db.scalars(select(VendorDevice).where(VendorDevice.software_id == software.id))
    }
    # Rows this file has already applied, so a file listing the same device
    # twice updates one row instead of tripping the unique constraint.
    touched: set[str] = set()
    for key, data, links in parsed:
        vd = existing.get(key)
        is_new = vd is None
        if is_new:
            vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
            db.add(vd)
            existing[key] = vd
        _apply(vd, data)
        if links is not None:
            _merge_component_support(vd, links)
        vd.updated_by = user.id
        if not is_new:
            vd.updated_at = utcnow()
        if key not in touched:
            result.created += 1 if is_new else 0
            result.updated += 0 if is_new else 1
            touched.add(key)

    log_action(
        db, user, "software.vendor_devices.import", "software", software.id,
        {
            "file": file.filename,
            "created": result.created,
            "updated": result.updated,
            "errors": result.errors,
        },
        request,
    )
    db.commit()
    return result


@router.post("/delete", status_code=204)
def delete_vendor_devices_bulk(
    software_id: str,
    body: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    """Delete multiple vendor devices by id (multi-select delete)."""
    software = _get_software(db, software_id)
    for vd_id in body.ids:
        vd = db.get(VendorDevice, vd_id)
        if vd and vd.software_id == software.id:
            log_action(
                db, user, "software.vendor_device.delete", "vendor_device", vd.id,
                {"software_id": software.id, "snapshot": _vd_dict(vd), "bulk": True}, request,
            )
            db.delete(vd)
    db.commit()
    return None


@router.post("", response_model=VendorDeviceOut, status_code=201)
def create_vendor_device(
    software_id: str,
    body: VendorDeviceCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    software = _get_software(db, software_id)
    try:
        data = _validate_values(db, software, body.model_dump(exclude={COMPONENT_SUPPORT_FIELD}))
        links = _resolve_component_support(db, software, body.component_support)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    key = build_match_key(data)
    duplicate = _find_duplicate(db, software, key)
    if duplicate:
        if not links:
            raise HTTPException(
                status_code=409,
                detail=f"{software.name} already lists a vendor device matching {_describe(data)}",
            )
        _merge_component_support(duplicate, links)
        duplicate.updated_by = user.id
        duplicate.updated_at = utcnow()
        log_action(db, user, "software.vendor_device.update", "vendor_device", duplicate.id,
                   {"software_id": software.id,
                    "component_support_added": body.model_dump(mode="json")[COMPONENT_SUPPORT_FIELD]}, request)
        db.commit()
        db.refresh(duplicate)
        response.status_code = 200
        return VendorDeviceOut.model_validate(duplicate)
    vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
    _apply(vd, data)
    _replace_component_support(vd, links)
    db.add(vd)
    log_action(
        db, user, "software.vendor_device.create", "vendor_device", vd.id,
        {"software_id": software.id, **body.model_dump(mode="json")}, request,
    )
    db.commit()
    db.refresh(vd)
    return VendorDeviceOut.model_validate(vd)


@router.patch("/{vd_id}", response_model=VendorDeviceOut)
def update_vendor_device(
    software_id: str,
    vd_id: str,
    body: VendorDeviceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    software = _get_software(db, software_id)
    vd = _get_vd(db, software, vd_id)
    old = {f: getattr(vd, f) for f in EDITABLE_FIELDS}
    old[COMPONENT_SUPPORT_FIELD] = [
        (item.component_id, item.support_status) for item in vd.component_support
    ]
    try:
        data = _validate_values(
            db, software, body.model_dump(exclude_unset=True, exclude={COMPONENT_SUPPORT_FIELD}), partial=True,
        )
        links = (_resolve_component_support(db, software, body.component_support or [])
                 if COMPONENT_SUPPORT_FIELD in body.model_fields_set else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _apply(vd, data)
    if links is not None:
        _replace_component_support(vd, links)
    if _find_duplicate(db, software, vd.match_key, exclude_id=vd.id):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{software.name} already lists a vendor device matching "
                   f"{_describe({f: getattr(vd, f) for f in EDITABLE_FIELDS})}",
        )
    vd.updated_by = user.id
    vd.updated_at = utcnow()
    new = {f: getattr(vd, f) for f in EDITABLE_FIELDS}
    new[COMPONENT_SUPPORT_FIELD] = [
        (item.component_id, item.support_status) for item in vd.component_support
    ]
    log_action(
        db, user, "software.vendor_device.update", "vendor_device", vd.id,
        {"software_id": software.id, "diff": field_diff(old, new)},
        request,
    )
    db.commit()
    db.refresh(vd)
    return VendorDeviceOut.model_validate(vd)


@router.delete("/{vd_id}", status_code=204)
def delete_vendor_device(
    software_id: str,
    vd_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    software = _get_software(db, software_id)
    vd = _get_vd(db, software, vd_id)
    log_action(
        db, user, "software.vendor_device.delete", "vendor_device", vd.id,
        {"software_id": software.id, "snapshot": _vd_dict(vd)}, request,
    )
    db.delete(vd)
    db.commit()


# ---------------------------------------------------------------------------
# The catalogue: vendor claims across every software version at once.
# ---------------------------------------------------------------------------

def _custom_filter_expression(field):
    expression = VendorDevice.misc_data[field.key].astext
    if field.field_type in {"boolean", "number"}:
        expression = cast(func.nullif(expression, ""), Boolean if field.field_type == "boolean" else Numeric)
    return expression


# Columns a catalogue search matches on, and the ones it filters by exactly.
CATALOG_TEXT_FILTERS = {
    "make": VendorDevice.make,
    "model": VendorDevice.model,
    "firmware_version": VendorDevice.firmware_version,
    "hardware_version": VendorDevice.hardware_version,
    "architecture": VendorDevice.architecture,
    "source": VendorDevice.source,
    "notes": VendorDevice.notes,
}

CATALOG_EXACT_FILTERS = {
    "support_status": VendorDevice.support_status,
    "software_id": VendorDevice.software_id,
}

CATALOG_SORT_COLUMNS = {
    **CATALOG_TEXT_FILTERS,
    "support_status": VendorDevice.support_status,
    "software_name": Software.name,
    "software_version": Software.version,
    "created_at": VendorDevice.created_at,
    "updated_at": VendorDevice.updated_at,
}

# The two columns naming the version a row is a claim by. The claim's own
# columns follow, and they come from the field catalog rather than from a list
# here: an installation that added a vendor-device field expects to see it in
# the file, and — because the file is also an import — a column left out is not
# merely missing from the export. Re-importing writes the whole row, so an
# absent column reads as "no value" and clears what was there.
CATALOG_SOFTWARE_COLUMNS = ["software_name", "software_version"]


def _catalog_export_fields(db: Session) -> list[EntityField]:
    """The claim columns a catalogue file carries, in catalog order.

    The global catalog, not one software's layout: a file that may name any
    software cannot be shaped by the visibility overrides of one of them.
    """
    return [
        field for field in get_entity_fields(db, "vendor_devices")
        if field.key not in {"misc_data", COMPONENT_SUPPORT_FIELD}
    ]

# Query parameters that steer the request rather than filter it.
CATALOG_CONTROLS = {"search", "sort", "order", "page", "page_size", "offset", "format", "software",
                    *COMPONENT_FILTER_KEYS}


def _catalog_query(db: Session, search: str | None, params: dict):
    """Vendor claims across every software, filtered by `?column=value`.

    Joined to `software` rather than resolved per row: the software's name is
    part of what a catalogue row *is*, and it is also something to search and
    sort on — "every claim Backup-Restore makes" is the same question as "every
    claim about a Cisco", asked of the other side of the join.
    """
    q = select(VendorDevice, Software).join(Software, VendorDevice.software_id == Software.id)
    component_filters = _component_filters(params)
    if any(component_filters.values()):
        q = q.where(_component_exists(component_filters))
    if search:
        like = f"%{search}%"
        # Custom (misc_data) columns are configured per software version, so a
        # catalogue-wide search takes the union of every version's own fields.
        custom = [
            VendorDevice.misc_data[field.key].astext.ilike(like)
            for field in get_entity_fields(db, "vendor_devices")
            if field.storage == "data" and not field.sensitive
        ]
        q = q.where(or_(
            *[column.ilike(like) for column in CATALOG_TEXT_FILTERS.values()],
            Software.name.ilike(like),
            Software.version.ilike(like),
            _component_exists(_component_filters({}), search=search),
            *custom,
        ))
    custom_fields = {field.key: field for field in get_entity_fields(db, "vendor_devices")
                     if field.storage == "data" and field.field_type != "json"}
    # A software named rather than identified: the name covers every version of
    # it, which is what someone filtering by software means.
    name = params.get("software")
    if name:
        q = q.where(func.lower(Software.name) == str(name).lower())
    for key, value in params.items():
        if key in CATALOG_CONTROLS or value in (None, ""):
            continue
        if key.startswith(("exclude__", "include__")):
            keeping = key.startswith("include__")
            field = key.removeprefix("include__" if keeping else "exclude__")
            expression = CATALOG_SORT_COLUMNS.get(field)
            values = excluded_values(value)
            if field in custom_fields:
                expression = _custom_filter_expression(custom_fields[field])
                values = [coerce_query_value(custom_fields[field], item) for item in values]
            pick = include_clause if keeping else exclude_clause
            clause = pick(expression, values) if expression is not None else None
            if clause is not None:
                q = q.where(clause)
            continue
        if key in CATALOG_EXACT_FILTERS:
            q = q.where(CATALOG_EXACT_FILTERS[key] == value)
        elif key in CATALOG_TEXT_FILTERS:
            q = q.where(CATALOG_TEXT_FILTERS[key].ilike(f"%{value}%"))
        elif key == "software_name":
            q = q.where(Software.name.ilike(f"%{value}%"))
        elif key == "software_version":
            q = q.where(Software.version.ilike(f"%{value}%"))
    return q


def _catalog_rows(db: Session, pairs: list[tuple[VendorDevice, Software]],
                  filters: dict[str, str] | None = None,
                  search: str | None = None) -> list[VendorDeviceCatalogOut]:
    """Vendor claims with the software that makes them, and its place in the line.

    `software_is_latest` is resolved for the whole page in one query: the same
    fact `/software` reports on every row, so a claim on a superseded version
    reads as one here too rather than looking like current guidance.
    """
    if not pairs:
        return []
    names = {software.name.lower() for _vd, software in pairs}
    siblings: dict[str, list[Software]] = {}
    for row in db.scalars(select(Software).where(func.lower(Software.name).in_(names))).all():
        siblings.setdefault(row.name.lower(), []).append(row)
    latest = {
        name: (newest_version(rows).id if rows else None) for name, rows in siblings.items()
    }
    matched = _matching_components(db, [vd for vd, _software in pairs], filters or _component_filters({}), search)
    out = []
    for vendor_device, software in pairs:
        item = VendorDeviceCatalogOut.model_validate(vendor_device)
        item.software_name = software.name
        item.software_version = software.version or ""
        item.software_is_latest = latest.get(software.name.lower()) == software.id
        item.matching_components = [VendorComponentMatchOut.model_validate(match)
                                    for match in matched.get(vendor_device.id, [])]
        out.append(item)
    return out


@catalog_router.get("", response_model=Page[VendorDeviceCatalogOut])
@catalog_router.post("/query", response_model=Page[VendorDeviceCatalogOut])
def search_vendor_devices(
    request: Request,
    search: str | None = None,
    software: str | None = None,
    make: str | None = None,
    model: str | None = None,
    firmware_version: str | None = None,
    hardware_version: str | None = None,
    architecture: str | None = None,
    support_status: str | None = None,
    sort: str = "make",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    offset: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    filters_body: Annotated[dict[str, str] | None, Body()] = None,
):
    """Search every vendor compatibility list at once.

    These are claims, not evidence: a row here says a vendor published support
    for that hardware, whether or not this fleet owns one and whether or not it
    has ever been run. What has actually been tested lives in `/tests`.

    Paginates the two ways `/devices` does. `page` is what the UI sends;
    `offset` takes precedence when given and counts in rows, which is what a
    caller resuming a sweep at the row it stopped on needs.
    """
    q = _catalog_query(db, search, checklist_query(request, filters_body))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    column = CATALOG_SORT_COLUMNS.get(sort, VendorDevice.make)
    # Secondary sort keeps paging stable when the primary column repeats — and
    # it repeats constantly here, where one make covers hundreds of claims.
    q = q.order_by(
        column.desc() if order == "desc" else column.asc(),
        Software.name.asc(),
        VendorDevice.match_key.asc(),
    )
    start = offset if offset is not None else (page - 1) * page_size
    pairs = [tuple(row) for row in db.execute(q.offset(start).limit(page_size)).all()]
    return Page(
        items=_catalog_rows(db, pairs, _component_filters(checklist_query(request, filters_body)), search),
        total=total,
        page=start // page_size + 1,
        page_size=page_size,
    )


@catalog_router.get("/export")
def export_vendor_device_catalog(
    request: Request,
    format: str = "json",
    search: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The same search, as a file. Filters are the ones the list endpoint takes.

    The claim's columns come from the field catalog, so a custom vendor-device
    field is in the file — and, because this file is what the catalogue import
    reads, stays in the rows when it is read back. A fixed column list here
    would not merely omit the field: re-importing writes the whole row, so the
    absent column would read as "no value" and clear what an operator typed.
    """
    params = dict(request.query_params)
    q = _catalog_query(db, search, params)
    count = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    fields = _catalog_export_fields(db)
    columns = (CATALOG_SOFTWARE_COLUMNS + [field.key for field in fields]
               + list(FLAT_COMPONENT_FIELDS) + [COMPONENT_SUPPORT_FIELD])
    log_action(db, user, "vendor_devices.catalog_export", "vendor_device", None,
               {"format": format, "count": count}, request)
    db.commit()
    def rows(export_db: Session):
        query = _catalog_query(export_db, search, params).order_by(
            Software.name.asc(), VendorDevice.match_key.asc())
        result = export_db.execute(query.execution_options(yield_per=100))
        for batch in result.partitions(100):
            for item in _catalog_rows(export_db, [tuple(pair) for pair in batch]):
                row = item.model_dump(mode="json")
                yield {
                    "software_name": row["software_name"],
                    "software_version": row["software_version"],
                    **project_fields(row, fields, "misc_data"),
                    **{key: None for key in FLAT_COMPONENT_FIELDS},
                    COMPONENT_SUPPORT_FIELD: row[COMPONENT_SUPPORT_FIELD],
                }
    return streaming_export_response(database_export_rows(rows), columns, format, "vendor-devices")


# ---------- creating and importing from the catalogue ----------
#
# The write half of the cross-software page. Everything below reaches the same
# validation, dedupe and audit path as the software-scoped endpoints above —
# the only thing it adds is working out *which* software version each row is a
# claim by, which on those endpoints is already settled by the URL.


def _software_by_name_version(db: Session, name: str, version: str | None) -> Software:
    """The one software row with this name and this version.

    Not `_get_software`: that resolves a bare name to the newest version, which
    is right for a URL an operator typed and wrong for a file. A row that names
    a version this software does not have is an error rather than a claim
    quietly filed against whichever version happens to be current — the file
    said 2.1, and writing it to 3.0 would be inventing a claim nobody made.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("software_name is required")
    version = (version or "").strip()
    candidates = list(db.scalars(
        select(Software).where(func.lower(Software.name) == name.lower())
    ))
    if not candidates:
        raise ValueError(f"Unknown software: {name}")
    for software in candidates:
        if (software.version or "") == version:
            return software
    known = ", ".join(sorted(item.version or "(unversioned)" for item in candidates))
    raise ValueError(
        f"{candidates[0].name} has no version '{version or '(unversioned)'}' — it has {known}"
    )


@catalog_router.get("/template")
def vendor_device_catalog_template(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A blank CSV with the columns a catalogue import accepts.

    Carries `software_name` and `software_version` ahead of the claim's own
    fields, because a row here has to say which version is making the claim.

    The same columns the catalogue export writes, from the same helper, so a
    blank template and a filled export are the same shape and both import.
    Read-only fields are dropped: a column an import would ignore is a column
    that invites someone to fill it in for nothing.
    """
    fields = [field for field in _catalog_export_fields(db) if field.writable]
    columns = (CATALOG_SOFTWARE_COLUMNS + [field.key for field in fields]
               + list(FLAT_COMPONENT_FIELDS) + [COMPONENT_SUPPORT_FIELD])
    return download_response(
        template_csv(columns), "vendor-devices-template.csv", "text/csv",
    )


@catalog_router.post("", response_model=VendorDeviceCatalogOut, status_code=201)
def create_vendor_device_from_catalog(
    body: VendorDeviceCatalogCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    """Add a claim to a named software version, from the cross-software page."""
    values = body.model_dump()
    name = values.pop("software_name")
    version = values.pop("software_version", "")
    component_support = values.pop(COMPONENT_SUPPORT_FIELD, [])
    try:
        software = _software_by_name_version(db, name, version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        data = _validate_values(db, software, values)
        links = _resolve_component_support(
            db, software, [VendorComponentSupportIn(**item) for item in component_support],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    key = build_match_key(data)
    duplicate = _find_duplicate(db, software, key)
    if duplicate:
        if not links:
            raise HTTPException(
                status_code=409,
                detail=f"{software.name} already lists a vendor device matching {_describe(data)}",
            )
        _merge_component_support(duplicate, links)
        duplicate.updated_by = user.id
        duplicate.updated_at = utcnow()
        log_action(db, user, "software.vendor_device.update", "vendor_device", duplicate.id,
                   {"software_id": software.id, "from_catalog": True,
                    "component_support_added": component_support}, request)
        db.commit()
        db.refresh(duplicate)
        response.status_code = 200
        return _catalog_rows(db, [(duplicate, software)])[0]
    vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
    _apply(vd, data)
    _replace_component_support(vd, links)
    db.add(vd)
    log_action(
        db, user, "software.vendor_device.create", "vendor_device", vd.id,
        {"software_id": software.id, "from_catalog": True, **values}, request,
    )
    db.commit()
    db.refresh(vd)
    return _catalog_rows(db, [(vd, software)])[0]


@catalog_router.post("/import", response_model=ImportResult)
async def import_vendor_device_catalog(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    """Load vendor claims for any number of software versions at once.

    One file may name many versions; each row says which through its
    `software_name` and `software_version` columns. Within a version, rows are
    matched on the identity fields exactly as the per-software import matches
    them, so re-importing an updated file refreshes rows instead of duplicating
    them — and a row whose software cannot be resolved is reported as that row's
    error, leaving the rest of the file to apply.
    """
    rows = parse_import(await file.read(), file.filename or "")
    result = ImportResult(created=0, updated=0, errors=[])
    fields = get_entity_fields(db, "vendor_devices")
    known = {
        field.key for field in fields if field.storage != "data"
    } | IMPORT_IGNORED | {"misc_data", COMPONENT_SUPPORT_FIELD} | set(CATALOG_SOFTWARE_COLUMNS) | set(FLAT_COMPONENT_FIELDS)

    # The whole file is validated before anything is written, for the reason
    # the per-software import gives: rolling one bad row back mid-file would
    # undo the rows already applied while the counts still claimed them.
    parsed: list[tuple[Software, str, dict, list[tuple[SoftwareComponent, str]] | None]] = []
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            row = merge_extra_columns(row, known, "misc_data")
            cleaned = strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
            software = _software_by_name_version(
                db, cleaned.pop("software_name", ""), cleaned.pop("software_version", ""),
            )
            component_entries = _import_component_support(cleaned)
            body = VendorDeviceCreate(**cleaned)
            links = _resolve_component_support(db, software, component_entries) if component_entries is not None else None
            data = _validate_values(db, software, body.model_dump(exclude={COMPONENT_SUPPORT_FIELD}))
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        parsed.append((software, build_match_key(data), data, links))

    # Loaded once per software the file actually names, rather than once per
    # row: a file is usually a handful of versions and a great many claims.
    existing: dict[str, dict[str, VendorDevice]] = {}
    for software, _key, _data, _links in parsed:
        if software.id in existing:
            continue
        existing[software.id] = {
            vd.match_key: vd for vd in db.scalars(
                select(VendorDevice).where(VendorDevice.software_id == software.id)
            )
        }

    touched: set[tuple[str, str]] = set()
    for software, key, data, links in parsed:
        rows_for_software = existing[software.id]
        vd = rows_for_software.get(key)
        is_new = vd is None
        if is_new:
            vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
            db.add(vd)
            rows_for_software[key] = vd
        _apply(vd, data)
        if links is not None:
            _merge_component_support(vd, links)
        vd.updated_by = user.id
        if not is_new:
            vd.updated_at = utcnow()
        if (software.id, key) not in touched:
            result.created += 1 if is_new else 0
            result.updated += 0 if is_new else 1
            touched.add((software.id, key))

    log_action(
        db, user, "vendor_devices.catalog_import", "vendor_device", None,
        {
            "file": file.filename,
            "software_count": len(existing),
            "created": result.created,
            "updated": result.updated,
            "errors": result.errors,
        },
        request,
    )
    db.commit()
    return result


@catalog_router.post("/delete", status_code=204)
def delete_vendor_devices_from_catalog(
    body: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    """Delete claims by id, wherever they live.

    The per-software endpoint takes the software from the URL and ignores any
    id that does not belong to it, which is the right shape for one version's
    list. A selection made on the catalogue page spans versions by design — the
    same device claimed by three software is three rows — so each id is
    resolved to its own software here instead.

    Audited one row at a time, naming the software each belonged to, so the
    entries read the same as the ones the per-software delete writes.
    """
    for vd_id in body.ids:
        vd = db.get(VendorDevice, vd_id)
        if vd is None:
            continue
        log_action(
            db, user, "software.vendor_device.delete", "vendor_device", vd.id,
            {"software_id": vd.software_id, "snapshot": _vd_dict(vd),
             "bulk": True, "from_catalog": True},
            request,
        )
        db.delete(vd)
    db.commit()
    return None
