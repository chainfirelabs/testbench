"""Audit logging: every mutating action writes an append-only audit row
in the same transaction as the change."""


from fastapi import Request
from sqlalchemy.orm import Session

from ..db import jsonable
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


# The diff goes into a JSONB column, and so does the device document; the rule
# is the same in both places and now lives in one.
_jsonable = jsonable


def field_diff(old: dict, new: dict) -> dict:
    """Field-level diff between two flat dicts."""
    diff = {}
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            diff[key] = {"old": _jsonable(old.get(key)), "new": _jsonable(new.get(key))}
    return diff
