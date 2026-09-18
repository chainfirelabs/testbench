import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..models import Device, DeviceType, Test, User
from ..schemas import (
    BulkIds,
    BulkPayload,
    DeviceCompatibilityOut,
    DeviceCompatibleSoftwareOut,
    DeviceCreate,
    DeviceDetail,
    DeviceInfoConfigOut,
    DeviceInfoLaunchOut,
    DeviceOut,
    DeviceOverdueOut,
    DeviceRelatedCountsOut,
    DeviceScanResult,
    DeviceScanState,
    DeviceUpdate,
    ImportResult,
    Page,
    ScanStatusOut,
    SoftwareOut,
    TestOut,
    VendorDeviceOut,
)
from ..services.audit import field_diff, log_action
from ..services.checkout import overdue_clause, today
from ..services.compat import compatible_software
from ..services.hooks import emit_status_change
from ..services.io import download_response, export_response, parse_import, strip_nulls, template_csv
from ..services.list_filters import exclude_clause, excluded_values
from ..services.query import row_error, row_scope
from ..services.device_info import DockerError, launch_device_info, missing_required_fields
from ..services.device_schema import (
    DeviceValidationError,
    apply_defaults,
    coerce_filter_value,
    current_revision,
    device_field_expression,
    device_order_by,
    get_available_actions,
    get_effective_fields,
    layout_keys,
    REDACTED,
    redact_sensitive,
    union_field_map,
    validate_device_document,
)
from ..services.scan import manager, scan_device
from .deps import get_current_user, require_write

router = APIRouter(prefix="/devices", tags=["devices"])

# Structural columns: identity, type, ownership and timestamps. Everything
# else about a device is installation-defined and comes from the published
# schema, so there is no list of inventory columns here any more.
STRUCTURAL_COLUMNS = ["id", "unique_id", "device_type"]

# Keys that are part of the envelope rather than the document, and so are never
# treated as a field value however a caller spells them.
ENVELOPE_KEYS = {
    "id", "data", "misc_data", "device_type", "device_type_id", "device_type_key",
    "device_type_label", "unique_id", "created_at", "updated_at", "created_by",
    "updated_by", "checked_out_by", "checked_out_by_username", "schema_revision",
    "verified_tests", "all_tests", "days_overdue",
}

# Server-owned columns; an export re-imported as-is carries them.
IMPORT_IGNORED = {"id", "created_at", "updated_at", "checked_out_by", "checked_out_by_username", "checked_out_at"}


def _get_device(db: Session, key: str) -> Device | None:
    """Resolve a device by its UUID or, failing that, its unique_id (used in URLs)."""
    device = db.get(Device, key)
    if device is None:
        device = db.scalar(select(Device).where(Device.unique_id == key))
    return device


def device_misc_data(db: Session, device: Device, cache: dict | None = None) -> dict:
    """The part of a device's document its type's page does not account for.

    Not on the ORM object, and it cannot be: it is the document minus whatever
    this device's *type* shows, so resolving it needs the session. The model's
    own `misc_data` predates installation-defined fields and subtracts a frozen
    list of legacy attribute names instead, which reports `username`,
    `serial_number` and every field an installation invented as unexplained
    data. Every read path that serves a client comes through here.

    Pass `cache` when serialising more than one device so the layout behind them
    is resolved once per device type rather than once per row.
    """
    return device.misc_data_beyond(layout_keys(db, device.device_type_id, cache))


def device_out(db: Session, device: Device, cache: dict | None = None) -> DeviceOut:
    """One device as the API returns it."""
    out = DeviceOut.model_validate(device)
    out.misc_data = device_misc_data(db, device, cache)
    return out


def _device_dict(db: Session, d: Device, cache: dict | None = None) -> dict:
    return device_out(db, d, cache).model_dump(mode="json")


def _redact_diff(db: Session, device: Device, diff: dict) -> dict:
    """Mask the values inside an audit diff, keeping which fields changed."""
    sensitive = {
        field.key for field in get_effective_fields(db, device.device_type_id) if field.sensitive
    }
    return {
        key: ({side: (REDACTED if value not in (None, "") else value)
               for side, value in change.items()} if key in sensitive else change)
        for key, change in diff.items()
    }


def _audit_snapshot(db: Session, device: Device) -> dict:
    """What a device looked like, for the audit log, with secrets masked.

    Enough to see what was deleted and, if it has to be recreated, to know what
    it held — minus the credentials, which a deletion record has no business
    carrying.
    """
    return {
        "id": device.id,
        "unique_id": device.unique_id,
        "device_type": device.device_type_key,
        "checked_out_by_username": device.checked_out_by_username,
        "data": redact_sensitive(db, device.device_type_id, device.data),
    }


