"""The read tools (MCP_SERVER.md §5, read half only).

Task-shaped, not endpoint-shaped: these do not mirror the REST paths. A model
given 40 CRUD tools picks badly; one given a dozen intent-named tools picks
well. Nothing here writes — no `set_`, `record_` or `import_` tool exists yet.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError

from .config import settings
from .enums import (
    AUDIT_ENTITY_TYPES,
    DEVICE_ARCHITECTURES,
    DEVICE_STATUSES,
    TEST_OUTCOMES,
    TEST_TAGS,
    check,
)
from .format import (
    audit_brief,
    clamp_limit,
    device_brief,
    device_full,
    drop_empty,
    envelope,
    software_brief,
    software_full,
    test_brief,
    vendor_device_brief,
)
from .resolve import (
    Unresolved,
    describe_device,
    describe_software,
    fetch_pages,
    resolve_device,
    resolve_software,
)
from .server import api, server

RECENT_TESTS = 10


def _ts(value: str | None) -> datetime | None:
    """An API timestamp as a naive UTC datetime, for comparison against filters."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _bound(value: str | None, *, end: bool) -> datetime | None:
    """Parse a `since`/`until` filter. A date with no time means the whole day,
    so an `until` of "2026-08-19" includes that day's runs."""
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(
            f"'{value}' is not an ISO 8601 date or datetime (e.g. 2026-08-19 "
            f"or 2026-08-19T14:30:00)."
        ) from exc
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if end and len(text) <= 10:
        parsed += timedelta(days=1)
    return parsed


