"""Compact projections and the truncation envelope.

§5.2: a tool must never return an export-sized payload. `GET /tests/export` is
~350 KB for 500 rows; the model needs the ten fields that answer the question
and a way to ask for the rest. §5.3: every list is paginated, capped, and
honest about being cut short.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .config import settings

NOTES_PREVIEW_CHARS = 160


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return settings.default_limit
    return max(1, min(int(limit), settings.max_limit))


def drop_empty(row: dict) -> dict:
    """Strip nulls, empty strings and empty dicts.

    Every key costs tokens, and a field that is null tells the model nothing it
    could not infer from the field's absence.
    """
    return {k: v for k, v in row.items() if v not in (None, "", {}, [])}


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= NOTES_PREVIEW_CHARS else text[: NOTES_PREVIEW_CHARS - 1] + "…"


def _date(value: str | None) -> str | None:
    """ISO timestamps trimmed to the day. Fleet questions are answered in days,
    and the time-of-day half of the string is pure token cost."""
    return value.split("T")[0] if value else None


def _overdue_days(d: dict) -> int | None:
    """How many days past its return date a device is, or None if it is not.

    The same rule the API derives `overdue` from: checked out, has a due date,
    and the date has passed. Nothing is returned automatically when it does —
    the device stays checked out and whoever has it is reminded.
    """
    due, status = d.get("checkout_due"), d.get("status")
    if status != "checked_out" or not due:
        return None
    days = (date.today() - date.fromisoformat(due[:10])).days
    return days if days > 0 else None


def _due_in_days(d: dict) -> int | None:
    """Days until a checked-out device is due; negative once overdue."""
    due, status = d.get("checkout_due"), d.get("status")
    if status != "checked_out" or not due:
        return None
    return (date.fromisoformat(due[:10]) - date.today()).days


# -- entity projections ---------------------------------------------------


def device_brief(d: dict) -> dict:
    return drop_empty({
        "unique_id": d.get("unique_id"),
        # The stable key, not the label: labels are presentation and an
        # administrator may rename one at any time.
        "device_type": d.get("device_type_key"),
        "status": d.get("status"),
        "location": d.get("location"),
        "make": d.get("make"),
        "model": d.get("model"),
        "online": d.get("online_status"),
        "checked_out_by": d.get("checked_out_by_username"),
        "checkout_due": d.get("checkout_due"),
        "due_in_days": _due_in_days(d),
        "overdue_days": _overdue_days(d),
    })


def device_full(d: dict) -> dict:
    return drop_empty({
        **device_brief(d),
        "firmware_version": d.get("firmware_version"),
        "hardware_version": d.get("hardware_version"),
        "architecture": d.get("architecture"),
        "wan_ip": d.get("wan_ip"),
        "lan_ip": d.get("lan_ip"),
        "last_seen_online": _date(d.get("last_seen_online")),
        "checked_out_at": _date(d.get("checked_out_at")),
        "checkout_purpose": d.get("checkout_purpose"),
        # Every installation-defined value, by stable key. The named fields
        # above are the ones that ship; this is what a type of device actually
        # carries here, including fields this code has never heard of.
        "data": d.get("data") or d.get("misc_data"),
        "created_at": _date(d.get("created_at")),
        "updated_at": _date(d.get("updated_at")),
    })


def software_brief(s: dict) -> dict:
    return drop_empty({
        "name": s.get("name"),
        # An unversioned row is a real state in this data model, not a gap.
        "version": s.get("version") or "(unversioned)",
        "version_count": s.get("version_count") if (s.get("version_count") or 1) > 1 else None,
        "is_latest": False if s.get("is_latest") is False else None,
        "vendor_device_count": s.get("vendor_device_count"),
        "component_count": len(s.get("bundle_components") or []) or None,
    })


def software_full(s: dict) -> dict:
    return drop_empty({
        **software_brief(s),
        "misc_data": s.get("misc_data"),
        "bundle_components": s.get("bundle_components") or None,
        "created_at": _date(s.get("created_at")),
        "updated_at": _date(s.get("updated_at")),
    })


def test_brief(t: dict) -> dict:
    return drop_empty({
        "device": t.get("device_unique_id"),
        "software": t.get("software_name"),
        "software_version": t.get("software_version") or None,
        "component": t.get("component_name"),
        "component_version": t.get("component_version") or None,
        "outcome": t.get("outcome"),
        "tag": t.get("tag"),
        "run_at": _date(t.get("run_at") or t.get("created_at")),
        "by": t.get("created_by_username"),
        "notes": _preview(t.get("notes")),
        # `data` is free-form JSONB and can be arbitrarily large. Signal that it
        # exists; `get_device`/`find_tests` callers can ask about a specific row.
        "has_data": bool(t.get("data")) or None,
    })


def vendor_device_brief(v: dict) -> dict:
    custom = {
        key: value for key, value in (v.get("misc_data") or {}).items()
        if not key.startswith("__")
    }
    return drop_empty({
        "make": v.get("make"),
        "model": v.get("model"),
        "firmware_version": v.get("firmware_version"),
        "hardware_version": v.get("hardware_version"),
        "architecture": v.get("architecture"),
        "support_status": v.get("support_status"),
        "source": v.get("source"),
        "notes": _preview(v.get("notes")),
        "custom_fields": custom,
    })


def audit_brief(a: dict) -> dict:
    return drop_empty({
        "timestamp": a.get("timestamp"),
        "user": a.get("username"),
        "action": a.get("action"),
        "entity": a.get("entity_type"),
        "entity_id": a.get("entity_id"),
        "ip": a.get("ip_address"),
        "detail": _summarize_detail(a.get("detail") or {}),
    })


def _summarize_detail(detail: dict) -> Any:
    """Audit detail holds whole-row snapshots and field diffs; a delete snapshot
    of a device is ~20 fields. Reduce it to the shape, keeping diffs (which are
    the interesting part) and naming the rest."""
    if not detail:
        return None
    if "diff" in detail:
        diff = detail["diff"]
        if isinstance(diff, dict):
            return {"changed": {k: v for k, v in list(diff.items())[:8]}}
    if "snapshot" in detail:
        snap = detail["snapshot"]
        name = snap.get("unique_id") or snap.get("name") if isinstance(snap, dict) else None
        return {"snapshot_of": name or "row"}
    return {k: v for k, v in list(detail.items())[:6]}


# -- the envelope ---------------------------------------------------------


def envelope(
    items: list[dict],
    total: int | None = None,
    offset: int | None = None,
    **extra: Any,
) -> dict:
    """Wrap a result list, trimming the tail until it fits the byte budget.

    Returns `{items, total, returned, truncated}` rather than silently cutting,
    so the model can say "showing 20 of 340" instead of reporting 20 as the
    whole answer.

    Pass `offset` for a tool that can be called again for the next chunk. The
    reply then carries `next_offset`, counted from the rows that SURVIVED
    trimming rather than from the page that was requested. That distinction is
    the whole point: ask for 200 rows at offset 0, have the byte budget keep
    only 180, and a next page computed from the request would start at 200 and
    drop rows 180-199 without a word. Counted from what was kept, the next call
    starts at 180 and the sweep stays complete.
    """
    total = len(items) if total is None else total
    kept = list(items)
    while kept and _size({**extra, "items": kept}) > settings.max_response_bytes:
        kept.pop()
    out: dict[str, Any] = {**extra, "items": kept, "total": total, "returned": len(kept)}

    trimmed = len(kept) < len(items)
    if offset is None:
        # An unpaginated tool: the caller sees this page or nothing.
        remaining = total - len(kept)
    else:
        out["offset"] = offset
        remaining = total - (offset + len(kept))
        if remaining > 0:
            out["next_offset"] = offset + len(kept)

    if remaining > 0:
        out["truncated"] = True
        seen = (offset or 0) + len(kept)
        # Which advice is useful depends on which ceiling was hit: a bigger
        # `limit` does nothing if the response was already too large to send.
        if offset is not None:
            out["note"] = (
                f"Showing {len(kept)} of {total}, rows {offset + 1}-{seen}. "
                f"Call again with offset={offset + len(kept)} for the next chunk."
            )
        elif trimmed:
            out["note"] = (
                f"Showing {len(kept)} of {total}. This response hit its size "
                "budget; narrow the search to see the rest."
            )
        else:
            out["note"] = (
                f"Showing {len(kept)} of {total}. Narrow the search or raise "
                f"`limit` (max {settings.max_limit}) to see more."
            )
    return out


def _size(payload: Any) -> int:
    """Serialized size as the client will see it — the SDK emits indented JSON,
    so measuring compact JSON would under-count by about a third."""
    return len(json.dumps(payload, indent=2, default=str))


def no_match(kind: str, term: str, candidates: list[str]) -> dict:
    """A miss, with near misses attached (§6).

    Returned rather than raised: "no such device" is an answer, and handing back
    an error string makes the model retry the same call instead of picking a
    real identifier from the suggestions.
    """
    out: dict[str, Any] = {"found": False, "searched_for": term}
    if candidates:
        out["did_you_mean"] = candidates
        out["message"] = f"No {kind} matching '{term}'. Did you mean: {', '.join(candidates)}?"
    else:
        out["message"] = f"No {kind} matching '{term}', and nothing with a similar name."
    return out
