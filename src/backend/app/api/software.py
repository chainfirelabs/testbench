import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Device, Test, Software, User, VendorDevice
from ..models.vendor_device import build_match_key
from ..schemas import (
    BulkIds,
    BulkPayload,
    DeviceOut,
    ImportResult,
    Page,
    SoftwareCreate,
    SoftwareOut,
    SoftwareVersionCreate,
    SoftwareTestedDeviceOut,
    SoftwareTestedDevicesOut,
    SoftwareUpdate,
    VendorDeviceCreate,
)
from ..services.audit import field_diff, log_action
from ..services.io import download_response, export_response, parse_import, strip_nulls, template_csv
from ..services.query import row_error, row_scope
from ..services.entity_fields import coerce_query_value, entity_field_expression, entity_order_by, get_entity_fields, merge_extra_columns, project_fields, validate_custom_values
from .devices import device_out
from .deps import get_current_user, require_write

router = APIRouter(prefix="/software", tags=["software"])

# `vendor_devices` carries each row's whole compatibility list, so one export
# is the software *and* what the vendor claims it runs on. In CSV it lands in a
# single cell as a JSON array — the same way misc_data already travels — and
# the CSV reader decodes it back on import.
EXPORT_COLUMNS = [
    "id", "name", "version", "misc_data", "vendor_devices", "created_at", "updated_at",
]
EDITABLE_FIELDS = ["name", "version", "misc_data"]

# The nested vendor device fields. Deliberately only the editable ones: ids and
# timestamps belong to the instance that produced the file, and carrying them
# into another one is how a restore ends up with rows nothing can match.
VENDOR_DEVICE_FIELDS = [
    "vendor", "make", "model", "firmware_version", "hardware_version",
    "architecture", "support_status", "source", "notes", "misc_data",
]
# Server-owned or derived columns; an export re-imported as-is carries them.
# "target_count" is a leftover from a removed join table.
IMPORT_IGNORED = {
    "id", "created_at", "updated_at", "target_count", "vendor_device_count",
    # Derived from the row's place among the versions sharing its name.
    "version_count", "is_latest",
    # Not a software field at all: pulled out of the row and applied to the
    # vendor_devices table after the software row itself is settled.
    "vendor_devices",
}

# The columns an import template offers, required first.
TEMPLATE_COLUMNS = ["name", "version"]

_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:[-+](.*))?$")


def _version_key(version: str) -> tuple:
    """Sort dotted numeric versions naturally, with releases above prereleases.

    The catalogue permits free-form versions, so values outside that common
    grammar fall back to a case-folded natural sort. Numeric versions always
    outrank free-form and unversioned rows.
    """
    raw = (version or "").strip()
    if not raw:
        return (0,)
    match = _VERSION_RE.fullmatch(raw)
    if match:
        release = [int(part) for part in match.group(1).split(".")]
        while len(release) > 1 and release[-1] == 0:
            release.pop()
        suffix = match.group(2)
        # A final release is newer than its prerelease (2.0 > 2.0-rc1).
        return (2, tuple(release), 1 if suffix is None else 0, _natural_key(suffix or ""))
    return (1, _natural_key(raw))


def _natural_key(value: str) -> tuple:
    return tuple(
        (1, int(token)) if token.isdigit() else (0, token.casefold())
        for token in re.findall(r"\d+|\D+", value)
    )


def _newest_version(rows: list[Software]) -> Software | None:
    """Highest version, with creation time/id only breaking equal-version ties."""
    return max(rows, key=lambda row: (_version_key(row.version), row.created_at, row.id)) if rows else None


def _same_name(name: str):
    """Match a software name the way the unique index does — folded case.

    `Backup-Restore` and `backup-restore` are the same software, so every lookup,
    grouping and clash check has to compare on `lower(name)`.
    """
    return func.lower(Software.name) == (name or "").lower()


def _copy_vendor_devices(db: Session, source: Software, target: Software, actor_id: str) -> int:
    """Give `target` its own copy of `source`'s vendor devices.

    Copies, not shared rows: the two lists diverge from here, so a device added
    to the new version never appears on the old one and editing an inherited
    row leaves the original alone.
    """
    copied = 0
    for vd in source.vendor_devices:
        db.add(VendorDevice(
            software_id=target.id,
            vendor=vd.vendor,
            make=vd.make,
            model=vd.model,
            firmware_version=vd.firmware_version,
            hardware_version=vd.hardware_version,
            architecture=vd.architecture,
            support_status=vd.support_status,
            source=vd.source,
            notes=vd.notes,
            misc_data=dict(vd.misc_data or {}),
            # The identity fields are copied verbatim, so the dedupe key is too
            # — it is unique per (software_id, match_key) and the software differs.
            match_key=vd.match_key,
            created_by=actor_id,
            updated_by=actor_id,
        ))
        copied += 1
    return copied