def _envelope(body) -> tuple[dict, str | None, bool]:
    """Split a device request into its document and its device type.

    Three spellings all mean the same thing and all arrive here: the `data`
    envelope the API documents, the flattened top-level fields older clients
    and CSV imports send, and the legacy `misc_data` bucket. `data` wins where
    they disagree, because it is the one a caller wrote deliberately.
    """
    payload = body if isinstance(body, dict) else body.model_dump(exclude_unset=True)
    flattened = {key: value for key, value in payload.items() if key not in ENVELOPE_KEYS}
    document = {**flattened, **(payload.get("misc_data") or {}), **(payload.get("data") or {})}
    supplied = "device_type" in payload or "device_type_id" in payload
    value = payload.get("device_type_id") if "device_type_id" in payload else payload.get("device_type")
    return document, value, supplied


def _resolve_device_type(db: Session, value: str | None, *, allow_disabled: bool = False) -> DeviceType | None:
    if value is None or value == "":
        return None
    item = db.get(DeviceType, value) or db.scalar(select(DeviceType).where(DeviceType.key == value))
    if item is None:
        raise ValueError(f"Unknown device type: {value}")
    if not item.enabled and not allow_disabled:
        raise ValueError(f"Device type '{item.key}' is disabled")
    return item


def _write_document(
    db: Session,
    device: Device,
    document: dict,
    device_type: DeviceType | None,
    *,
    creating: bool,
    changing_type: bool = False,
) -> dict:
    """Validate one document against its type's schema and apply it.

    The single write path. Everything — the API, bulk edits, CSV and JSON
    imports — arrives here, so "is this a valid device?" has exactly one
    answer. Status is applied separately by the caller because crossing the
    checked_out boundary has its own rules, and those need the old value.

    Changing a device's type revalidates the whole document against the type it
    is moving to. It never deletes anything: values belonging to fields the new
    type does not show stay in the document, hidden until something shows them
    again.
    """
    type_id = device_type.id if device_type else None
    validated = validate_device_document(
        db, type_id, document,
        partial=not creating,
        device_id=None if creating else device.id,
        existing=device.data,
        enforce_required=creating or changing_type,
    )
    if creating:
        validated = apply_defaults(get_effective_fields(db, type_id), validated)
    status = validated.pop("status", None)
    device.merge_data(validated)
    return {"status": status, "document": validated}


SCAN_ADDRESS_ROLES = ("scan_address_wan", "scan_address_lan")


def _require_scan_addresses(db: Session, device: Device | None = None) -> None:
    """A scan needs somewhere to knock. Ask by role, not by field name.

    An installation is free to call its addresses anything; what the scanner
    needs is a field carrying the meaning "an address this device answers on".
    """
    scopes = {device.device_type_id} if device is not None else {
        None, *db.scalars(select(Device.device_type_id).distinct()),
    }
    has_address = any(
        field.visible and field.role in SCAN_ADDRESS_ROLES
        for type_id in scopes for field in get_effective_fields(db, type_id)
    )
    if not has_address:
        raise HTTPException(
            status_code=409,
            detail="Network scanning needs a device field with the scan_address_wan or scan_address_lan role",
        )


def _require_checkout_details(device: Device) -> None:
    """A checkout has to say why, and until when.

    Read off the instance rather than the request body because the values may
    have arrived in this same request: every caller applies the other fields
    before crossing the status boundary, so by here `device` holds what the
    write is about to save. That also means an already-checked-out device can
    still be edited when it predates these columns — the check runs on the
    transition into checked_out, not on every save.
    """
    missing = [
        label
        for label, value in (("purpose", device.checkout_purpose), ("return date", device.checkout_due))
        if not value
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Checking out a device needs a {' and a '.join(missing)}.",
        )
    if device.checkout_due < today():
        raise HTTPException(status_code=422, detail="The return date is in the past.")
    if device.checkout_due > today() + timedelta(days=7):
        raise HTTPException(status_code=422, detail="The return date cannot be more than 7 days away.")


def _reject_checkout_details_off_checkout(
    device: Device, requested: dict, previous: dict
) -> None:
    """The mirror of `_require_checkout_details`: only a checkout has them.

    A purpose and a return date describe one checkout. On a device that is not
    checked out they are not merely unused — they read as the reason an
    available device is unavailable, and a stray due date in the past would
    make the row overdue while nobody has it.

    Two cases, deliberately answered differently:

    * The request *asked* for one of them on a device that will not end up
      checked out. That is the mistake this rejects, so the values do not
      quietly vanish and leave the user wondering where they went.
    * The values are simply along for the ride — an edit dialog resubmits every
      field, so checking a device in sends back the purpose it is about to lose
      — or they predate this rule. Nothing was asked for, so nothing is
      refused; they are cleared, which is where they were headed anyway.

    Runs after the status transition, so `device.status` is what the write is
    about to save rather than what it was.
    """
    if device.status == "checked_out":
        # A due date can also be changed after the original transition. Apply
        # the same ceiling to extensions, without making an unrelated edit to
        # a legacy checkout fail merely because its existing date is farther
        # out than the new policy permits.
        if (
            requested.get("checkout_due")
            and requested.get("checkout_due") != previous.get("checkout_due")
            and device.checkout_due > today() + timedelta(days=7)
        ):
            raise HTTPException(
                status_code=422, detail="The return date cannot be more than 7 days away."
            )
        return
    offending = [
        label
        for label, field in (("purpose", "checkout_purpose"), ("return date", "checkout_due"))
        if requested.get(field) and requested.get(field) != previous.get(field)
    ]
    if offending:
        raise HTTPException(
            status_code=422,
            detail=(
                f"A {' and a '.join(offending)} "
                f"{'applies' if len(offending) == 1 else 'apply'} only to a checked-out device. "
                "Set the status to Checked Out first."
            ),
        )
    device.checkout_purpose = None
    device.checkout_due = None


