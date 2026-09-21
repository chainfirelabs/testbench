"""Vendor devices: the hardware a vendor claims their software works against.

Scoped to one software version and imported separately from the device inventory — a
vendor device is a claim on a compatibility list, not something we own. What
the software has actually been run against comes from `tests` instead
(see `/software/{software_id}/tested-devices`).
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import EntityField, Software, User, VendorDevice, VendorDeviceFieldOverride
from ..models.vendor_device import build_match_key
from ..schemas import (
    BulkIds,
    ImportResult,
    Page,
    VendorDeviceCatalogCreate,
    VendorDeviceCatalogOut,
    VendorDeviceCreate,
    VendorDeviceOut,
    VendorDeviceUpdate,
)
from ..services.audit import field_diff, log_action
from ..services.io import (
    download_response,
    parse_import,
    safe_filename,
    streaming_export_response,
    strip_nulls,
    template_csv,
)
from ..services.list_filters import exclude_clause, excluded_values, include_clause
from ..services.query import order_by, row_error
from ..services.versions import newest_version
from ..services.entity_fields import (
    field_payload, get_entity_fields, merge_extra_columns, project_fields,
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

# Fields the API owns; an import file carrying them is ignored rather than rejected.
IMPORT_IGNORED = {"id", "software_id", "vendor", "match_key", "created_at", "updated_at", "created_by", "updated_by"}

# The columns an import template offers. None is required on its own, but a row
# has to say something about which device it describes (see build_match_key).
TEMPLATE_COLUMNS = EDITABLE_FIELDS

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


def _query(db: Session, software: Software, search: str | None):
    q = select(VendorDevice).where(VendorDevice.software_id == software.id)
    if search:
        like = f"%{search}%"
        custom = [
            VendorDevice.misc_data[field.key].astext.ilike(like)
            for field, _payload in _effective_fields(db, software)
            if field.storage == "data" and not field.sensitive
        ]
        q = q.where(or_(
            *[getattr(VendorDevice, f).ilike(like) for f in SEARCH_FIELDS],
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
def list_grouped_vendor_devices(
    software_id: str,
    search: str | None = None,
    sort: str = "make",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Page the presentation groups while retaining every firmware member."""
    software = _get_software(db, software_id)
    source_query = _query(db, software, search)
    standard = {key for key in EDITABLE_FIELDS if key != "misc_data"}
    custom = {field.key for field, _payload in _effective_fields(db, software) if field.storage == "data"}
    for key, value in request.query_params.items():
        if key in {"search", "sort", "order", "page", "page_size"} or not value:
            continue
        excluded = key.startswith("exclude__")
        included = key.startswith("include__")
        field = key.removeprefix("exclude__" if excluded else "include__") if excluded or included else key
        expression = (getattr(VendorDevice, field) if field in standard else
                      VendorDevice.misc_data[field].astext if field in custom else None)
        if expression is None:
            continue
        if excluded or included:
            clause = (exclude_clause if excluded else include_clause)(expression, excluded_values(value))
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
    members = db.scalars(select(VendorDevice).where(
        VendorDevice.software_id == software.id, or_(*clauses),
    )).all()
    by_key: dict[tuple[str, str, str], list[dict]] = {}
    for member in members:
        payload = _vd_dict(member)
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
    sort: str = "make",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    software = _get_software(db, software_id)
    q = _query(db, software, search)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    # Secondary sort keeps paging stable when the primary column repeats.
    q = q.order_by(order_by(VendorDevice, sort, order, "make"), VendorDevice.match_key.asc())
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(
        items=[VendorDeviceOut.model_validate(v) for v in items],
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
    items = db.scalars(_query(db, software, search).order_by(VendorDevice.match_key)).all()
    fields = [field for field, payload in _effective_fields(db, software) if payload["visible"]]
    rows = [project_fields(_vd_dict(v), fields, "misc_data") for v in items]
    log_action(
        db, user, "software.vendor_devices.export", "software", software.id,
        {"format": format, "count": len(rows)}, request,
    )
    db.commit()
    # The stem carries a software name, which is free text: safe_filename keeps a
    # quote or newline in it from breaking out of the Content-Disposition header.
    return streaming_export_response(rows, [field.key for field in fields], format, f"{software.name}-vendor-devices")


@router.get("/template")
def vendor_device_template(software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A blank CSV with the columns an import accepts — fill it in and import it."""
    software = _get_software(db, software_id)
    name = safe_filename(f"{software.name}-vendor-devices-template", "csv")
    columns = [
        field.key for field, payload in _effective_fields(db, software)
        if payload["visible"] and field.writable and field.key != "misc_data"
    ]
    return download_response(template_csv(columns), name, "text/csv")


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
    parsed: list[tuple[str, dict]] = []
    fields = get_entity_fields(db, "vendor_devices")
    # JSON-backed catalog fields (and still-unknown spreadsheet columns) are
    # folded into misc_data; only physical columns remain top-level.
    known = {
        field.key for field in fields if field.storage != "data"
    } | IMPORT_IGNORED | {"misc_data"}
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            row = merge_extra_columns(row, known, "misc_data")
            data = VendorDeviceCreate(
                **strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
            ).model_dump()
            data = _validate_values(db, software, data)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        parsed.append((build_match_key(data), data))

    existing = {
        vd.match_key: vd
        for vd in db.scalars(select(VendorDevice).where(VendorDevice.software_id == software.id))
    }
    # Rows this file has already applied, so a file listing the same device
    # twice updates one row instead of tripping the unique constraint.
    touched: set[str] = set()
    for key, data in parsed:
        vd = existing.get(key)
        is_new = vd is None
        if is_new:
            vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
            db.add(vd)
            existing[key] = vd
        _apply(vd, data)
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
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    software = _get_software(db, software_id)
    try:
        data = _validate_values(db, software, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    key = build_match_key(data)
    if _find_duplicate(db, software, key):
        raise HTTPException(
            status_code=409,
            detail=f"{software.name} already lists a vendor device matching {_describe(data)}",
        )
    vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
    _apply(vd, data)
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
    try:
        data = _validate_values(db, software, body.model_dump(exclude_unset=True), partial=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _apply(vd, data)
    if _find_duplicate(db, software, vd.match_key, exclude_id=vd.id):
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{software.name} already lists a vendor device matching "
                   f"{_describe({f: getattr(vd, f) for f in EDITABLE_FIELDS})}",
        )
    vd.updated_by = user.id
    vd.updated_at = utcnow()
    log_action(
        db, user, "software.vendor_device.update", "vendor_device", vd.id,
        {"software_id": software.id, "diff": field_diff(old, {f: getattr(vd, f) for f in EDITABLE_FIELDS})},
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
        if field.key != "misc_data"
    ]

# Query parameters that steer the request rather than filter it.
CATALOG_CONTROLS = {"search", "sort", "order", "page", "page_size", "offset", "format", "software"}


def _catalog_query(db: Session, search: str | None, params: dict):
    """Vendor claims across every software, filtered by `?column=value`.

    Joined to `software` rather than resolved per row: the software's name is
    part of what a catalogue row *is*, and it is also something to search and
    sort on — "every claim Backup-Restore makes" is the same question as "every
    claim about a Cisco", asked of the other side of the join.
    """
    q = select(VendorDevice, Software).join(Software, VendorDevice.software_id == Software.id)
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
            *custom,
        ))
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
            pick = include_clause if keeping else exclude_clause
            clause = pick(expression, excluded_values(value)) if expression is not None else None
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


def _catalog_rows(db: Session, pairs: list[tuple[VendorDevice, Software]]) -> list[VendorDeviceCatalogOut]:
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
    out = []
    for vendor_device, software in pairs:
        item = VendorDeviceCatalogOut.model_validate(vendor_device)
        item.software_name = software.name
        item.software_version = software.version or ""
        item.software_is_latest = latest.get(software.name.lower()) == software.id
        out.append(item)
    return out


@catalog_router.get("", response_model=Page[VendorDeviceCatalogOut])
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
):
    """Search every vendor compatibility list at once.

    These are claims, not evidence: a row here says a vendor published support
    for that hardware, whether or not this fleet owns one and whether or not it
    has ever been run. What has actually been tested lives in `/tests`.

    Paginates the two ways `/devices` does. `page` is what the UI sends;
    `offset` takes precedence when given and counts in rows, which is what a
    caller resuming a sweep at the row it stopped on needs.
    """
    q = _catalog_query(db, search, dict(request.query_params))
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
        items=_catalog_rows(db, pairs),
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
    q = _catalog_query(db, search, dict(request.query_params))
    q = q.order_by(Software.name.asc(), VendorDevice.match_key.asc())
    pairs = [tuple(row) for row in db.execute(q).all()]
    fields = _catalog_export_fields(db)
    columns = CATALOG_SOFTWARE_COLUMNS + [field.key for field in fields]
    rows = []
    for item in _catalog_rows(db, pairs):
        row = item.model_dump(mode="json")
        rows.append({
            "software_name": row["software_name"],
            "software_version": row["software_version"],
            **project_fields(row, fields, "misc_data"),
        })
    log_action(db, user, "vendor_devices.catalog_export", "vendor_device", None,
               {"format": format, "count": len(rows)}, request)
    db.commit()
    return streaming_export_response(rows, columns, format, "vendor-devices")


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
    columns = CATALOG_SOFTWARE_COLUMNS + [field.key for field in fields]
    return download_response(
        template_csv(columns), "vendor-devices-template.csv", "text/csv",
    )


@catalog_router.post("", response_model=VendorDeviceCatalogOut, status_code=201)
def create_vendor_device_from_catalog(
    body: VendorDeviceCatalogCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_software_edit),
):
    """Add a claim to a named software version, from the cross-software page."""
    values = body.model_dump()
    name = values.pop("software_name")
    version = values.pop("software_version", "")
    try:
        software = _software_by_name_version(db, name, version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        data = _validate_values(db, software, values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    key = build_match_key(data)
    if _find_duplicate(db, software, key):
        raise HTTPException(
            status_code=409,
            detail=f"{software.name} already lists a vendor device matching {_describe(data)}",
        )
    vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
    _apply(vd, data)
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
    } | IMPORT_IGNORED | {"misc_data"} | set(CATALOG_SOFTWARE_COLUMNS)

    # The whole file is validated before anything is written, for the reason
    # the per-software import gives: rolling one bad row back mid-file would
    # undo the rows already applied while the counts still claimed them.
    parsed: list[tuple[Software, str, dict]] = []
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            row = merge_extra_columns(row, known, "misc_data")
            cleaned = strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
            software = _software_by_name_version(
                db, cleaned.pop("software_name", ""), cleaned.pop("software_version", ""),
            )
            data = _validate_values(db, software, VendorDeviceCreate(**cleaned).model_dump())
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        parsed.append((software, build_match_key(data), data))

    # Loaded once per software the file actually names, rather than once per
    # row: a file is usually a handful of versions and a great many claims.
    existing: dict[str, dict[str, VendorDevice]] = {}
    for software, _key, _data in parsed:
        if software.id in existing:
            continue
        existing[software.id] = {
            vd.match_key: vd for vd in db.scalars(
                select(VendorDevice).where(VendorDevice.software_id == software.id)
            )
        }

    touched: set[tuple[str, str]] = set()
    for software, key, data in parsed:
        rows_for_software = existing[software.id]
        vd = rows_for_software.get(key)
        is_new = vd is None
        if is_new:
            vd = VendorDevice(software_id=software.id, created_by=user.id, updated_by=user.id)
            db.add(vd)
            rows_for_software[key] = vd
        _apply(vd, data)
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