def _version_taken(name: str, version: str) -> str:
    return (
        f"'{name}' already has a version '{version}'"
        if version
        else f"An unversioned '{name}' already exists"
    )


def _annotate(db: Session, software: list[Software]) -> list[SoftwareOut]:
    """Attach the version-group fields to a batch of rows.

    `version_count` and `is_latest` describe a row's place among the rows
    sharing its name. They are worked out for the whole batch in one query
    rather than per row.
    """
    out = [SoftwareOut.model_validate(t) for t in software]
    keys = {t.name.lower() for t in software}
    if not keys:
        return out
    siblings = list(db.scalars(select(Software).where(func.lower(Software.name).in_(keys))).all())
    groups: dict[str, list[Software]] = {}
    for sibling in siblings:
        groups.setdefault(sibling.name.lower(), []).append(sibling)
    for software, item in zip(software, out):
        versions = groups.get(software.name.lower(), [software])
        newest = _newest_version(versions)
        item.version_count = len(versions)
        item.is_latest = newest is not None and software.id == newest.id
    return out


def _software_dict(t: Software) -> dict:
    return SoftwareOut.model_validate(t).model_dump(mode="json")


def _vendor_device_rows(software: Software) -> list[dict]:
    """A software's compatibility list, as it travels inside an export row."""
    return [
        {f: getattr(vd, f) for f in VENDOR_DEVICE_FIELDS}
        for vd in sorted(software.vendor_devices, key=lambda v: v.match_key)
    ]


def _apply_vendor_devices(db: Session, software: Software, rows: list, actor_id: str) -> int:
    """Merge an imported compatibility list onto `software`.

    Merge, not replace: rows are matched on their identity fields the same way
    the per-software vendor device import matches them, so re-importing a file
    refreshes rows instead of duplicating them. A row the file omits is left
    alone rather than deleted — an import says what exists, not what does not,
    and the alternative turns a partial file into a silent purge.
    """
    if not isinstance(rows, list):
        raise ValueError("vendor_devices must be a list")
    existing = {vd.match_key: vd for vd in software.vendor_devices}
    touched = 0
    for entry in rows:
        if not isinstance(entry, dict):
            raise ValueError("each vendor_devices entry must be an object")
        # Tolerate a file produced by the per-software export, which carries
        # ids and timestamps this has no use for.
        data = VendorDeviceCreate(
            **strip_nulls({k: v for k, v in entry.items() if k in VENDOR_DEVICE_FIELDS})
        ).model_dump()
        key = build_match_key(data)
        vd = existing.get(key)
        if vd is None:
            vd = VendorDevice(software_id=software.id, created_by=actor_id)
            db.add(vd)
            existing[key] = vd
        for field, value in data.items():
            setattr(vd, field, value)
        vd.match_key = key
        vd.updated_by = actor_id
        touched += 1
    return touched


def _latest_of(db: Session, name: str) -> Software | None:
    """The highest version of the named software."""
    return _newest_version(list(db.scalars(select(Software).where(_same_name(name))).all()))


def _get_software(db: Session, key: str) -> Software | None:
    """Resolve software by its UUID or, failing that, its name (used in URLs).

    A bare name addresses the software's current version; a specific version is
    addressed by its UUID (the version switcher links by id).
    """
    software = db.get(Software, key)
    if software is None:
        software = _latest_of(db, key)
    return software


def _versions_of(db: Session, name: str) -> list[Software]:
    """Every version of the named software, highest first."""
    rows = list(db.scalars(select(Software).where(_same_name(name))).all())
    return sorted(rows, key=lambda row: (_version_key(row.version), row.created_at, row.id), reverse=True)