def _apply_status_transition(db: Session, device: Device, new_status: str | None, actor: User) -> dict:
    """Stamp/clear checkout fields when status crosses the checked_out boundary."""
    hook_failures: dict[str, str] = {}
    if not new_status:
        return hook_failures
    if new_status == device.status:
        # Written even though nothing changed. `status` reads back through a
        # default when the document has no key for it, so a device created as
        # `available` would otherwise be stored carrying no status at all —
        # and a client reading the document rather than the flattened
        # projection would find nothing there.
        device.status = new_status
        return hook_failures
    old = device.status
    if new_status == "checked_out":
        _require_checkout_details(device)
        device.checked_out_by = actor.id
        device.checked_out_at = today()
    elif old == "checked_out":
        device.checked_out_by = None
        device.checked_out_at = None
        # The purpose and the due date belong to the checkout that just ended.
        # Left behind, they read as the reason an available device is
        # unavailable — and would keep it "overdue" forever.
        device.checkout_purpose = None
        device.checkout_due = None
    device.status = new_status
    return emit_status_change(db, device, old, new_status, actor)


# Columns filterable by substring, case-insensitively. These are free text —
# "Dell" should find "Dell Inc." — so they match anywhere in the value.
SUBSTRING_FILTERS = ("location", "make", "model", "firmware_version", "hardware_version")

# Columns filterable only by an exact value. `status` and `architecture` are
# controlled vocabularies, and a substring match on them is actively wrong:
# `arm` is a real architecture and also a prefix of `arm64` and `aarch64`, so
# a substring filter could never return the arm devices on their own.
EXACT_FILTERS = ("status", "architecture")


def _query_devices(db: Session, filters: dict, search: str | None = None) -> select:
    """Build the device query from `?column=value` filters plus free `search`.

    Filters are ANDed: `?make=Dell&online=true` is Dell devices that are up.
    An unknown key is ignored rather than 400-ing, because the caller may be a
    newer client sending a column this deploy does not have yet.
    """
    q = select(Device)
    catalog = union_field_map(db)
    if search:
        like = f"%{search}%"
        searchable = [
            device_field_expression(field).ilike(like)
            for field in catalog.values()
            if field.field_type in {"text", "textarea", "select"}
            and field.storage in {"data", "column"}
            # A sensitive value is never a search term: matching on it would
            # confirm a password to anyone who can guess at one.
            and not field.sensitive
        ]
        q = q.where(
            or_(*searchable) if searchable else Device.id.ilike(like)
        )
    for name, value in filters.items():
        if value is None:
            continue
        if name.startswith("exclude__"):
            field_name = name.removeprefix("exclude__")
            if field_name == "device_type_id":
                clause = exclude_clause(Device.device_type_id, excluded_values(value))
                if clause is not None:
                    q = q.where(clause)
                continue
            field = catalog.get(field_name)
            if field and field.storage in {"data", "column"}:
                expression = device_field_expression(field)
                values = [coerce_filter_value(field, item) for item in excluded_values(value)]
                clause = exclude_clause(expression, values)
                if clause is not None:
                    q = q.where(clause)
            continue
        if name == "device_type":
            if value == "uncategorized":
                q = q.where(Device.device_type_id.is_(None))
            else:
                q = q.join(DeviceType).where(DeviceType.key == value)
        elif name in catalog and catalog[name].storage in {"data", "column"}:
            field = catalog[name]
            expression = device_field_expression(field)
            value = coerce_filter_value(field, value)
            if field.field_type in {"text", "textarea"} and not field.indexed:
                q = q.where(expression.ilike(f"%{value}%"))
            else:
                q = q.where(expression == value)
        elif name == "online":
            # The column is `online_status`; the query parameter is `online`,
            # which is what it is called everywhere a human reads it.
            q = q.where(Device.online_status.is_(value))
        elif name == "overdue":
            # Not a column at all: overdue is derived from the status and the
            # due date (services/checkout.py). Exposing it here rather than
            # only as its own endpoint is what gives the grid filter, the CSV
            # export and the MCP device search the same answer for free.
            clause = overdue_clause()
            q = q.where(clause if value else ~clause)
    return q