def _outcome_counts(tests: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in tests:
        counts[t["outcome"]] = counts.get(t["outcome"], 0) + 1
    return counts


# -- devices --------------------------------------------------------------


@server.tool()
async def find_devices(
    search: str | None = None,
    device_type: str | None = None,
    status: str | None = None,
    location: str | None = None,
    make: str | None = None,
    model: str | None = None,
    architecture: str | None = None,
    firmware_version: str | None = None,
    hardware_version: str | None = None,
    online: bool | None = None,
    overdue: bool | None = None,
    fields: dict[str, str | int | float | bool] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Find devices in the fleet, filtered by any combination of columns.

    `search` matches unique_id, make, model, location and IP addresses at once;
    the named filters below are narrower and combine with AND, so
    `make="Dell", online=True` is Dell devices that are currently up.

    `status` is one of available, checked_out, inventory, missing, broken.
    `online` is whether the last network scan reached the device, and is
    independent of `status` — a checked_out device can be online.
    `overdue=True` narrows to devices past their return date and still checked
    out. Overdue is derived, not stored: nothing is taken back automatically,
    so an overdue device is still `checked_out` and still has a holder. Use it
    to answer "what is late?" and "who has it?" in one call.
    `architecture` must be an exact value (x86_64, arm64, mipsbe, ...).
    `location`, `make`, `model`, `firmware_version` and `hardware_version`
    match a substring, case-insensitively.

    To sweep a fleet larger than one response, pass the `next_offset` from the
    previous reply back as `offset` and repeat until `truncated` is absent.
    Prefer a filter over a sweep where one exists: `total` on a filtered call
    counts every match, so a count needs one call, not a full walk.

    `device_type` narrows to one kind of hardware by its stable key
    ("router", "mobile", ...), or "uncategorized" for devices with no type.
    Types are installation-defined; `testbench://schema` lists the ones this
    installation has, with the fields each of them carries.

    `fields` filters installation-specific catalog fields by key, including
    fields only one device type defines. Read `testbench://schema` for the
    available keys and their types.

    Returns a compact row per device. Use `get_device` for the full record and
    test history of one of them.
    """
    limit = clamp_limit(limit)
    status = check(status, "device status", DEVICE_STATUSES)
    architecture = check(architecture, "architecture", DEVICE_ARCHITECTURES)
    page = await api().get(
        "/devices",
        params={**(fields or {}),
            "search": search, "device_type": device_type,
            "status": status, "location": location,
            "make": make, "model": model, "architecture": architecture,
            "firmware_version": firmware_version,
            "hardware_version": hardware_version, "online": online,
            "overdue": overdue,
            "sort": "unique_id", "order": "asc",
            "page_size": limit, "offset": max(0, offset),
        },
    )
    return envelope(
        [device_brief(d) for d in page["items"]],
        page["total"],
        offset=max(0, offset),
    )


@server.tool()
async def find_due_devices(
    due_within_days: int = 3,
    include_overdue: bool = True,
    location: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Find checked-out devices that are overdue or due soon.

    This is the direct tool for questions such as "which devices are overdue?",
    "what is due back soon?", and "what is due in the next three days?".
    `due_within_days` defaults to 3 and includes devices due today through that
    many calendar days from today. `include_overdue=True` also includes every
    device whose due date has already passed; set it false for upcoming returns
    only. Results are ordered by due date, most urgent first, and include the
    holder, due date, `due_in_days`, and `overdue_days`.
    """
    try:
        window = int(due_within_days)
    except (TypeError, ValueError) as exc:
        raise ToolError("due_within_days must be a whole number from 0 to 365.") from exc
    if not 0 <= window <= 365:
        raise ToolError("due_within_days must be a whole number from 0 to 365.")

    limit = clamp_limit(limit)
    offset = max(0, offset)
    rows = await fetch_pages(
        api(),
        "/devices",
        {"status": "checked_out", "location": location, "sort": "checkout_due", "order": "asc"},
        settings.max_scan_rows,
        page_size=min(settings.max_limit, 500),
    )
    today = date.today()
    latest = today + timedelta(days=window)
    matches = []
    for row in rows:
        raw_due = row.get("checkout_due")
        if not raw_due:
            continue
        try:
            due = date.fromisoformat(raw_due[:10])
        except (TypeError, ValueError):
            continue
        if due <= latest and (include_overdue or due >= today):
            matches.append(row)

    page = matches[offset : offset + limit]
    result = envelope(
        [device_brief(d) for d in page],
        len(matches),
        offset=offset,
        due_within_days=window,
        include_overdue=include_overdue,
    )
    if len(rows) >= settings.max_scan_rows:
        result["scan_note"] = (
            f"Scanned the first {settings.max_scan_rows} checked-out devices; "
            "the fleet may contain additional matches."
        )
    return result


@server.tool()
async def get_device(identifier: str) -> dict:
    """Full detail for one device, by unique_id (e.g. "dev-0042") or UUID.

    Includes the device's recent test runs and its pass/fail/warn totals. For a
    per-software breakdown of what has been run against it, use
    `list_software_tested_on`.
    """
    try:
        device = await resolve_device(api(), identifier)
    except Unresolved as miss:
        return miss.payload

    all_tests = device.get("all_tests", [])
    out = {
        "resolved": describe_device(device),
        "device": device_full(device),
        "test_summary": {
            "total": len(all_tests),
            "outcomes": _outcome_counts(all_tests),
            "distinct_software": len({t["software_id"] for t in all_tests}),
        },
        "recent_tests": [test_brief(t) for t in all_tests[:RECENT_TESTS]],
    }
    if len(all_tests) > RECENT_TESTS:
        out["recent_tests_note"] = (
            f"Showing the {RECENT_TESTS} most recent of {len(all_tests)} runs. Use "
            f"`list_software_tested_on` for the per-software breakdown, or "
            f"`find_tests(device=...)` for more rows."
        )
    return out


# -- software -------------------------------------------------------------


@server.tool()
async def list_device_actions(identifier: str) -> dict:
    """What automation can run against one device, and why the rest cannot.

    Not every installed plugin applies to every device: a plugin is available
    only where the device type's policy allows it and the fields it needs are
    both configured and populated. Each entry says whether it is `available`
    and, when it is not, gives the reason — which is usually something an
    administrator can fix (enable the plugin for that device type, add the
    field, fill in the value).
    """
    try:
        device = await resolve_device(api(), identifier)
    except Unresolved as miss:
        return miss.payload
    result = await api().get(f"/devices/{quote(device['id'], safe='')}/actions")
    return {
        "resolved": describe_device(device),
        "device_type": device.get("device_type_key"),
        "actions": [
            drop_empty({
                "plugin": action.get("plugin_id"),
                "action": action.get("id"),
                "label": action.get("label"),
                "risk": action.get("risk"),
                "available": action.get("available"),
                "unavailable_reason": action.get("unavailable_reason"),
            })
            for action in result.get("actions", [])
        ],
    }


@server.tool()
async def find_software(
    search: str | None = None,
    latest_only: bool = True,
    limit: int | None = None,
) -> dict:
    """Find software by name or version.

    By default only the current version of each name is returned. Set
    `latest_only=false` to list every version of every match — useful when
    comparing versions, noisy otherwise.

    "Software" here is the domain entity (nmap, iperf3, ...), not an MCP tool.
    """
    limit = clamp_limit(limit)
    page = await api().get(
        "/software",
        params={"search": search, "latest_only": latest_only,
                "sort": "name", "order": "asc", "page_size": limit},
    )
    return envelope([software_brief(s) for s in page["items"]], page["total"],
                    showing="current versions only" if latest_only else "all versions")


@server.tool()
async def get_software(identifier: str, version: str | None = None) -> dict:
    """Detail for one piece of software, by name (e.g. "nmap") or UUID.

    A bare name resolves to the CURRENT version. Pass `version` to pin a
    specific one. The response echoes which version it resolved to and how many
    exist — check it before reporting version-specific results.
    """
    try:
        software = await resolve_software(api(), identifier, version)
    except Unresolved as miss:
        return miss.payload
    return {
        "resolved": describe_software(software),
        "software": software_full(software),
        "vendor_device_count": software.get("vendor_device_count", 0),
        "next": (
            "Use `list_devices_tested_with` for measured evidence, or "
            "`list_vendor_supported_devices` for the vendor's compatibility claims."
        ),
    }


@server.tool()
async def list_software_versions(name: str) -> dict:
    """Every version of a named piece of software, highest first.

    The first entry is the highest numeric version — the one a bare name
    resolves to.
    """
    try:
        software = await resolve_software(api(), name)
    except Unresolved as miss:
        return miss.payload
    versions = await api().get(f"/software/{quote(software['id'], safe='')}/versions")
    return envelope([software_brief(v) for v in versions], len(versions), software=software["name"])


# -- the two senses of "works with" (§4) ----------------------------------


@server.tool()
async def list_devices_tested_with(
    software: str, version: str | None = None, limit: int | None = None
) -> dict:
    """Devices this software has ACTUALLY BEEN RUN against, with outcome counts.

    This is measured evidence from the tests table. A device missing from this
    list is UNTESTED, which is not the same as unsupported — for what the vendor
    claims to support, call `list_vendor_supported_devices` instead. The two
    routinely disagree; do not merge them.
    """
    limit = clamp_limit(limit)
    try:
        record = await resolve_software(api(), software, version)
    except Unresolved as miss:
        return miss.payload
    body = await api().get(f"/software/{quote(record['id'], safe='')}/tested-devices")
    rows = body.get("devices", [])
    items = [
        {
            **device_brief(row["device"]),
            "component": (
                f"{row['component']['name']} {row['component'].get('version') or ''}".strip()
                if row.get("component") else None
            ),
            "tests": row["test_count"],
            "last_test": (row.get("last_test_at") or "").split("T")[0] or None,
            "outcomes": row.get("outcomes", {}),
        }
        for row in rows
    ]
    return envelope(
        items[:limit],
        len(rows),
        software=describe_software(record),
        sense="measured test evidence — absence means untested, not unsupported",
    )


@server.tool()
async def list_vendor_supported_devices(
    software: str,
    version: str | None = None,
    search: str | None = None,
    limit: int | None = None,
) -> dict:
    """Hardware the VENDOR CLAIMS this software supports.

    This is the vendor's compatibility list. These are not necessarily devices
    this fleet owns and they carry no test evidence whatsoever. For devices
    actually run against, call `list_devices_tested_with` instead.

    `support_status` on each row is one of supported, partial, unsupported,
    planned — that is the vendor's claim, not a test result.
    """
    limit = clamp_limit(limit)
    try:
        record = await resolve_software(api(), software, version)
    except Unresolved as miss:
        return miss.payload
    page = await api().get(
        f"/software/{quote(record['id'], safe='')}/vendor-devices",
        params={"search": search, "page_size": limit},
    )
    schema = await api().get(
        f"/software/{quote(record['id'], safe='')}/vendor-devices/schema",
    )
    sensitive = {field["key"] for field in schema if field.get("sensitive")}
    items = []
    for vendor_device in page["items"]:
        safe = dict(vendor_device)
        safe["misc_data"] = {
            key: value for key, value in (safe.get("misc_data") or {}).items()
            if key not in sensitive
        }
        items.append(vendor_device_brief(safe))
    return envelope(
        items,
        page["total"],
        software=describe_software(record),
        sense="vendor compatibility claims — not evidence, and not necessarily hardware we own",
    )


@server.tool()
async def list_software_tested_on(device: str, limit: int | None = None) -> dict:
    """Software that has been run against a device, with per-software outcomes.

    The reverse of `list_devices_tested_with`: measured evidence, aggregated by
    software. "Has worked against" is read as "has test evidence for" — pass and
    fail counts are both reported rather than filtered to passes, so read the
    outcomes before calling something supported.
    """
    limit = clamp_limit(limit)
    try:
        record = await resolve_device(api(), device)
    except Unresolved as miss:
        return miss.payload

    # The API has no /devices/{id}/tested-software endpoint (MCP_SERVER.md §9.1),
    # so the aggregation happens here — in Python, over rows that never enter the
    # model's context — rather than being handed to the model as raw test rows.
    groups: dict[str, dict[str, Any]] = {}
    for t in record.get("all_tests", []):
        key = t["software_id"]
        entry = groups.setdefault(key, {
            "name": t.get("software_name"),
            "version": t.get("software_version") or None,
            "tests": 0, "last": None, "outcomes": {},
        })
        entry["tests"] += 1
        entry["outcomes"][t["outcome"]] = entry["outcomes"].get(t["outcome"], 0) + 1
        stamp = _ts(t.get("run_at") or t.get("created_at"))
        if stamp and (entry["last"] is None or stamp > entry["last"]):
            entry["last"] = stamp

    items = sorted(groups.values(), key=lambda e: (e["last"] or datetime.min), reverse=True)
    for item in items:
        item["last"] = item["last"].date().isoformat() if item["last"] else None
        if item["version"] is None:
            del item["version"]

    return envelope(
        items[:limit],
        len(items),
        device=describe_device(record),
        sense="measured test evidence — absence means untested, not unsupported",
    )


# -- tests ----------------------------------------------------------------


@server.tool()
async def find_tests(
    device: str | None = None,
    software: str | None = None,
    software_version: str | None = None,
    outcome: str | None = None,
    tag: str | None = None,
    since: str | None = None,
    until: str | None = None,
    search: str | None = None,
    fields: dict[str, str | int | float | bool] | None = None,
    limit: int | None = None,
) -> dict:
    """Find individual test runs, newest first.

    `device` is a unique_id, `software` a name (pinned with `software_version`,
    otherwise the current version). `outcome` is pass, fail or warn; `tag` is
    adhoc, acceptance, end-to-end or automated. `since`/`until` are ISO dates or datetimes
    and are inclusive of whole days when given as bare dates. `search` matches
    device unique_id, software name and test notes.

    `fields` filters installation-specific test fields by key. Read
    `testbench://schema` before using it.

    For a summary rather than individual runs, use `list_devices_tested_with` or
    `list_software_tested_on` — they aggregate in the database instead.
    """
    limit = clamp_limit(limit)
    client = api()
    resolved: dict[str, str] = {}
    params: dict[str, Any] = {**(fields or {}),
        "outcome": check(outcome, "test outcome", TEST_OUTCOMES),
        "tag": check(tag, "test tag", TEST_TAGS),
        "search": search,
    }

    try:
        if device:
            record = await resolve_device(client, device)
            params["device_id"] = record["id"]
            resolved["device"] = describe_device(record)
        if software:
            record = await resolve_software(client, software, software_version)
            params["software_id"] = record["id"]
            resolved["software"] = describe_software(record)
        start = _bound(since, end=False)
        end = _bound(until, end=True)
    except Unresolved as miss:
        return miss.payload

    if start is None and end is None:
        page = await client.get("/tests", params={**params, "page_size": limit})
        rows, total = page["items"], page["total"]
    else:
        # The tests endpoint has no date-range filter (MCP_SERVER.md §9), so the
        # window is applied here over a bounded scan.
        scanned = await fetch_pages(client, "/tests", params, settings.max_scan_rows)
        rows = [t for t in scanned if _in_window(t, start, end)]
        total = len(rows)
        if len(scanned) >= settings.max_scan_rows:
            resolved["scan_note"] = (
                f"Only the {settings.max_scan_rows} most recent matching runs were "
                f"searched for this date range; add filters to be sure of completeness."
            )
        rows = rows[:limit]

    return envelope([test_brief(t) for t in rows], total, **resolved)


def _in_window(test: dict, start: datetime | None, end: datetime | None) -> bool:
    stamp = _ts(test.get("run_at") or test.get("created_at"))
    if stamp is None:
        return False
    return not ((start and stamp < start) or (end and stamp >= end))


# -- audit ----------------------------------------------------------------


@server.tool()
async def search_audit(
    user: str | None = None,
    action: str | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict:
    """Search the audit log — who changed what, when (admin only).

    `action` is a substring of the action name (e.g. "device.update",
    "export"). `entity` is one of device, software, test, vendor_device, user,
    audit_log. `since`/`until` are ISO dates or datetimes.

    Requires an admin account; the API returns a permission error otherwise.
    Note that reads are not audited — only mutations and exports appear here.
    """
    limit = clamp_limit(limit)
    entity = check(entity, "audit entity type", AUDIT_ENTITY_TYPES)
    page = await api().get(
        "/audit_logs",
        params={"username": user, "action": action, "entity_type": entity,
                "entity_id": entity_id, "date_from": since, "date_to": until,
                "page_size": limit},
    )
    return envelope([audit_brief(a) for a in page["items"]], page["total"])