def _query_software(db: Session, search: str | None, latest_only: bool = False, filters: dict | None = None) -> select:
    q = select(Software)
    catalog = {field.key: field for field in get_entity_fields(db, "software")}
    if search:
        like = f"%{search}%"
        searchable = [
            entity_field_expression(Software, field).ilike(like)
            for field in get_entity_fields(db, "software")
            if field.field_type in {"text", "textarea", "select"}
            and field.key not in {"vendor_device_count", "version_count"}
        ]
        q = q.where(or_(*searchable) if searchable else Software.id.ilike(like))
    for key, value in (filters or {}).items():
        field = catalog.get(key)
        if not field or field.key in {"vendor_device_count", "version_count"}:
            continue
        expression = entity_field_expression(Software, field)
        value = coerce_query_value(field, value)
        q = q.where(expression == value if field.indexed else expression.ilike(f"%{value}%"))
    if latest_only:
        # Choose the current row from each complete version group, then apply
        # search to those rows. Searching for an older version must not promote
        # that version merely because its newer siblings did not match.
        candidates = list(db.scalars(select(Software)).all())
        groups: dict[str, list[Software]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.name.lower(), []).append(candidate)
        ids = [latest.id for versions in groups.values() if (latest := _newest_version(versions))]
        q = q.where(Software.id.in_(ids))
    return q


