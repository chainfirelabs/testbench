"""A device's own history, read out of the audit log.

The audit log already records every write, so a per-device changelog is not a
second record to keep in step with the first — it is a projection of the one
that exists. What this module adds is the part that makes it readable by
someone who is not an auditor:

* one sentence per entry, rather than an action key and a JSON blob;
* field changes as a list of `label: old → new`, using the device type's own
  labels, so a row says "Firmware Version 1.2 → 1.3" and not `firmware_version`;
* redaction applied on the way *out* as well as on the way in.

That last point is the reason the projection exists at all rather than the page
reading `/audit_logs` directly. The audit log is admin-only, and deliberately:
its rows carry login attempts, IP addresses, user agents and — for a couple of
plugin callbacks — details written without passing through the device's own
redaction. A changelog anyone who can see the device may read has to be built
from a whitelist of what an entry is allowed to say, not from whatever the
writer happened to put in the detail. So `changes` only ever comes from a
field diff, and every value in it is masked again here against the device's
current sensitive fields.
"""

from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog, Device
from .device_schema import REDACTED, get_effective_fields

# Actions written against `entity_type="device"` that are about one device.
# An action not listed here still appears — the log is the record, and hiding
# an entry because this list is out of date would be the wrong failure — it
# simply gets its action key as its summary.
ACTION_SUMMARIES = {
    "device.create": "Device created",
    "device.update": "Device updated",
    "device.delete": "Device deleted",
    "device.scan": "Network scan",
    "device.info_start": "Device info lookup started",
    "device.info_failed": "Device info lookup could not start",
    "device.bulk": "Bulk edit",
    "device.import": "Imported",
    "plugin.configuration.device": "Plugin settings changed",
    "plugin.device_info.result": "Device info lookup finished",
    "plugin.network_scan.result": "Network scan finished",
    "plugin.device_reboot.result": "Reboot finished",
    "plugin.artifact.update": "Plugin steps saved",
    "plugin.artifact.delete": "Plugin steps cleared",
}


def _field_labels(db: Session, device: Device | None) -> tuple[dict[str, str], set[str]]:
    """This device type's labels, and which of its fields are secrets."""
    if device is None:
        return {}, set()
    fields = get_effective_fields(db, device.device_type_id)
    return (
        {field.key: field.label for field in fields},
        {field.key for field in fields if field.sensitive},
    )


def _diff_of(detail: dict) -> dict:
    """The field diff inside an audit detail, wherever that writer put it.

    `device.update` nests it under `diff`; the device-info plugin callback
    writes one under the same key; a detail with neither has no field changes
    to show, which is not the same as having none to record.
    """
    diff = detail.get("diff")
    return diff if isinstance(diff, dict) else {}


def _mask(value: Any) -> Any:
    """A secret's value, as a changelog is allowed to report it."""
    return REDACTED if value not in (None, "") else value


def _link_text(override: Any) -> str:
    """One link override as a sentence fragment: `https://…:8443`.

    An override sets a scheme, a port, or both, and says nothing about the rest
    of the address — so the rendering says nothing about it either, rather than
    inventing a host the entry cannot know was current at the time.
    """
    if not isinstance(override, dict) or not override:
        return "default"
    scheme, port = override.get("scheme"), override.get("port")
    return f"{scheme or 'default'}://…{f':{port}' if port else ''}"


def entry_changes(
    detail: dict, labels: dict[str, str], sensitive: set[str]
) -> list[dict]:
    """The field-level changes an audit entry describes, ready to render.

    Re-masked here rather than trusted: most writers redact before logging, but
    the plugin result callbacks diff the raw document, and an entry written
    before a field was *marked* sensitive still holds what it held. Masking on
    read means the answer follows the current schema either way.
    """
    changes = []
    for key, change in sorted(_diff_of(detail).items()):
        if not isinstance(change, dict):
            continue
        secret = key in sensitive
        changes.append({
            "field": key,
            "label": labels.get(key, key),
            "old": _mask(change.get("old")) if secret else change.get("old"),
            "new": _mask(change.get("new")) if secret else change.get("new"),
        })
    # A changed link override reads as a change to that field, because that is
    # what an operator did — they changed how LAN IP opens. It is labelled
    # apart from the value so the entry cannot be mistaken for the address
    # itself moving, and it is never masked: a scheme and a port are not
    # secrets even on a field whose value is one.
    link_diff = detail.get("link_diff")
    for key, change in sorted((link_diff or {}).items()):
        if not isinstance(change, dict):
            continue
        changes.append({
            "field": key,
            "label": f"{labels.get(key, key)} link",
            "old": _link_text(change.get("old")),
            "new": _link_text(change.get("new")),
        })
    return changes


def entry_summary(log: AuditLog, changes: list[dict]) -> str:
    """One line saying what happened, in the words of the thing that happened."""
    base = ACTION_SUMMARIES.get(log.action, log.action)
    detail = log.detail or {}
    if log.action == "device.update" and changes:
        named = ", ".join(change["label"] for change in changes[:3])
        more = len(changes) - 3
        return f"{base}: {named}{f' and {more} more' if more > 0 else ''}"
    if log.action == "device.scan":
        if detail.get("error"):
            return f"{base} failed"
        if "online" in detail:
            return f"{base}: {'online' if detail['online'] else 'offline'}"
    if log.action == "plugin.configuration.device" and detail.get("plugin_id"):
        return f"{base} ({detail['plugin_id']})"
    if log.action.startswith("plugin.") and detail.get("plugin_id"):
        return f"{base} ({detail['plugin_id']})"
    return base


def changelog_entry(
    log: AuditLog,
    labels: dict[str, str],
    sensitive: set[str],
    *,
    include_raw: bool,
) -> dict:
    """One audit row as a changelog entry.

    `include_raw` carries the untouched detail and the caller's IP alongside
    the projection — for an admin, who can read all of that in the audit log
    anyway, and for whom the raw entry is often the point.
    """
    detail = log.detail or {}
    changes = entry_changes(detail, labels, sensitive)
    return {
        "id": log.id,
        "timestamp": log.timestamp,
        "username": log.username,
        "action": log.action,
        "summary": entry_summary(log, changes),
        "changes": changes,
        "ip_address": log.ip_address if include_raw else None,
        "detail": detail if include_raw else None,
    }


def changelog_entries(
    db: Session,
    device: Device | None,
    logs: list[AuditLog],
    *,
    include_raw: bool,
) -> list[dict]:
    """A page of audit rows as changelog entries, resolving labels once."""
    labels, sensitive = _field_labels(db, device)
    return [changelog_entry(log, labels, sensitive, include_raw=include_raw) for log in logs]
