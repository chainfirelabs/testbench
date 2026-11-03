"""The read tools (MCP_SERVER.md §5, read half only).

Task-shaped, not endpoint-shaped: these do not mirror the REST paths. A model
given 40 CRUD tools picks badly; one given a dozen intent-named tools picks
well. Nothing here writes — no `set_`, `record_` or `import_` tool exists yet.
"""

from __future__ import annotations

import codecs
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError

from .client import NotFound
from .config import settings
from .enums import (
    AUDIT_ENTITY_TYPES,
    DEVICE_ARCHITECTURES,
    DEVICE_STATUSES,
    TEST_OUTCOMES,
    TEST_TAGS,
    VENDOR_SUPPORT_STATUSES,
    check,
)
from .format import (
    artifact_brief,
    audit_brief,
    changelog_brief,
    clamp_limit,
    device_brief,
    device_full,
    drop_empty,
    envelope,
    no_match,
    software_brief,
    software_full,
    test_brief,
    test_full,
    test_run_brief,
    test_run_full,
    test_with_device,
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


async def _fetch_window(client, path: str, params: dict, offset: int, limit: int) -> dict:
    """Rows `offset`..`offset + limit` of a page-numbered list, in at most two
    requests, so a chunked tool can resume at any row the byte budget left off."""
    first = offset // limit + 1
    skip = offset % limit
    page = await client.get(path, params={**params, "page": first, "page_size": limit})
    rows = page["items"]
    if skip and len(rows) == limit:
        more = await client.get(path, params={**params, "page": first + 1, "page_size": limit})
        rows = rows + more["items"]
    return {"items": rows[skip:skip + limit], "total": page["total"]}


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


@server.tool()
async def get_device_history(identifier: str, offset: int = 0, limit: int | None = None) -> dict:
    """What has happened to one device, newest first: who changed what, and when.

    The tool for "who changed router-1's firmware?" or "what happened to this
    device last week?". Each entry says who, when and what in a sentence, and
    lists field changes as old → new under the device type's current labels.
    Secret fields are masked. Readable with any key that can read the device;
    `search_audit`, which needs an admin key, is for questions across devices.

    `identifier` is a unique_id or UUID. Long histories come in chunks: when
    `next_offset` is present, call again with it to continue.
    """
    limit = clamp_limit(limit)
    offset = max(0, int(offset or 0))
    client = api()
    try:
        record = await resolve_device(client, identifier)
    except Unresolved as miss:
        return miss.payload
    page = await _fetch_window(client, f"/devices/{quote(record['id'], safe='')}/changelog", {}, offset, limit)
    return envelope([changelog_brief(e) for e in page["items"]], page["total"], offset,
                    device=describe_device(record))


@server.tool()
async def get_fleet_summary() -> dict:
    """The fleet at a glance: how many devices there are, how many are online,
    offline or never scanned (the latest network scan), and how many are in
    each status (available, checked_out, inventory, missing, broken).

    Counted by the API without listing devices. For which devices, use
    `find_devices`; for overdue checkouts, `find_due_devices`.
    """
    client = api()
    scan = await client.get("/dashboard/fleet-summary")
    statuses = {}
    for status in DEVICE_STATUSES:
        page = await client.get("/devices", params={"status": status, "page_size": 1})
        statuses[status] = page.get("total", 0)
    return {
        "devices": scan.get("total", 0),
        "scan": {
            "online": scan.get("online", 0),
            "offline": scan.get("offline", 0),
            "never_scanned": scan.get("never_scanned", 0),
        },
        "status": statuses,
        "as_of": _minute_stamp(scan.get("as_of")),
    }


def _minute_stamp(value: str | None) -> str | None:
    return value[:16].replace("T", " ") if value else None


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
    component_name: str | None = None,
    component_version: str | None = None,
    component_status: str | None = None,
    limit: int | None = None,
) -> dict:
    """Hardware the VENDOR CLAIMS this software supports.

    This is the vendor's compatibility list. These are not necessarily devices
    this fleet owns and they carry no test evidence whatsoever. For devices
    actually run against, call `list_devices_tested_with` instead.

    `support_status` on each row is the general vendor claim (supported,
    partial, unsupported, or planned). Optional `component_support` entries
    override it for specific component versions; neither is a test result.
    Component filters use effective support and return `matching_components`,
    including whether each status was inherited from the suite.
    """
    limit = clamp_limit(limit)
    try:
        record = await resolve_software(api(), software, version)
    except Unresolved as miss:
        return miss.payload
    page = await api().get(
        f"/software/{quote(record['id'], safe='')}/vendor-devices",
        params={"search": search, "component_name": component_name,
                "component_version": component_version,
                "component_status": check(component_status, "component status", VENDOR_SUPPORT_STATUSES),
                "page_size": limit},
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
async def find_vendor_devices(
    search: str | None = None,
    software: str | None = None,
    make: str | None = None,
    model: str | None = None,
    firmware_version: str | None = None,
    hardware_version: str | None = None,
    architecture: str | None = None,
    support_status: str | None = None,
    component_name: str | None = None,
    component_version: str | None = None,
    component_status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Search every vendor compatibility list at once, by hardware.

    The reverse of `list_vendor_supported_devices`, which starts from a
    software and lists its claims. This starts from the hardware: "does
    anything claim to support a Cisco ISR 4331?" is a question no single
    software's list can answer, and each row here names the software and
    version the claim belongs to.

    Claims, not evidence. A row means a vendor published support for that
    hardware — not that this fleet owns one, and not that anyone has run it.
    `support_status` is the vendor's own word (supported, partial, unsupported,
    planned), and "unsupported" rows are returned like any other, because
    "the vendor says no" is an answer. For what has actually been run, call
    `list_devices_tested_with` or `list_software_tested_on`.

    `search` matches make, model, firmware, hardware version, architecture,
    source, notes, component name/version and the software's name at once; the named filters are
    narrower and combine with AND. `software` takes a software name and covers
    every version of it. Component status includes inherited suite support.

    A claim on a superseded version is still returned, marked
    `software_is_latest: false` — it is real, but it is not current guidance.

    To sweep more than one response, pass `next_offset` back as `offset`.
    """
    limit = clamp_limit(limit)
    support_status = check(support_status, "support status", VENDOR_SUPPORT_STATUSES)
    component_status = check(component_status, "component status", VENDOR_SUPPORT_STATUSES)
    architecture = check(architecture, "architecture", DEVICE_ARCHITECTURES)
    page = await api().get(
        "/vendor-devices",
        params={
            "search": search, "software": software,
            "make": make, "model": model,
            "firmware_version": firmware_version,
            "hardware_version": hardware_version,
            "architecture": architecture,
            "support_status": support_status,
            "component_name": component_name,
            "component_version": component_version,
            "component_status": component_status,
            "sort": "make", "order": "asc",
            "page_size": limit, "offset": max(0, offset),
        },
    )
    return envelope(
        [
            {
                "software": item.get("software_name"),
                "software_version": item.get("software_version") or None,
                "software_is_latest": item.get("software_is_latest", True),
                **vendor_device_brief(item),
            }
            for item in page["items"]
        ],
        page["total"],
        offset=max(0, offset),
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


@server.tool()
async def list_vendor_claims_for_device(identifier: str) -> dict:
    """Software whose VENDOR CLAIMS to support this device, best claim first.

    The device-first view of the vendor compatibility lists: "what software
    says it supports router-1?". Each row is one software version with the
    vendor's `support_status` for this device, `matched_on` (the device fields
    the claim pinned down: a short list means a broad claim such as "any
    Cisco", not a weak one) and the matching claims themselves.

    Claims, not evidence: for what has actually been run on the device, call
    `list_software_tested_on`. A claim on a superseded version is still listed,
    marked `software_is_latest: false`.
    """
    client = api()
    try:
        record = await resolve_device(client, identifier)
    except Unresolved as miss:
        return miss.payload
    result = await client.get(f"/devices/{quote(record['id'], safe='')}/compatible-software")
    items = []
    for item in result.get("items", []):
        software = item.get("software") or {}
        # Only the fields this version shows, and never a secret one: the
        # claims' own custom values are filtered against the version's layout.
        shown = {field["key"] for field in item.get("fields") or []
                 if not field.get("sensitive")}
        claims = []
        for claim in item.get("vendor_devices") or []:
            safe = dict(claim)
            safe["misc_data"] = {k: v for k, v in (claim.get("misc_data") or {}).items() if k in shown}
            claims.append({k: v for k, v in vendor_device_brief(safe).items() if k != "software_id"})
        items.append(drop_empty({
            "software": software.get("name"),
            "software_version": software.get("version") or None,
            "software_id": software.get("id"),
            "software_is_latest": software.get("is_latest", True),
            "support_status": item.get("support_status"),
            "matched_on": item.get("matched_on"),
            "claims": claims,
        }))
    return envelope(items, device=describe_device(record),
                    sense="vendor compatibility claims — not evidence")


@server.tool()
async def list_owned_devices_matching_claim(
    claim_id: str, software_id: str, offset: int = 0, limit: int | None = None,
) -> dict:
    """The devices in this fleet that one vendor claim describes.

    "The vendor says nmap supports a Cisco ISR 4331; do we own one?" Take
    `claim_id` (a claim row's `id`) and `software_id` from a
    `list_vendor_supported_devices`, `find_vendor_devices` or
    `list_vendor_claims_for_device` row; rows already show `owned_matches` and
    the first few unique_ids, so call this when there are more.

    Owning matching hardware is not test evidence: for whether it was run,
    call `list_devices_tested_with`. Rows are unique_ids; `get_device` gives
    the rest.
    """
    # The API pages this list at 100 at most.
    limit = min(clamp_limit(limit), 100)
    offset = max(0, int(offset or 0))
    path = (f"/software/{quote(software_id, safe='')}/vendor-devices/"
            f"{quote(claim_id, safe='')}/inventory-matches")
    try:
        page = await _fetch_window(api(), path, {}, offset, limit)
    except NotFound as exc:
        return {"claim_id": claim_id, "found": False,
                "note": f"{exc}. Take `id` and `software_id` from a vendor claim row."}
    return envelope([{"unique_id": row["unique_id"]} for row in page["items"]],
                    page["total"], offset, claim_id=claim_id,
                    sense="owned hardware matching the claim — not test evidence")


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

    Tests recorded by one pipeline or suite execution belong to a test run:
    rows show it under `test_run`, and `find_test_runs` / `get_test_run` work
    with whole runs.

    For a summary rather than individual runs, use `list_devices_tested_with` or
    `list_software_tested_on` — they aggregate in the database instead. For
    every test of one software version with full device details, use
    `list_software_tests`.
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


@server.tool()
async def list_software_tests(
    software: str,
    version: str | None = None,
    outcome: str | None = None,
    devices: list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> dict:
    """EVERY test of one software version, each with its device's full details.

    The tool for "show me all tests for nmap 7.94" or "what was nmap 7.94 tested
    on, and how did it go". One row per test — outcome, tag, component, run
    date, test run, notes and the installation's own test fields — with a
    `device` object: make, model, serial, firmware and the rest of its fields
    (passwords and other secrets are never included). It is the same data as
    Software → Tested Devices → Export tests.

    `software` is a name; `version` pins the version, otherwise the CURRENT one
    is used — check the echoed `software` before reporting. `outcome` is pass,
    fail or warn. `devices` narrows to some unique_ids. `summary` counts every
    matching test, not just the rows returned. A large version comes in chunks:
    when `next_offset` is present, call again with it to continue, and say the
    answer is partial until you have.

    Each row's `id` works with `get_test_output` and `list_test_artifacts`. For
    per-device counts only, `list_devices_tested_with` is lighter; for tests
    across software or within a date range, use `find_tests`.
    """
    limit = clamp_limit(limit)
    offset = max(0, int(offset or 0))
    client = api()
    try:
        record = await resolve_software(client, software, version)
    except Unresolved as miss:
        return miss.payload
    params: dict[str, Any] = {
        "format": "json", "include_device": "true", "software_id": record["id"],
        "outcome": check(outcome, "test outcome", TEST_OUTCOMES),
    }
    if devices:
        params["include__device_unique_id"] = json.dumps(list(devices))
    rows = await client.get("/tests/export", params=params)
    # The export is oldest first; a question about tests wants the newest.
    rows.reverse()
    summary = {
        "tests": len(rows),
        "devices": len({row.get("device_unique_id") for row in rows}),
        "outcomes": _outcome_counts(rows),
    }
    return envelope(
        [test_with_device(row) for row in rows[offset:offset + limit]],
        len(rows),
        offset,
        software=describe_software(record),
        summary=summary,
    )


def _in_window(test: dict, start: datetime | None, end: datetime | None) -> bool:
    stamp = _ts(test.get("run_at") or test.get("created_at"))
    if stamp is None:
        return False
    return not ((start and stamp < start) or (end and stamp >= end))


# -- test runs ----------------------------------------------------------

UUID_LENGTH = 36


async def _resolve_run(client, identifier: str) -> tuple[dict, str | None]:
    """A test run by id, external id (a CI pipeline id, say) or exact name.

    A name can belong to many runs — "Nightly" every night — so it means the
    newest run of that name, and the returned note says so. A partial name is
    not guessed at: the near misses come back instead.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        raise Unresolved(no_match("test run", identifier, []))
    if len(identifier) == UUID_LENGTH:
        try:
            return await client.get(f"/test-runs/{quote(identifier, safe='')}"), None
        except NotFound:
            pass
    page = await client.get("/test-runs", params={
        "search": identifier, "sort": "started_at", "order": "desc", "page_size": 200})
    runs = page["items"]
    wanted = identifier.lower()
    for run in runs:
        if (run.get("external_id") or "").lower() == wanted:
            return run, None
    named = [run for run in runs if (run.get("name") or "").lower() == wanted]
    if named:
        note = None
        if len(named) > 1:
            note = (f"{len(named)} runs are named '{named[0]['name']}'; this is the newest. "
                    f"Use `find_test_runs` to see the others, and pass a run's `id` to choose one.")
        return named[0], note
    suggestions = list(dict.fromkeys(run["name"] for run in runs))[:8]
    raise Unresolved(no_match("test run", identifier, suggestions))


@server.tool()
async def find_test_runs(
    search: str | None = None,
    software: str | None = None,
    software_version: str | None = None,
    device: str | None = None,
    failing: bool = False,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict:
    """Find test runs (pipeline or suite executions), newest first.

    The tool for "show me the latest test run for nmap", "the last Nightly
    run" (`search="Nightly", limit=1`) or "recent runs with failures". Each row
    is a whole run: its name, start and finish times, test count, pass/fail/warn
    counts, number of shared files and CI link.

    `search` matches the run's name and external id (a CI pipeline id) in part.
    `software` is a name and finds runs that tested ANY version of it, unless
    `software_version` pins one. `device` is a unique_id. `failing=true` keeps
    runs with at least one failed test. `since`/`until` are ISO dates or
    datetimes on the run's start, inclusive of whole days when given as dates.

    Pass a row's `id` to `get_test_run` for the run's files and its individual
    test cases.
    """
    limit = clamp_limit(limit)
    client = api()
    resolved: dict[str, str] = {}
    params: dict[str, Any] = {"search": search, "sort": "started_at", "order": "desc"}
    try:
        if software:
            record = await resolve_software(client, software, software_version)
            # A pinned version, or a software id (which names one version).
            if software_version or record["id"] == software.strip():
                params["software_id"] = record["id"]
                resolved["software"] = describe_software(record)
            else:
                params["software_name"] = record["name"]
                resolved["software"] = f"{record['name']} (any version)"
        if device:
            record = await resolve_device(client, device)
            params["device_id"] = record["id"]
            resolved["device"] = describe_device(record)
        start = _bound(since, end=False)
        end = _bound(until, end=True)
    except Unresolved as miss:
        return miss.payload

    if start is None and end is None and not failing:
        page = await client.get("/test-runs", params={**params, "page_size": limit})
        rows, total = page["items"], page["total"]
    else:
        # The runs endpoint filters neither by date nor by outcome, so both are
        # applied here over a bounded scan of the newest matching runs.
        scanned = await fetch_pages(client, "/test-runs", params, settings.max_scan_rows)
        rows = [
            run for run in scanned
            if (not failing or (run.get("fail_count") or 0) > 0)
            and _in_window({"run_at": run.get("started_at") or run.get("created_at")}, start, end)
        ]
        total = len(rows)
        if len(scanned) >= settings.max_scan_rows:
            resolved["scan_note"] = (
                f"Only the {settings.max_scan_rows} most recent matching runs were "
                f"searched; add filters to be sure of completeness."
            )
        rows = rows[:limit]
    return envelope([test_run_brief(run) for run in rows], total, **resolved)


@server.tool()
async def get_test_run(
    run: str,
    outcome: str | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> dict:
    """Everything about one test run: its details, the files it shares with all
    of its tests (a pytest or JUnit report, say), and its individual test cases.

    `run` is a run's `id` from `find_test_runs`, its external id, or its exact
    name — a name means the NEWEST run of that name, so check the echoed `run`
    (and any `note`) before reporting. For "the latest run of X" where X is only
    part of a name, use `find_test_runs(search=X, limit=1)` first.

    `outcome` (pass, fail or warn) narrows the test cases, e.g. to see only what
    failed. Each test case's `id` works with `get_test`, `get_test_output` (its
    stdout/log) and `list_test_artifacts`; a shared file's `id` works with
    `read_artifact(artifact_id, run_id=...)`. A large run's test cases come in
    chunks: when `next_offset` is present, call again with it to continue.
    """
    limit = clamp_limit(limit)
    offset = max(0, int(offset or 0))
    client = api()
    try:
        record, note = await _resolve_run(client, run)
    except Unresolved as miss:
        return miss.payload
    run_id = quote(record["id"], safe="")
    files = await client.get(f"/test-runs/{run_id}/artifacts")
    page = await _fetch_window(client, "/tests", {
        "test_run_id": record["id"], "outcome": check(outcome, "test outcome", TEST_OUTCOMES),
    }, offset, limit)
    # The run is the same on every row, so it is said once, above them.
    tests = [{k: v for k, v in test_brief(t).items() if k not in {"test_run", "test_run_id"}}
             for t in page["items"]]
    extra: dict[str, Any] = {
        "run": test_run_full(record),
        "shared_files": [artifact_brief(item) for item in files.get("items", [])],
    }
    if note:
        extra["note_on_run"] = note
    return envelope(tests, page["total"], offset, **extra)


# -- test cases and their files ---------------------------------------------


@server.tool()
async def get_test(test_id: str) -> dict:
    """One test case in full: device, software, outcome, notes, its own fields,
    the run it belongs to, and the files attached to it and its run.

    `test_id` is the `id` from a `find_tests`, `get_test_run` or `get_device`
    row. For the text it logged, use `get_test_output`; to read one of the
    files, `read_artifact`.
    """
    client = api()
    path = quote(test_id, safe="")
    try:
        test = await client.get(f"/tests/{path}")
    except NotFound:
        return {"test_id": test_id, "found": False,
                "note": "No test has this id. Take `id` from a find_tests or get_test_run row."}
    out = test_full(test)
    if test.get("artifact_count") or test.get("test_run_artifact_count"):
        files = await client.get(f"/tests/{path}/artifacts")
        out.update(_files(files))
    return drop_empty(out)


def _files(listing: dict) -> dict:
    return {
        "files": [artifact_brief(item) for item in listing.get("items", [])],
        "run_files": [artifact_brief(item) for item in listing.get("test_run_items", [])],
    }


@server.tool()
async def get_test_output(test_id: str, tail: bool = False) -> dict:
    """The text output (stdout/log) recorded for one test case.

    `test_id` is the `id` from a `find_tests`, `get_test_run` or `get_device`
    row; rows whose test has output show its filename under `output`. Long
    output is cut to fit the response: by default the beginning is kept; pass
    `tail=true` for the end, which is usually where a failure is reported.
    """
    try:
        result = await api().get(f"/tests/{quote(test_id, safe='')}/output")
    except NotFound:
        return {"test_id": test_id, "found": False,
                "note": "No test has this id. Take `id` from a find_tests or get_test_run row."}
    meta = result.get("output")
    if not meta:
        return {"test_id": test_id, "has_output": False}
    out: dict[str, Any] = {
        "test_id": test_id, "has_output": True,
        "filename": meta.get("filename"), "size_bytes": meta.get("size_bytes"),
    }
    if result.get("error"):
        out["error"] = result["error"]
        return out
    text = result.get("text") or ""
    budget = _text_budget()
    if len(text) > budget:
        out["truncated"] = True
        out["note"] = (
            f"Showing the {'last' if tail else 'first'} {budget} of {len(text)} characters. "
            f"Call again with tail={'false' if tail else 'true'} for the other end."
        )
        text = text[-budget:] if tail else text[:budget]
    out["text"] = text
    return out


def _text_budget() -> int:
    # Leave headroom under the byte budget for the fields around the text.
    return max(1024, settings.max_response_bytes - 2048)


@server.tool()
async def list_test_artifacts(test_id: str) -> dict:
    """The files (reports, captures, screenshots) attached to one test case,
    and under `run_files` the ones its test run shares with all of its tests.

    `test_id` is the `id` from a `find_tests`, `get_test_run` or `get_device`
    row. Lists names, types and sizes; pass a file's `id` with this `test_id`
    to `read_artifact` to read a text file. Binary files are downloaded in the
    TestBench UI or with its API clients.
    """
    try:
        result = await api().get(f"/tests/{quote(test_id, safe='')}/artifacts")
    except NotFound:
        return {"test_id": test_id, "found": False,
                "note": "No test has this id. Take `id` from a find_tests or get_test_run row."}
    files = _files(result)
    extra: dict[str, Any] = {"test_id": test_id}
    if result.get("test_run"):
        extra["test_run"] = result["test_run"]
    if files["run_files"]:
        extra["run_files"] = files["run_files"]
    return envelope(files["files"], **extra)


BINARY_TYPES = ("image/", "video/", "audio/", "font/")
BINARY_SUBTYPES = {
    "application/zip", "application/gzip", "application/x-gzip", "application/x-tar",
    "application/x-7z-compressed", "application/pdf", "application/vnd.tcpdump.pcap",
    "application/x-bzip2", "application/x-xz", "application/x-pcapng",
}
SNIFF_BYTES = 8192


def _decode(data: bytes, *, cut_start: bool, cut_end: bool) -> str | None:
    """`data` as text, or None when it looks binary. Where the file was cut, a
    UTF-8 character may be split; only a cut edge may be incomplete."""
    if b"\x00" in data[:SNIFF_BYTES] or b"\x00" in data[-SNIFF_BYTES:]:
        return None
    if cut_start:
        # Drop the continuation bytes of a character whose start was cut off.
        start = 0
        while start < min(3, len(data)) and data[start] & 0xC0 == 0x80:
            start += 1
        data = data[start:]
    try:
        # Not final at a cut: a character split there is dropped, not an error.
        return codecs.getincrementaldecoder("utf-8")().decode(data, final=not cut_end)
    except UnicodeDecodeError:
        pass
    # Not UTF-8 but free of NULs: a Latin-1 log, unless it is full of control bytes.
    text = data.decode("latin-1")
    sample = text[:SNIFF_BYTES]
    controls = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n\r\f\x1b")
    return None if controls > len(sample) // 100 else text


@server.tool()
async def read_artifact(
    artifact_id: str,
    test_id: str | None = None,
    run_id: str | None = None,
    tail: bool = False,
) -> dict:
    """Read a text file attached to a test case or a test run: a JUnit/pytest
    report, a JSON or XML result, a log.

    Give `artifact_id` and the `test_id` or `run_id` it was listed under — from
    `get_test`, `list_test_artifacts` or `get_test_run`. A test's `test_id` also
    reaches its run's shared files. Long files are cut to fit: the beginning by
    default, `tail=true` for the end. Binary files (archives, images, packet
    captures) are not read; the reply says so and gives their name and size.
    """
    if bool(test_id) == bool(run_id):
        raise ToolError("Give exactly one of `test_id` or `run_id`: the one the file was listed under.")
    client = api()
    try:
        if test_id:
            listing = await client.get(f"/tests/{quote(test_id, safe='')}/artifacts")
            own = {item["id"]: item for item in listing.get("items", [])}
            shared = {item["id"]: item for item in listing.get("test_run_items", [])}
            if artifact_id in own:
                meta = own[artifact_id]
                path = f"/tests/{quote(test_id, safe='')}/artifacts/{quote(artifact_id, safe='')}/download"
            elif artifact_id in shared:
                meta = shared[artifact_id]
                run = quote(listing["test_run"]["id"], safe="")
                path = f"/test-runs/{run}/artifacts/{quote(artifact_id, safe='')}/download"
            else:
                meta = None
            choices = list(own.values()) + list(shared.values())
        else:
            listing = await client.get(f"/test-runs/{quote(run_id, safe='')}/artifacts")
            choices = listing.get("items", [])
            meta = next((item for item in choices if item["id"] == artifact_id), None)
            path = f"/test-runs/{quote(run_id, safe='')}/artifacts/{quote(artifact_id, safe='')}/download"
    except NotFound:
        owner = f"test {test_id}" if test_id else f"test run {run_id}"
        return {"artifact_id": artifact_id, "found": False, "note": f"No {owner} exists."}
    if meta is None:
        return {"artifact_id": artifact_id, "found": False,
                "note": "That file is not attached here.",
                "files": [artifact_brief(item) for item in choices]}

    out: dict[str, Any] = {**artifact_brief(meta)}
    content_type = (meta.get("content_type") or "").split(";")[0].strip().lower()
    if content_type.startswith(BINARY_TYPES) or content_type in BINARY_SUBTYPES:
        out["readable"] = False
        out["note"] = "A binary file; download it in the TestBench UI to open it."
        return out
    budget = _text_budget()
    data, read, stopped = await client.read_bytes(
        path, keep=budget, tail=tail, max_bytes=settings.max_artifact_scan_bytes)
    size = meta.get("size_bytes") or read
    cut = size > len(data)
    text = _decode(data, cut_start=tail and cut, cut_end=cut and (not tail or stopped))
    if text is None:
        out["readable"] = False
        out["note"] = "This file is not text; download it in the TestBench UI to open it."
        return out
    if stopped and tail:
        out["truncated"] = True
        out["note"] = (
            f"The file is larger than the {settings.max_artifact_scan_bytes} bytes this "
            f"server reads, so its end could not be reached. Showing bytes up to that point."
        )
    elif size > len(data):
        out["truncated"] = True
        out["note"] = (
            f"Showing the {'last' if tail else 'first'} {len(data)} of {size} bytes. "
            f"Call again with tail={'false' if tail else 'true'} for the other end."
        )
    out["text"] = text
    return out


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