@router.get("", response_model=Page[DeviceOut])
def list_devices(
    request: Request,
    search: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
    location: str | None = None,
    make: str | None = None,
    model: str | None = None,
    firmware_version: str | None = None,
    hardware_version: str | None = None,
    architecture: str | None = None,
    online: bool | None = None,
    overdue: bool | None = None,
    sort: str = "created_at",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    offset: int | None = Query(None, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List devices, filtered by any combination of columns.

    Paginates two ways. `page` is the original, and is what the UI sends.
    `offset` takes precedence when given and counts in rows rather than pages,
    which is what a caller doing "give me the next chunk after the N I already
    have" needs: a client that trims a response for its own reasons can resume
    at exactly the row it stopped on, which page numbers cannot express.
    """
    dynamic = {
        key: value for key, value in request.query_params.items()
        if key not in {"search", "sort", "order", "page", "page_size", "offset"}
    }
    dynamic.update({
        key: value for key, value in {
            "status": status, "location": location, "make": make, "model": model,
            "firmware_version": firmware_version, "hardware_version": hardware_version,
            "architecture": architecture,
        }.items() if value is not None
    })
    q = _query_devices(
        db,
        {**dynamic, "device_type": device_type, "online": online, "overdue": overdue},
        search,
    )
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(device_order_by(db, sort, order))
    start = offset if offset is not None else (page - 1) * page_size
    items = db.scalars(q.offset(start).limit(page_size)).all()
    # One layout lookup per device type on the page, not one per row.
    cache: dict = {}
    return Page(
        items=[device_out(db, d, cache) for d in items],
        total=total,
        page=start // page_size + 1,
        page_size=page_size,
    )


@router.get("/scan/status", response_model=ScanStatusOut)
def scan_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return manager.status()


@router.get("/scan/results", response_model=list[DeviceScanState])
def scan_results(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every device's current online state, id and scan columns only.

    Polled by the fleet view alongside `/scan/status` while a large scan runs,
    so the online dots flip as the sweep reaches each device instead of waiting
    for the whole sweep and a page reload. Deliberately not the full device
    row: this is read every couple of seconds, and the client patches only
    these columns into rows it already has.
    """
    rows = db.execute(
        select(
            Device.id,
            Device.online_status.label("online_status"),
            Device.last_seen_online.label("last_seen_online"),
            Device.last_scanned_at.label("last_scanned_at"),
        )
    ).all()
    return [DeviceScanState.model_validate(r) for r in rows]


@router.post("/scan", response_model=ScanStatusOut)
def scan_all_devices(
    request: Request,
    db: Session = Depends(get_db),
    # A scan probes every device on the network and writes the result back to
    # the row; that is a write, not a read.
    user: User = Depends(require_write),
):
    """Start a large scan of all devices in the background."""
    _require_scan_addresses(db)
    started = manager.start_all()
    log_action(db, user, "device.scan_all_start" if started else "device.scan_all_rejected", "device", None, {}, request)
    db.commit()
    if not started:
        raise HTTPException(status_code=409, detail="A scan is already running")
    return manager.status()


@router.get("/info/config", response_model=DeviceInfoConfigOut)
def device_info_config(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return only the non-secret settings needed to decide if an icon is shown."""
    required = list(dict.fromkeys(settings.device_info_required_field_list + [settings.device_info_url_field]))
    scopes = {None, *db.scalars(select(Device.device_type_id).distinct())}
    fields_exist = any(
        set(required) <= {field.key for field in get_effective_fields(db, type_id) if field.visible}
        for type_id in scopes
    )
    return DeviceInfoConfigOut(
        enabled=settings.device_info_enabled and fields_exist,
        required_fields=required,
        url_field=settings.device_info_url_field,
    )


@router.post("/{device_id}/info", response_model=DeviceInfoLaunchOut, status_code=202)
def start_device_info(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    if not settings.device_info_enabled:
        raise HTTPException(status_code=409, detail="Device information lookup is disabled")
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    configured = {field.key for field in get_effective_fields(db, device.device_type_id) if field.visible}
    required_config = set(settings.device_info_required_field_list + [settings.device_info_url_field])
    if not required_config <= configured:
        absent = ", ".join(sorted(required_config - configured))
        raise HTTPException(status_code=409, detail=f"Device information fields are not configured: {absent}")
    if not device.online_status:
        raise HTTPException(status_code=409, detail="Device must be online for information lookup")
    missing = missing_required_fields(device)
    if missing:
        raise HTTPException(status_code=409, detail=f"Device is missing required information: {', '.join(missing)}")
    inventory = _device_dict(db, device)
    try:
        container_id = launch_device_info(device, inventory)
    except DockerError as exc:
        log_action(db, user, "device.info_failed", "device", device.id, {"error": str(exc)}, request)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    log_action(
        db, user, "device.info_start", "device", device.id,
        {"container_id": container_id, "image": settings.device_info_image}, request,
    )
    db.commit()
    return DeviceInfoLaunchOut(container_id=container_id)


@router.get("/export")
def export_devices(
    format: str = "json",
    columns_mode: str = Query("type", pattern="^(type|all|data)$", alias="columns"),
    # On by default. A file whose stray values are a JSON blob in one cell is
    # not something anyone can work with in a spreadsheet, and the blob is not
    # a stable column set either — its contents vary per row. Pass
    # `expand_misc=false` for the single-column shape, which a script reading
    # fixed headers may prefer.
    expand_misc: bool = Query(True),
    search: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
    location: str | None = None,
    make: str | None = None,
    model: str | None = None,
    firmware_version: str | None = None,
    hardware_version: str | None = None,
    architecture: str | None = None,
    online: bool | None = None,
    overdue: bool | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Export the devices a filter selects. Takes the same filters as the list
    endpoint, so what you see in a filtered table is what you get in the file."""
    dynamic = {
        key: value for key, value in request.query_params.items()
        if key not in {"format", "search", "online", "overdue", "columns", "expand_misc"}
    } if request else {}
    dynamic.update({key: value for key, value in {
        "status": status, "location": location, "make": make, "model": model,
        "firmware_version": firmware_version, "hardware_version": hardware_version,
        "architecture": architecture,
    }.items() if value is not None})
    q = _query_devices(
        db,
        {**dynamic, "device_type": device_type, "online": online, "overdue": overdue},
        search,
    )
    items = db.scalars(q.order_by(Device.created_at)).all()
    resolved = _resolve_device_type(db, device_type, allow_disabled=True) if device_type and device_type != "uncategorized" else None
    # An unscoped export represents the fleet, not just its global schema.
    effective_mode = "all" if columns_mode == "type" and device_type is None else columns_mode
    columns, projector = _export_shape(db, resolved, effective_mode)
    rows = [projector(device) for device in items]
    if expand_misc:
        rows, columns = _expand_misc_columns(rows, columns)
    log_action(db, user, "export.devices", "device", None,
               {"format": format, "count": len(rows), "columns": effective_mode,
                "expand_misc": expand_misc}, request)
    db.commit()
    return export_response(rows, columns, format, "devices")


def _expand_misc_columns(rows: list[dict], columns: list[str]) -> tuple[list[dict], list[str]]:
    """Lift every device's `misc_data` into columns of its own.

    One column per stray key across the whole file, instead of a JSON object
    per row. That is what makes an export readable in a spreadsheet, and it
    round-trips: a column an installation does not define lands in the document
    on import, which is where these values came from.

    Columns are the union across every exported device, so a device without a
    given key leaves that cell blank rather than the file growing a shape per
    row. They are sorted, so the same fleet exports the same way twice.

    One kind of key cannot be expanded. A key the request envelope claims —
    `id`, `created_at`, `device_type` and the rest — would be read back as part
    of the envelope rather than as a value, so those stay behind in a residual
    `misc_data` column and keep their meaning. The column only appears if some
    device actually has one.
    """
    if "misc_data" not in columns:
        return rows, columns

    reserved = ENVELOPE_KEYS | IMPORT_IGNORED
    known = set(columns)
    expanded: set[str] = set()
    residual = False
    for row in rows:
        for key in row.get("misc_data") or {}:
            if key in reserved:
                residual = True
            elif key not in known:
                expanded.add(key)

    for row in rows:
        misc = row.pop("misc_data", None) or {}
        for key, value in misc.items():
            if key in reserved:
                continue
            # A key that already has a column of its own was filled from this
            # same document, so the two agree; never overwrite what the layout
            # resolved with a copy of it.
            if row.get(key) in (None, ""):
                row[key] = value
        # Only the devices that actually have one carry the residual column;
        # the rest leave the cell blank rather than showing an empty object.
        held_back = {key: value for key, value in misc.items() if key in reserved}
        if held_back:
            row["misc_data"] = held_back

    columns = ([column for column in columns if column != "misc_data"]
               + sorted(expanded) + (["misc_data"] if residual else []))
    return rows, columns


def _export_shape(db: Session, device_type: DeviceType | None, mode: str):
    """Which columns an export carries, and how each device fills them.

    Three deliberate answers rather than one guess:

    * `type` — the effective columns of the page being exported. A router
      export looks like the router page, which is what somebody exporting a
      filtered view expects to get back.
    * `all` — the union of every field any type defines, so one file can carry
      a mixed fleet without losing a column.
    * `data` — the structural columns plus the whole JSON document in one
      cell. Lossless, and the only shape that survives a schema that changes
      between export and re-import.
    """
    # Shared by both projections below, and by every row they are called for.
    cache: dict = {}

    if mode == "data":
        columns = [*STRUCTURAL_COLUMNS, "data", "checked_out_by_username", "created_at", "updated_at"]

        def project(device: Device) -> dict:
            row = _device_dict(db, device, cache)
            return {**{key: row.get(key) for key in columns}, "device_type": device.device_type_key,
                    "data": device.data}
        return columns, project

    if mode == "all":
        fields = _union_fields(db)
    else:
        fields = [field for field in get_effective_fields(db, device_type.id if device_type else None) if field.visible]
    # `misc_data` is a column like any other here. It was excluded while it
    # meant "the document minus a handful of flattened names", when it would
    # have repeated nearly every other column in the file. It now means only
    # what no field on the layout accounts for — the one part of a device that
    # no other column can carry — so leaving it out is how an export silently
    # drops the values it exists to preserve. The import side has always
    # accepted the column.
    keys = [field.key for field in fields]
    columns = ["device_type", *dict.fromkeys(["unique_id", *keys])]

    def project(device: Device) -> dict:
        row = _device_dict(db, device, cache)
        document = device.data
        result = {"device_type": device.device_type_key}
        for field in fields:
            # A virtual field has no value in the document to read: misc_data
            # is resolved against the layout, and the serialized row already
            # carries that answer.
            result[field.key] = row.get(field.key) if field.storage != "data" else document.get(field.key)
        result["unique_id"] = device.unique_id
        return result

    return columns, project


def _union_fields(db: Session):
    """Every field any device type shows, global ones first, without repeats."""
    seen: dict[str, object] = {field.key: field for field in get_effective_fields(db, None)}
    for item in db.scalars(select(DeviceType)):
        for field in get_effective_fields(db, item.id):
            seen.setdefault(field.key, field)
    return list(seen.values())


@router.get("/template")
def device_template(
    device_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A blank CSV with the columns an import accepts — fill it in and import it.

    With `device_type` the template is that type's own: system columns, the
    global fields it inherits, and its own fields. Without one it is the global
    set, which is what an import of mixed hardware can rely on.
    """
    try:
        resolved = _resolve_device_type(db, device_type, allow_disabled=True) if device_type else None
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # A type-specific button gets that layout. The all-devices button needs the
    # union so a mixed-fleet CSV can carry fields that exist on only one type.
    fields = get_effective_fields(db, resolved.id) if resolved else _union_fields(db)
    columns = ["device_type", *[
        field.key for field in fields
        # Scan- and checkout-owned columns are filled in by the server, not by
        # hand. `misc_data` is left out for a different reason: it carries the
        # values a layout does not account for, and a device being created from
        # a blank template has none yet. An import still accepts the column, so
        # an exported file that has one round-trips.
        if field.visible and field.writable and field.key != "misc_data"
    ]]
    name = f"devices-{resolved.key}-template.csv" if resolved else "devices-template.csv"
    return download_response(template_csv(columns), name, "text/csv")


# Declared above /{device_id}: routes match in order, and that one resolves a
# device by UUID *or* unique_id, so a later /overdue would be looked up as a
# device called "overdue" and 404 instead of reaching this.
@router.get("/overdue", response_model=Page[DeviceOverdueOut])
def list_overdue_devices(
    min_days: int = Query(1, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Devices that are past their return date, worst first.

    Nothing here changes anything: a device stays checked out when it goes
    overdue, because taking it off whoever has it — while it is still on their
    desk — would make the fleet lie about where it is. The state is derived, so
    extending a due date removes a device from this list on the next read.

    `min_days` skips the ones that are barely late (default 1: everything).
    """
    stamp = today()
    q = select(Device).where(overdue_clause())
    if min_days > 1:
        q = q.where(Device._data["checkout_due"].astext <= (stamp - timedelta(days=min_days)).isoformat())
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    # Oldest due date first: the device that has been out longest is the one
    # worth chasing, and it is what a caller reading only the first page wants.
    q = q.order_by(Device._data["checkout_due"].astext.asc())
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    cache: dict = {}
    return Page(
        items=[
            DeviceOverdueOut(
                **device_out(db, d, cache).model_dump(),
                days_overdue=(stamp - d.checkout_due).days,
            )
            for d in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/lookup/by-unique-id", response_model=DeviceDetail)
def lookup_device(
    unique_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Resolve names containing slashes without interpreting them as URL paths."""
    device = db.scalar(select(Device).where(Device.unique_id == unique_id))
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return get_device(device.id, db=db, user=user)


@router.get("/{device_id}", response_model=DeviceDetail)
def get_device(device_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    all_tests = db.scalars(
        select(Test).where(Test.device_id == device.id).order_by(Test.created_at.desc())
    ).all()
    verified = [t for t in all_tests if t.outcome == "pass"]
    detail = DeviceDetail.model_validate(device)
    detail.misc_data = device_misc_data(db, device)
    detail.verified_tests = [TestOut.model_validate(t) for t in verified]
    detail.all_tests = [TestOut.model_validate(t) for t in all_tests]
    return detail


@router.get("/{device_id}/actions")
def device_actions(
    device_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every plugin action for this device, and why each one can or cannot run.

    The frontend renders exactly what this returns. It does not decide
    availability itself, and it could not: whether Reboot applies to a phone is
    a question about the installation's device-type policy, not about the row.
    """
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {
        "device_id": device.id,
        "device_type": device.device_type_key,
        "revision": current_revision(db),
        "actions": get_available_actions(db, device, user),
    }


@router.get("/{device_id}/compatible-software", response_model=DeviceCompatibilityOut)
def get_compatible_software(
    device_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Software whose vendor device lists describe this device.

    The vendor's claim about the hardware, not our test results — see
    `services/compat.py` for how a claim is matched to a device.
    """
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceCompatibilityOut(
        device_id=device.id,
        items=[
            DeviceCompatibleSoftwareOut(
                software=SoftwareOut.model_validate(item["software"]),
                support_status=item["support_status"],
                matched_on=item["matched_on"],
                vendor_devices=[VendorDeviceOut.model_validate(v) for v in item["vendor_devices"]],
            )
            for item in compatible_software(db, device)
        ],
    )


@router.get("/{device_id}/related-counts", response_model=DeviceRelatedCountsOut)
def get_related_counts(
    device_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Totals for the device's Vendor Claims and Tests tabs.

    Vendor claims deliberately run through the same reverse compatibility
    matcher as the tab itself; this is not a looser make/model-only count.
    """
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    matches = compatible_software(db, device)
    return DeviceRelatedCountsOut(
        vendor_claims=sum(len(item["vendor_devices"]) for item in matches),
        tests=db.scalar(select(func.count(Test.id)).where(Test.device_id == device.id)) or 0,
    )


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(
    body: DeviceCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    unique_id = (body.unique_id or "").strip()
    if not unique_id:
        raise HTTPException(status_code=422, detail="Unique ID is required")
    if db.scalar(select(Device.id).where(Device.unique_id == unique_id)):
        raise HTTPException(status_code=409, detail=f"Device with unique_id '{unique_id}' already exists")
    document, type_value, _ = _envelope(body)
    device = Device(unique_id=unique_id, created_by=user.id, updated_by=user.id)
    try:
        requested_type = _resolve_device_type(db, type_value)
        applied = _write_document(db, device, document, requested_type, creating=True)
    except (ValueError, DeviceValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    device.device_type = requested_type
    db.add(device)
    # Built available, then transitioned. Creating a device already checked out
    # used to skip the transition entirely, so it got no checked_out_by and no
    # checked_out_at — and now it would also skip the purpose/due requirement.
    # Going through the same path as a PATCH is what makes the rule hold on
    # every way in.
    _apply_status_transition(db, device, applied["status"] or "available", user)
    _reject_checkout_details_off_checkout(device, applied["document"], {})
    # Flushed first: `id` is a column default, so it does not exist until the
    # row reaches the database, and the audit entry would name no device.
    db.flush()
    log_action(db, user, "device.create", "device", device.id, {
        "unique_id": unique_id, "device_type": device.device_type_key,
        # Secrets are masked: an audit entry should say a credential was set,
        # not be a second copy of it that everyone with log access can read.
        "data": redact_sensitive(db, device.device_type_id, device.data),
    }, request)
    db.commit()
    db.refresh(device)
    return device_out(db, device)


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: str,
    body: DeviceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    old = {"unique_id": device.unique_id, "device_type": device.device_type_key, **device.data}
    document, type_value, type_supplied = _envelope(body)
    requested_type = device.device_type
    try:
        if type_supplied:
            requested_type = _resolve_device_type(db, type_value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    changing_type = type_supplied and (requested_type.id if requested_type else None) != device.device_type_id
    new_unique_id = (body.unique_id or "").strip() or None
    if new_unique_id and new_unique_id != device.unique_id:
        # unique_id is UNIQUE in the schema, so without this the clash surfaces
        # as an IntegrityError at commit time — a 500 for a user's typo.
        clash = db.scalar(
            select(Device.id).where(Device.unique_id == new_unique_id, Device.id != device.id)
        )
        if clash:
            raise HTTPException(
                status_code=409, detail=f"Device with unique_id '{new_unique_id}' already exists"
            )
        device.unique_id = new_unique_id
    try:
        applied = _write_document(db, device, document, requested_type,
                                  creating=False, changing_type=changing_type)
    except (ValueError, DeviceValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if type_supplied:
        device.device_type = requested_type
    device.updated_by = user.id
    device.updated_at = utcnow()
    hook_failures = _apply_status_transition(db, device, applied["status"], user)
    _reject_checkout_details_off_checkout(device, applied["document"], old)
    new = {"unique_id": device.unique_id, "device_type": device.device_type_key, **device.data}
    # Diffed on the real values so a changed secret still reads as changed, then
    # masked: the entry says the password was replaced without being a second
    # copy of either the old one or the new one.
    detail = {"diff": _redact_diff(db, device, field_diff(old, new))}
    if hook_failures:
        detail["hook_failures"] = hook_failures
    log_action(db, user, "device.update", "device", device.id, detail, request)
    db.commit()
    db.refresh(device)
    return device_out(db, device)


@router.post("/{device_id}/scan", response_model=DeviceScanResult)
def scan_single_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    """Probe a single device (ICMP/HTTP/HTTPS/TELNET/SSH) and update its online state."""
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    scan_action = next(
        (
            action for action in get_available_actions(db, device, user)
            if action.get("plugin_id") == "network-scan"
            and action.get("id") == "network-scan.scan-device"
        ),
        None,
    )
    if scan_action is None:
        raise HTTPException(
            status_code=409,
            detail="Network Scan is not installed or is unhealthy",
        )
    if not scan_action.get("available"):
        raise HTTPException(
            status_code=403,
            detail=scan_action.get("unavailable_reason") or "Network Scan is not enabled for this device type",
        )
    _require_scan_addresses(db, device)
    try:
        result = scan_device(db, device)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log_action(db, user, "device.scan", "device", device.id, {"error": str(exc)}, request)
        db.commit()
        return DeviceScanResult(device=device_out(db, device), online=False, error=str(exc))
    log_action(db, user, "device.scan", "device", device.id, result, request)
    db.commit()
    db.refresh(device)
    return DeviceScanResult(
        device=device_out(db, device),
        online=result["online"],
        checks=result["checks"],
        probes=result["probes"],
    )


@router.delete("/{device_id}", status_code=204)
def delete_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    device = _get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    log_action(db, user, "device.delete", "device", device.id,
               {"snapshot": _audit_snapshot(db, device)}, request)
    db.delete(device)
    db.commit()


@router.post("/delete", status_code=204)
def delete_devices_bulk(
    body: BulkIds,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_write),
):
    """Delete multiple devices by id (multi-select delete)."""
    deleted = 0
    for device_id in body.ids:
        device = db.get(Device, device_id)
        if device:
            log_action(db, user, "device.delete", "device", device.id,
                       {"snapshot": _audit_snapshot(db, device), "bulk": True}, request)
            db.delete(device)
            deleted += 1
    db.commit()
    return None


def _upsert_row(db: Session, row: dict, user: User) -> str:
    """Create or update one device from a flat or enveloped row.

    Shared by the bulk endpoint and by CSV/JSON import so that a row means the
    same thing however it arrived, and so that every one of them is checked
    against the same published schema.
    """
    document, type_value, type_supplied = _envelope(row)
    device = db.get(Device, row["id"]) if row.get("id") else None
    unique_id = str(row.get("unique_id") or "").strip()
    if device is None and unique_id:
        device = db.scalar(select(Device).where(Device.unique_id == unique_id))
    creating = device is None

    requested_type = _resolve_device_type(db, type_value) if type_supplied else (
        None if creating else device.device_type
    )
    if creating:
        if not unique_id:
            raise ValueError("unique_id is required")
        device = Device(unique_id=unique_id, created_by=user.id, updated_by=user.id)
        previous: dict = {}
    else:
        previous = {"unique_id": device.unique_id, **device.data}
        if unique_id and unique_id != device.unique_id:
            clash = db.scalar(select(Device.id).where(
                Device.unique_id == unique_id, Device.id != device.id))
            if clash:
                raise ValueError(f"Device with unique_id '{unique_id}' already exists")
            device.unique_id = unique_id

    changing_type = type_supplied and (requested_type.id if requested_type else None) != device.device_type_id
    applied = _write_document(db, device, document, requested_type,
                              creating=creating, changing_type=changing_type)
    if creating:
        device.device_type = requested_type
        db.add(device)
        # Built available and then transitioned, exactly as POST /devices does:
        # constructing the row with the status it asked for would skip the
        # stamping and the checkout rules entirely, so an import could make a
        # checkout nobody holds.
        _apply_status_transition(db, device, applied["status"] or "available", user)
    else:
        if type_supplied:
            device.device_type = requested_type
        # Status last: the transition must see the old value in order to stamp
        # or clear a checkout.
        _apply_status_transition(db, device, applied["status"], user)
        device.updated_by = user.id
        device.updated_at = utcnow()
    _reject_checkout_details_off_checkout(device, applied["document"], previous)
    return "created" if creating else "updated"


@router.post("/bulk")
def bulk_devices(
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
                outcome = _upsert_row(db, row, user)
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": i, "error": row_error(exc)})
            continue
        created += outcome == "created"
        updated += outcome == "updated"
    for device_id in body.delete_ids:
        device = db.get(Device, device_id)
        if device:
            db.delete(device)
            deleted += 1
        else:
            errors.append({"row": None, "error": f"device {device_id} not found"})
    log_action(db, user, "device.bulk", "device", None,
               {"created": created, "updated": updated, "deleted": deleted, "errors": errors}, request)
    db.commit()
    return {"created": created, "updated": updated, "deleted": deleted, "errors": errors}


@router.post("/import", response_model=ImportResult)
async def import_devices(
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
    for i, row in enumerate(rows):
        try:
            with row_scope(db):
                if not isinstance(row, dict):
                    raise ValueError("row must be an object")
                # A CSV cell is always a string, so a column left blank arrives
                # as "" rather than missing. Dropping the empty ones keeps an
                # import from clearing values the file never meant to mention.
                cleaned = strip_nulls({k: v for k, v in row.items() if k not in IMPORT_IGNORED})
                if is_csv and isinstance(cleaned.get("data"), str):
                    # The `columns=data` export writes the whole document into
                    # one cell; reading it back is how that export round-trips.
                    cleaned["data"] = json.loads(cleaned["data"] or "{}")
                if isinstance(cleaned.get("misc_data"), str):
                    cleaned["misc_data"] = json.loads(cleaned["misc_data"] or "{}")
                cleaned["id"] = row.get("id")
                outcome = _upsert_row(db, cleaned, user)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"row": i, "error": row_error(exc)})
            continue
        result.created += outcome == "created"
        result.updated += outcome == "updated"
    log_action(db, user, "device.import", "device", None,
               {"file": file.filename, "created": result.created, "updated": result.updated, "errors": result.errors}, request)
    db.commit()
    return result
