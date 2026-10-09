"""Batched audit-log deletion shared by manual requests and the daily job."""

from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import utcnow
from ..models import AuditLog, AuditRetention, User
from .audit import log_action
from .login_guard import WINDOW

BATCH_SIZE = 500


def retention_days(db: Session) -> int:
    row = db.get(AuditRetention, 1)
    return row.days if row else 0


def safe_cutoff(cutoff: datetime) -> datetime:
    """Never erase rows still used by login throttling."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    if cutoff > utcnow() - WINDOW:
        raise ValueError("The cutoff must be at least 15 minutes in the past")
    return cutoff.astimezone(timezone.utc)


def eligible_filter(cutoff: datetime, delete_device_changelogs: bool | None = None):
    """Protect the same per-device rows that power the device changelog API."""
    clauses = [AuditLog.timestamp < cutoff]
    if delete_device_changelogs is None:
        delete_device_changelogs = settings.audit_delete_device_changelogs
    if not delete_device_changelogs:
        clauses.append(or_(
            AuditLog.entity_type != "device",
            AuditLog.entity_type.is_(None),
            AuditLog.entity_id.is_(None),
        ))
    return clauses


def eligible_count(db: Session, cutoff: datetime, delete_device_changelogs: bool | None = None) -> int:
    return db.scalar(select(func.count()).select_from(AuditLog).where(
        *eligible_filter(cutoff, delete_device_changelogs))) or 0


def delete_before(
    db: Session, cutoff: datetime, *,
    user: User | None = None, request: Request | None = None,
    automatic: bool = False,
) -> int:
    cutoff = safe_cutoff(cutoff)
    deleted = 0
    while True:
        ids = db.scalars(
            select(AuditLog.id).where(*eligible_filter(cutoff))
            .order_by(AuditLog.timestamp, AuditLog.id).limit(BATCH_SIZE)
        ).all()
        if not ids:
            break
        removed = db.scalars(delete(AuditLog).where(AuditLog.id.in_(ids)).returning(AuditLog.id)).all()
        if removed:
            log_action(
                db, user, "audit_logs.cleanup.auto" if automatic else "audit_logs.cleanup.manual",
                "audit_log", None,
                {"before": cutoff.isoformat(), "deleted": len(removed)}, request,
            )
        db.commit()
        deleted += len(removed)
        if len(ids) < BATCH_SIZE:
            break
    return deleted


def run_scheduled_cleanup(db: Session) -> int:
    days = retention_days(db)
    if days == 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    return delete_before(db, cutoff, automatic=True)
