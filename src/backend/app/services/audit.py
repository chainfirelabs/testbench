"""Audit logging: every mutating action writes an append-only audit row
in the same transaction as the change."""

from datetime import date, datetime

from fastapi import Request
from sqlalchemy.orm import Session

from ..models import AuditLog, User


def log_action(
    db: Session,
    user: User | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
    request: Request | None = None,
) -> AuditLog:
    detail = dict(detail or {})
    # An action taken with an API key records which key, so revoking one answers
    # "what did it do?" rather than only "who owned it?". Set by
    # `_from_api_key` in api/deps.py; absent for a browser session.
    key_id = getattr(request.state, "api_key_id", None) if request else None
    if key_id:
        detail.setdefault("via", "api_key")
        detail.setdefault("api_key_id", key_id)
        detail.setdefault("api_key_label", getattr(request.state, "api_key_label", None))

    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        detail=detail,
        ip_address=request.client.host if request and request.client else None,
        user_agent=(request.headers.get("user-agent") if request else None),
    )
    db.add(entry)
    return entry


def _jsonable(value):
    """Coerce a column value to something JSONB will take.

    The diff goes into a JSONB column, so a value the JSON encoder does not
    know is not a formatting problem — it is a 500 on the write it was
    describing. Dates and times are the ones that reach here (checkout_due,
    checked_out_at, last_seen_online); everything else the models hold is
    already a JSON primitive.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def field_diff(old: dict, new: dict) -> dict:
    """Field-level diff between two flat dicts."""
    diff = {}
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            diff[key] = {"old": _jsonable(old.get(key)), "new": _jsonable(new.get(key))}
    return diff
