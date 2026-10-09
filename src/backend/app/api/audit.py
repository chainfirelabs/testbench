from typing import Annotated

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuditCleanupJob, AuditLog, AuditRetention, User
from ..schemas import AuditOut, Page
from ..services.audit import log_action
from ..services.audit_cleanup_jobs import process_pending_cleanup_jobs
from ..services.audit_retention import eligible_count, retention_days, safe_cutoff
from ..services.io import database_export_rows, streaming_export_response
from ..services.list_filters import checklist_query, exclude_clause, excluded_values, include_clause
from .deps import require_audit_view, require_settings_manage

router = APIRouter(prefix="/audit_logs", tags=["audit_logs"])

EXPORT_COLUMNS = ["id", "timestamp", "username", "action", "entity_type", "entity_id", "detail", "ip_address"]


class RetentionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(ge=0, le=36500)


class CleanupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before: datetime


def _cleanup_cutoff(before: datetime) -> datetime:
    try:
        return safe_cutoff(before)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/retention")
def get_retention(
    db: Session = Depends(get_db),
    _viewer: User = Depends(require_audit_view),
):
    return {"days": retention_days(db),
            "delete_device_changelogs": settings.audit_delete_device_changelogs}


@router.put("/retention")
def set_retention(
    body: RetentionIn, request: Request, db: Session = Depends(get_db),
    user: User = Depends(require_settings_manage),
    _viewer: User = Depends(require_audit_view),
):
    row = db.get(AuditRetention, 1)
    if row is None:
        row = AuditRetention(id=1, days=0)
        db.add(row)
    old_days = row.days
    row.days = body.days
    log_action(db, user, "audit_logs.retention.update", "audit_log", None,
               {"old_days": old_days, "new_days": body.days}, request)
    db.commit()
    return {"days": row.days,
            "delete_device_changelogs": settings.audit_delete_device_changelogs}


@router.post("/cleanup/preview")
def preview_cleanup(
    body: CleanupIn, db: Session = Depends(get_db),
    _manager: User = Depends(require_settings_manage),
    _viewer: User = Depends(require_audit_view),
):
    cutoff = _cleanup_cutoff(body.before)
    eligible = eligible_count(db, cutoff)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.timestamp < cutoff)) or 0
    return {"eligible": eligible, "protected": total - eligible,
            "before": cutoff.isoformat(),
            "delete_device_changelogs": settings.audit_delete_device_changelogs}


@router.post("/cleanup", status_code=202)
def cleanup_logs(
    body: CleanupIn, request: Request, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_settings_manage),
    _viewer: User = Depends(require_audit_view),
):
    cutoff = _cleanup_cutoff(body.before)
    job = AuditCleanupJob(
        before=cutoff, delete_device_changelogs=settings.audit_delete_device_changelogs,
        requested_by=user.username, total=eligible_count(db, cutoff),
    )
    db.add(job)
    db.flush()
    log_action(db, user, "audit_logs.cleanup.request", "audit_log", None,
               {"job_id": job.id, "before": cutoff.isoformat(), "total": job.total}, request)
    db.commit()
    background_tasks.add_task(process_pending_cleanup_jobs)
    return _cleanup_job_out(job)


def _cleanup_job_out(job: AuditCleanupJob) -> dict:
    return {
        "id": job.id, "before": job.before.isoformat(), "state": job.state,
        "total": job.total, "deleted": job.deleted, "error": job.error,
        "requested_by": job.requested_by,
        "delete_device_changelogs": job.delete_device_changelogs,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.get("/cleanup/jobs")
def list_cleanup_jobs(
    db: Session = Depends(get_db),
    _manager: User = Depends(require_settings_manage),
    _viewer: User = Depends(require_audit_view),
):
    jobs = db.scalars(select(AuditCleanupJob).order_by(AuditCleanupJob.created_at.desc()).limit(20)).all()
    return [_cleanup_job_out(job) for job in jobs]


@router.get("/cleanup/jobs/{job_id}")
def get_cleanup_job(
    job_id: str, db: Session = Depends(get_db),
    _manager: User = Depends(require_settings_manage),
    _viewer: User = Depends(require_audit_view),
):
    job = db.get(AuditCleanupJob, job_id)
    if job is None:
        raise HTTPException(404, "Cleanup job not found")
    return _cleanup_job_out(job)


@router.post("/cleanup/jobs/{job_id}/retry")
def retry_cleanup_job(
    job_id: str, request: Request, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_settings_manage),
    _viewer: User = Depends(require_audit_view),
):
    job = db.get(AuditCleanupJob, job_id)
    if job is None:
        raise HTTPException(404, "Cleanup job not found")
    if job.state != "failed":
        raise HTTPException(409, "Only failed cleanup jobs can be retried")
    job.state = "queued"
    job.error = None
    log_action(db, user, "audit_logs.cleanup.retry", "audit_log", None,
               {"job_id": job.id}, request)
    db.commit()
    background_tasks.add_task(process_pending_cleanup_jobs)
    return _cleanup_job_out(job)


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
@router.post("/query", response_model=Page[AuditOut])
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
    filters_body: Annotated[dict[str, str] | None, Body()] = None,
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
    for key, value in checklist_query(request, filters_body).items():
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
    q = _query_logs(db, username, action, entity_type, None, date_from, date_to)
    count = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    log_action(db, user, "export.audit_logs", "audit_log", None, {"format": format, "count": count}, request)
    db.commit()
    def rows(export_db: Session):
        query = _query_logs(export_db, username, action, entity_type, None, date_from, date_to)
        for item in export_db.scalars(query.order_by(AuditLog.timestamp, AuditLog.id).execution_options(yield_per=500)):
            yield AuditOut.model_validate(item).model_dump(mode="json")
    return streaming_export_response(database_export_rows(rows), EXPORT_COLUMNS, format, "audit_logs")