@router.get("", response_model=Page[SoftwareOut])
def list_software(
    request: Request,
    search: str | None = None,
    latest_only: bool = False,
    sort: str = "name",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = {
        key: value for key, value in request.query_params.items()
        if key not in {"search", "latest_only", "sort", "order", "page", "page_size"}
    }
    q = _query_software(db, search, latest_only, filters)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(entity_order_by(db, "software", Software, sort, order))
    items = list(db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all())
    return Page(items=_annotate(db, items), total=total, page=page, page_size=page_size)


@router.get("/export")
def export_software(
    format: str = "json",
    search: str | None = None,
    latest_only: bool = False,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = db.scalars(_query_software(db, search, latest_only).order_by(Software.name, Software.created_at)).all()
    rows = []
    fields = get_entity_fields(db, "software")
    vendor_total = 0
    for t in items:
        vendor_devices = _vendor_device_rows(t)
        vendor_total += len(vendor_devices)
        rows.append({
            **project_fields(_software_dict(t), fields, "misc_data"),
            "vendor_devices": vendor_devices,
        })
    log_action(
        db, user, "export.software", "software", None,
        {"format": format, "count": len(rows), "vendor_devices": vendor_total}, request,
    )
    db.commit()
    return export_response(rows, [field.key for field in fields] + ["vendor_devices"], format, "software")


@router.get("/template")
def software_template(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A blank CSV with the columns an import accepts — fill it in and import it."""
    columns = [
        field.key for field in get_entity_fields(db, "software")
        if field.writable and field.key != "misc_data"
    ]
    return download_response(template_csv(columns), "software-template.csv", "text/csv")


@router.get("/lookup/by-name", response_model=SoftwareOut)
def lookup_software(
    name: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Resolve a software name without using it as a URL path."""
    software = _latest_of(db, name)
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    return _annotate(db, [software])[0]


@router.get("/{software_id}", response_model=SoftwareOut)
def get_software(software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    software = _get_software(db, software_id)
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    return _annotate(db, [software])[0]


@router.get("/{software_id}/tested-devices", response_model=SoftwareTestedDevicesOut)
def get_tested_devices(software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Devices this software has actually been tested against (from the tests table)."""
    software = _get_software(db, software_id)
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    rows = db.execute(
        select(
            Test.device_id,
            func.count(Test.id),
            func.max(Test.run_at),
            Test.outcome,
        )
        .where(Test.software_id == software.id)
        .group_by(Test.device_id, Test.outcome)
    ).all()
    by_device: dict[str, dict] = {}
    for device_id, count, last_at, outcome in rows:
        entry = by_device.setdefault(device_id, {"count": 0, "last": None, "outcomes": {}})
        entry["count"] += count
        if last_at and (entry["last"] is None or last_at > entry["last"]):
            entry["last"] = last_at
        entry["outcomes"][outcome] = entry["outcomes"].get(outcome, 0) + count
    devices = list(
        db.scalars(select(Device).where(Device.id.in_(by_device))).all()
    ) if by_device else []
    devices.sort(key=lambda d: d.unique_id)
    cache: dict = {}
    return SoftwareTestedDevicesOut(
        software_id=software.id,
        devices=[
            SoftwareTestedDeviceOut(
                device=device_out(db, d, cache),
                test_count=by_device[d.id]["count"],
                last_test_at=by_device[d.id]["last"],
                outcomes=by_device[d.id]["outcomes"],
            )
            for d in devices
        ],
    )


@router.get("/{software_id}/versions", response_model=list[SoftwareOut])
def list_versions(software_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every version of this software, newest first."""
    software = _get_software(db, software_id)
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    return _annotate(db, _versions_of(db, software.name))


@router.post("/{software_id}/versions", response_model=SoftwareOut, status_code=201)
def create_version(
    software_id: str,
    body: SoftwareVersionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    """Add a version, starting from the one it branches off.

    The new version inherits a *copy* of the source version's vendor devices,
    so the two diverge from here: a device added to the new version does not
    appear on the older one, and editing one list leaves the other alone.
    """
    source = _get_software(db, software_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Software not found")

    version = (body.version or "").strip()
    if not version:
        raise HTTPException(status_code=400, detail="A new version needs a version string")
    if db.scalar(select(Software.id).where(_same_name(source.name), Software.version == version)):
        raise HTTPException(status_code=409, detail=_version_taken(source.name, version))

    software = Software(
        name=source.name,
        version=version,
        misc_data=body.misc_data if body.misc_data is not None else dict(source.misc_data or {}),
    )
    db.add(software)
    db.flush()  # the copies below need the new row's id

    copied = _copy_vendor_devices(db, source, software, user.id) if body.copy_vendor_devices else 0

    log_action(
        db, user, "software.version.create", "software", software.id,
        {
            "name": software.name,
            "version": software.version,
            "from_version": source.version,
            "from_software_id": source.id,
            "vendor_devices_copied": copied,
        },
        request,
    )
    db.commit()
    db.refresh(software)
    return _annotate(db, [software])[0]


@router.post("", response_model=SoftwareOut, status_code=201)
def create_software(
    body: SoftwareCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    # A name that already exists is a new *version* of that software, and versions
    # inherit a copy of the vendor device list. Creating one here would make a
    # version with an empty list and no sign that anything was skipped, so the
    # caller is sent to the endpoint that does the copying.
    existing = _latest_of(db, body.name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{existing.name}' already exists (current version "
                f"{existing.version or 'unversioned'}). Add a version with "
                f"POST /software/{existing.id}/versions so it inherits the vendor devices."
            ),
        )
    try:
        data = validate_custom_values(db, "software", body.model_dump(), "misc_data")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    software = Software(**data)
    db.add(software)
    log_action(db, user, "software.create", "software", software.id, body.model_dump(mode="json"), request)
    db.commit()
    db.refresh(software)
    return _annotate(db, [software])[0]


@router.patch("/{software_id}", response_model=SoftwareOut)
def update_software(
    software_id: str,
    body: SoftwareUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    software = _get_software(db, software_id)
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    try:
        updates = validate_custom_values(
            db, "software", body.model_dump(exclude_unset=True), "misc_data", partial=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    new_name = updates.get("name", software.name)
    new_version = updates.get("version", software.version)
    renaming = new_name != software.name

    # Names match case-insensitively, so re-spelling a software's own name
    # (backup-restore -> Backup-Restore) is a restyle of this same software, not a
    # move onto another one — it renames the group without a clash.
    restyling = renaming and new_name.lower() == software.name.lower()

    if renaming and not restyling:
        # Rows sharing a name are the same software, so a rename has to move all of them
        # — renaming a single row would split the software's history in two.
        if db.scalar(select(Software.id).where(_same_name(new_name))):
            raise HTTPException(
                status_code=409,
                detail=f"Software named '{new_name}' already exists",
            )
    elif not renaming and new_version != software.version:
        if db.scalar(
            select(Software.id).where(
                _same_name(new_name), Software.version == new_version, Software.id != software.id
            )
        ):
            raise HTTPException(status_code=409, detail=_version_taken(new_name, new_version))

    old = {f: getattr(software, f) for f in EDITABLE_FIELDS}
    siblings = _versions_of(db, software.name) if renaming else []
    for field, value in updates.items():
        setattr(software, field, value)
    software.updated_at = utcnow()

    for sibling in siblings:
        if sibling.id == software.id:
            continue
        sibling.name = new_name
        sibling.updated_at = utcnow()

    detail = {"diff": field_diff(old, {f: getattr(software, f) for f in EDITABLE_FIELDS})}
    if renaming and len(siblings) > 1:
        detail["renamed_versions"] = [s.version for s in siblings]
    log_action(db, user, "software.update", "software", software.id, detail, request)
    db.commit()
    db.refresh(software)
    return _annotate(db, [software])[0]


@router.delete("/{software_id}", status_code=204)
def delete_software(
    software_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    software = _get_software(db, software_id)
    if software is None:
        raise HTTPException(status_code=404, detail="Software not found")
    log_action(db, user, "software.delete", "software", software.id, {"snapshot": _software_dict(software)}, request)
    db.delete(software)
    db.commit()


@router.post("/delete", status_code=204)
def delete_software_bulk(
    body: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    """Delete multiple software records by id (multi-select delete)."""
    for software_id in body.ids:
        software = db.get(Software, software_id)
        if software:
            log_action(db, user, "software.delete", "software", software.id, {"snapshot": _software_dict(software), "bulk": True}, request)
            db.delete(software)
    db.commit()
    return None


@router.post("/bulk")
def bulk_software(
    body: BulkPayload,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    created = updated = deleted = 0
    errors: list[dict] = []
    for i, row in enumerate(body.upserts):
        try:
            with row_scope(db):
                data = SoftwareCreate(**{k: v for k, v in row.items() if k != "id"}).model_dump()
                data = validate_custom_values(db, "software", data, "misc_data")
                software = db.get(Software, row.get("id")) if row.get("id") else None
                if software is None:
                    # Match the way the unique index does, so re-sending a row
                    # updates that version instead of failing the whole commit.
                    software = db.scalar(
                        select(Software).where(
                            _same_name(data["name"]), Software.version == data["version"]
                        )
                    )
                if software is None:
                    software = Software(**data)
                    db.add(software)
                    outcome = "created"
                else:
                    for field, value in data.items():
                        setattr(software, field, value)
                    software.updated_at = utcnow()
                    outcome = "updated"
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": i, "error": row_error(exc)})
            continue
        created += outcome == "created"
        updated += outcome == "updated"
    for software_id in body.delete_ids:
        software = db.get(Software, software_id)
        if software:
            db.delete(software)
            deleted += 1
        else:
            errors.append({"row": None, "error": f"software {software_id} not found"})
    log_action(db, user, "software.bulk", "software", None,
               {"created": created, "updated": updated, "deleted": deleted, "errors": errors}, request)
    db.commit()
    return {"created": created, "updated": updated, "deleted": deleted, "errors": errors}


@router.post("/import", response_model=ImportResult)
async def import_software(
    file: UploadFile,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    content = await file.read()
    filename = file.filename or ""
    rows = parse_import(content, filename)
    is_csv = filename.lower().endswith(".csv")
    result = ImportResult(created=0, updated=0, errors=[])
    vendor_applied = 0
    for i, row in enumerate(rows):
        try:
            with row_scope(db):
                if not isinstance(row, dict):
                    raise ValueError("row must be an object")
                if is_csv:
                    configured = {field.key for field in get_entity_fields(db, "software")}
                    known = configured | {"misc_data", "vendor_devices"} | IMPORT_IGNORED
                    row = merge_extra_columns(row, known, "misc_data")
                data = SoftwareCreate(
                    **strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
                ).model_dump()
                data = validate_custom_values(db, "software", data, "misc_data")
                software = db.get(Software, row["id"]) if row.get("id") else None
                if software is None:
                    # A hand-filled template has no ids, so match the way the unique
                    # index does — on (lower(name), version). Re-importing an edited
                    # file then updates that version instead of failing at commit.
                    software = db.scalar(
                        select(Software).where(
                            _same_name(data["name"]), Software.version == data["version"]
                        )
                    )
                # An explicit list in the file is the authority on this row's
                # vendor devices; absent, the inheritance rule below applies.
                vendor_devices = row.get("vendor_devices") if isinstance(row, dict) else None

                if software is not None:
                    for field, value in data.items():
                        setattr(software, field, value)
                    outcome = "updated"
                else:
                    # No such version. If the *name* is known this row is a new
                    # version of an existing software, and versions inherit the vendor
                    # device list — importing one must not quietly produce an empty one.
                    previous = _latest_of(db, data["name"])
                    software = Software(**data)
                    if previous is not None:
                        # Keep the software's established spelling; only matching folds case.
                        software.name = previous.name
                    db.add(software)
                    if previous is not None:
                        db.flush()  # the copies need the new row's id
                        # Skipped when the file states the list itself: inheriting
                        # the previous version's rows *and* applying the file's
                        # would leave the union of two lists, which is neither.
                        if vendor_devices is None:
                            _copy_vendor_devices(db, previous, software, user.id)
                    outcome = "created"

                if vendor_devices is not None:
                    db.flush()  # a new software row needs its id before rows hang off it
                    vendor_applied += _apply_vendor_devices(
                        db, software, vendor_devices, user.id
                    )
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        result.created += outcome == "created"
        result.updated += outcome == "updated"
    log_action(db, user, "software.import", "software", None,
               {"file": file.filename, "created": result.created, "updated": result.updated,
                "vendor_devices": vendor_applied, "errors": result.errors}, request)
    db.commit()
    return result
