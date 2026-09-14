"""Resources: small, stable context the model gets without spending a tool call.

§10. The schema resource matters more than it looks — without it the model
guesses at a status value, gets a 422, and retries, burning two round trips on
something that fits in 200 tokens.

Devices (100+) and tests (500+) are deliberately NOT resources. Those are tool
calls, because they are neither small nor stable.
"""

from __future__ import annotations

import json

from mcp.server.mcpserver.exceptions import ResourceError

from .client import ApiError
from .enums import (
    AUDIT_ENTITY_TYPES,
    DEVICE_STATUSES,
    TEST_OUTCOMES,
    TEST_TAGS,
    VENDOR_SUPPORT_STATUSES,
)
from .server import api, server

# The enum values come from `enums.py`, which is also what the tools validate
# their filter arguments against — so what the model reads here is exactly what
# will be accepted.
SCHEMA = {
    "device": {
        "identifier": "unique_id, e.g. 'dev-0042'",
        "status": list(DEVICE_STATUSES),
        "fields": [
            "unique_id", "status", "location", "make", "model", "firmware_version",
            "hardware_version", "architecture", "wan_ip", "lan_ip", "online_status",
            "last_seen_online", "misc_data", "checked_out_by_username", "checked_out_at",
            "checkout_purpose", "checkout_due",
        ],
        "notes": [
            "`online_status` comes from network scanning and is independent of `status`: "
            "a device can be `available` and offline.",
            "`checked_out_by`/`checked_out_at` are stamped by the API when status "
            "becomes `checked_out`, and cleared when it leaves that status.",
            "`checkout_purpose` is free text saying why the device is out, written by "
            "hand rather than stamped, and cleared on check-in with the two above.",
            "`checkout_due` is the day the device is due back. Both it and "
            "`checkout_purpose` are required to check a device out, and both are "
            "cleared on check-in.",
            "A device past `checkout_due` is OVERDUE. That state is derived, never "
            "stored: there is no 'overdue' status, and nothing is returned "
            "automatically — an overdue device is still `checked_out` and still has a "
            "holder. `find_due_devices()` lists overdue devices together with "
            "devices due in the next 3 days by default; `find_devices(overdue=True)` "
            "lists only overdue devices. Results report `due_in_days` and "
            "`overdue_days`.",
        ],
    },
    "software": {
        "identifier": "name, e.g. 'nmap' — case-insensitive, resolves to the current version",
        "fields": ["name", "version", "misc_data", "vendor_device_count", "version_count", "is_latest"],
        "notes": [
            "Rows sharing a name form a version group. The current version is the "
            "highest numeric version (6.0.0 remains current if 2.5.3 is added later).",
            "An empty version is a real state (unversioned software), not missing data.",
            "This entity was formerly called a Tool. `tool_name` in an old CSV export and "
            "`tool_targets` in older docs both mean Software.",
        ],
    },
    "test": {
        "outcome": list(TEST_OUTCOMES),
        "tag": list(TEST_TAGS),
        "fields": [
            "device_unique_id", "software_name", "software_version", "outcome",
            "tag", "data", "notes", "run_at", "created_by_username",
        ],
        "notes": [
            "`software_version` is snapshotted at creation and then frozen: the software "
            "moves on, the evidence does not.",
            "`data` is free-form JSONB and can be large; list tools report only whether "
            "it is present.",
        ],
    },
    "vendor_device": {
        "support_status": list(VENDOR_SUPPORT_STATUSES),
        "fields": [
            "vendor", "make", "model", "firmware_version", "hardware_version",
            "architecture", "support_status", "source", "notes",
        ],
        "notes": [
            "A vendor's compatibility CLAIM about hardware, attached to a piece of "
            "software. Not test evidence, and not necessarily hardware this fleet owns.",
        ],
    },
    "audit_log": {
        "entity_type": list(AUDIT_ENTITY_TYPES),
        "notes": [
            "Mutations and exports are logged. Reads are not, so absence from the audit "
            "log does not mean nobody looked.",
        ],
    },
}


@server.resource("testbench://schema", mime_type="application/json")
async def schema() -> str:
    """Enum values and field lists for every entity. Read this before filtering
    by a status, outcome, tag or support_status."""
    rendered = json.loads(json.dumps(SCHEMA))
    try:
        configured = await api().get("/entity-fields")
        overview = await api().get("/device-schema")
        device_types = await api().get("/device-types")
    except ApiError as exc:
        raise ResourceError(str(exc)) from exc
    for api_name, schema_name in (("devices", "device"), ("software", "software"), ("tests", "test")):
        fields = configured.get(api_name, [])
        rendered[schema_name]["fields"] = [field["key"] for field in fields]
        rendered[schema_name]["field_definitions"] = fields

    # Devices are configurable per type: the list above is the global set every
    # type inherits, and each type may add fields of its own or require ones the
    # global set leaves optional. Filtering and `get_device` use stable keys,
    # never labels, which an administrator may change at any time.
    rendered["device"]["device_types"] = [
        {
            "key": item["key"],
            "label": item["label"],
            "devices": item.get("device_count", 0),
            "required_fields": item.get("required_field_keys", []),
            "plugins": item.get("plugin_ids", []),
            "fields": [
                field["key"]
                for field in overview.get("types", {}).get(item["key"], {}).get("fields", [])
                if field.get("visible")
            ],
        }
        for item in device_types
    ]
    rendered["device"]["notes"].append(
        "Devices are split by device type. `find_devices(device_type='router')` takes the "
        "stable key shown in `device_types` above, or 'uncategorized' for devices with no "
        "type. A type's own fields are listed there; the `fields` list on this entity is "
        "the global set every type inherits."
    )
    rendered["configuration"] = {
        "revision": overview.get("revision"),
        "owner": "configMap" if overview.get("yaml_configured") else "database",
        "reconciliation": overview.get("reconciliation"),
        "storage": "Inventory attributes are one JSONB document per device; unique_id, relationships, audit ownership and timestamps are physical columns.",
        "note": (
            "Device fields, types and per-type plugin policy are configurable and change "
            "when an administrator publishes a new revision. Re-read this resource if a "
            "field key stops resolving."
        ),
    }
    return json.dumps(rendered, indent=2)


@server.resource("testbench://software-catalog", mime_type="application/json")
async def software_catalog() -> str:
    """Every piece of software by name, with its current version.

    Small (tens of rows) and it makes name resolution nearly free — the model can
    see what exists rather than guessing a name and taking a miss.
    """
    try:
        page = await api().get(
            "/software",
            params={"latest_only": True, "sort": "name", "order": "asc", "page_size": 1000},
        )
    except ApiError as exc:
        # ResourceError's message reaches the client; anything else is logged as
        # a crash and the client is told only the URI.
        raise ResourceError(str(exc)) from exc
    catalog = [
        {
            "name": s["name"],
            "current_version": s.get("version") or "(unversioned)",
            "versions": s.get("version_count", 1),
            "vendor_devices": s.get("vendor_device_count", 0),
        }
        for s in page["items"]
    ]
    return json.dumps(
        {
            "software": catalog,
            "total": page["total"],
            "note": "Current version of each name. Use `list_software_versions` for the rest.",
        },
        indent=2,
    )
