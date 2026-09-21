from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, User
from ..schemas import AuditOut, Page
from ..services.audit import log_action
from ..services.io import streaming_export_response
from ..services.list_filters import exclude_clause, excluded_values, include_clause
from .deps import require_audit_view

router = APIRouter(prefix="/audit_logs", tags=["audit_logs"])

EXPORT_COLUMNS = ["id", "timestamp", "username", "action", "entity_type", "entity_id", "detail", "ip_address"]


def _as_datetime(value: str | None, field: str) -> datetime | None:
    """Parse a date filter. Handed straight to the query, an unparseable string
    reaches Postgres as a cast error — a 500 for what is a bad request."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be an ISO 8601 date or datetime")


def _query_logs(
    db: Session,
    username: str | None,
    action: str | None,
    entity_type: str | None,
    entity_id: str | None,
    date_from: str | None,
    date_to: str | None,
):
    q = select(AuditLog)
    if username:
        q = q.where(AuditLog.username.ilike(f"%{username}%"))
    if action:
        q = q.where(AuditLog.action.ilike(f"%{action}%"))
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    start = _as_datetime(date_from, "date_from")
    if start is not None:
        q = q.where(AuditLog.timestamp >= start)
    end = _as_datetime(date_to, "date_to")
    if end is not None:
        q = q.where(AuditLog.timestamp <= end)
    return q


@router.get("", response_model=Page[AuditOut])
def list_audit_logs(
    request: Request,
    search: str | None = None,
    username: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "timestamp",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_view),
):
    q = _query_logs(db, username, action, entity_type, entity_id, date_from, date_to)
    if search:
        like = f"%{search}%"
        q = q.where(or_(
            AuditLog.username.ilike(like), AuditLog.action.ilike(like),
            AuditLog.entity_type.ilike(like), AuditLog.entity_id.ilike(like),
            AuditLog.ip_address.ilike(like),
        ))
    filter_columns = {
        "timestamp": AuditLog.timestamp, "username": AuditLog.username,
        "action": AuditLog.action, "entity_type": AuditLog.entity_type,
        "entity_id": AuditLog.entity_id, "ip_address": AuditLog.ip_address,
    }
    for key, value in request.query_params.items():
        if not key.startswith(("exclude__", "include__")):
            continue
        keeping = key.startswith("include__")
        expression = filter_columns.get(key.removeprefix("include__" if keeping else "exclude__"))
        pick = include_clause if keeping else exclude_clause
        clause = pick(expression, excluded_values(value)) if expression is not None else None
        if clause is not None:
            q = q.where(clause)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    sort_columns = {
        "timestamp": AuditLog.timestamp, "username": AuditLog.username,
        "action": AuditLog.action, "entity_type": AuditLog.entity_type,
        "entity_id": AuditLog.entity_id, "ip_address": AuditLog.ip_address,
    }
    column = sort_columns.get(sort, AuditLog.timestamp)
    q = q.order_by(column.desc() if order == "desc" else column.asc(), AuditLog.id.desc())
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[AuditOut.model_validate(a) for a in items], total=total, page=page, page_size=page_size)


@router.get("/export")
def export_audit_logs(
    format: str = "json",
    username: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_audit_view),
):
    items = db.scalars(_query_logs(db, username, action, entity_type, None, date_from, date_to).order_by(AuditLog.timestamp)).all()
    rows = [AuditOut.model_validate(a).model_dump(mode="json") for a in items]
    log_action(db, user, "export.audit_logs", "audit_log", None, {"format": format, "count": len(rows)}, request)
    db.commit()
    return streaming_export_response(rows, EXPORT_COLUMNS, format, "audit_logs")
