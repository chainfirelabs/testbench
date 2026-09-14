from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AuditLog, User
from ..schemas import AuditOut, Page
from ..services.audit import log_action
from ..services.io import export_response
from .deps import require_admin

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
    username: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    q = _query_logs(db, username, action, entity_type, entity_id, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
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
    user: User = Depends(require_admin),
):
    items = db.scalars(_query_logs(db, username, action, entity_type, None, date_from, date_to).order_by(AuditLog.timestamp)).all()
    rows = [AuditOut.model_validate(a).model_dump(mode="json") for a in items]
    log_action(db, user, "export.audit_logs", "audit_log", None, {"format": format, "count": len(rows)}, request)
    db.commit()
    return export_response(rows, EXPORT_COLUMNS, format, "audit_logs")
