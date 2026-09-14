"""Vendor devices: the hardware a vendor claims their software works against.

Scoped to one software version and imported separately from the device inventory — a
vendor device is a claim on a compatibility list, not something we own. What
the software has actually been run against comes from `tests` instead
(see `/software/{software_id}/tested-devices`).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Software, User, VendorDevice
from ..models.vendor_device import build_match_key
from ..schemas import (
    BulkIds,
    ImportResult,
    Page,
    VendorDeviceCreate,
    VendorDeviceOut,
    VendorDeviceUpdate,
)
from ..services.audit import field_diff, log_action
from ..services.io import (
    download_response,
    export_response,
    parse_import,
    safe_filename,
    strip_nulls,
    template_csv,
)
from ..services.query import order_by, row_error
from .deps import get_current_user, require_write

router = APIRouter(prefix="/software/{software_id}/vendor-devices", tags=["vendor-devices"])

EXPORT_COLUMNS = [
    "id",
    "vendor",
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
    "vendor",
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
IMPORT_IGNORED = {"id", "software_id", "match_key", "created_at", "updated_at", "created_by", "updated_by"}

# The columns an import template offers. None is required on its own, but a row
# has to say something about which device it describes (see build_match_key).
TEMPLATE_COLUMNS = EDITABLE_FIELDS

SEARCH_FIELDS = ("vendor", "make", "model", "firmware_version", "hardware_version", "architecture", "source", "notes")


def _vd_dict(vd: VendorDevice) -> dict:
    return VendorDeviceOut.model_validate(vd).model_dump(mode="json")


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
    for f in ("vendor", "make", "model"):
        part = str(values[f]).strip() if values.get(f) else ""
        # Vendor and make are usually the same word; say it once.
        if part and part.lower() not in (p.lower() for p in parts):
            parts.append(part)
    return " ".join(parts) or "(blank)"


def _query(db: Session, software: Software, search: str | None):
    q = select(VendorDevice).where(VendorDevice.software_id == software.id)
    if search:
        like = f"%{search}%"
        q = q.where(or_(*[getattr(VendorDevice, f).ilike(like) for f in SEARCH_FIELDS]))
    return q


@router.get("", response_model=Page[VendorDeviceOut])
def list_vendor_devices(
    software_id: str,
    search: str | None = None,
    sort: str = "vendor",
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
    q = q.order_by(order_by(VendorDevice, sort, order, "vendor"), VendorDevice.match_key.asc())
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
    rows = [_vd_dict(v) for v in items]
    log_action(
        db, user, "software.vendor_devices.export", "software", software.id,
        {"format": format, "count": len(rows)}, request,
    )
    db.commit()
    # The stem carries a software name, which is free text: safe_filename keeps a
    # quote or newline in it from breaking out of the Content-Disposition header.
    return export_response(rows, EXPORT_COLUMNS, format, f"{software.name}-vendor-devices")


@router.get("/template")
def vendor_device_template(software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A blank CSV with the columns an import accepts — fill it in and import it."""
    software = _get_software(db, software_id)
    name = safe_filename(f"{software.name}-vendor-devices-template", "csv")
    return download_response(template_csv(TEMPLATE_COLUMNS), name, "text/csv")


@router.post("/import", response_model=ImportResult)
async def import_vendor_devices(
    software_id: str,
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    """Load a vendor compatibility list.

    Rows are matched on the identity fields (vendor/make/model/firmware/
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
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            data = VendorDeviceCreate(
                **strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
            ).model_dump()
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
    user: User = Depends(require_write),
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
    user: User = Depends(require_write),
):
    software = _get_software(db, software_id)
    data = body.model_dump()
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
    user: User = Depends(require_write),
):
    software = _get_software(db, software_id)
    vd = _get_vd(db, software, vd_id)
    old = {f: getattr(vd, f) for f in EDITABLE_FIELDS}
    _apply(vd, body.model_dump(exclude_unset=True))
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
    user: User = Depends(require_write),
):
    software = _get_software(db, software_id)
    vd = _get_vd(db, software, vd_id)
    log_action(
        db, user, "software.vendor_device.delete", "vendor_device", vd.id,
        {"software_id": software.id, "snapshot": _vd_dict(vd)}, request,
    )
    db.delete(vd)
    db.commit()
