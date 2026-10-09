"""Durable manual cleanup worker. A minute CronJob resumes work after restarts."""

import logging

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..db import engine
from ..models import AuditCleanupJob, AuditLog
from .audit import log_action
from .audit_retention import BATCH_SIZE, eligible_filter

logger = logging.getLogger(__name__)
LOCK_ID = 746441029  # Session-level PostgreSQL lock, shared by API and CronJob workers.


def _run_job(db: Session, job: AuditCleanupJob) -> None:
    job.state = "running"
    job.error = None
    db.commit()
    while True:
        ids = db.scalars(
            select(AuditLog.id)
            .where(*eligible_filter(job.before, job.delete_device_changelogs))
            .order_by(AuditLog.timestamp, AuditLog.id)
            .limit(BATCH_SIZE)
        ).all()
        if not ids:
            job.state = "completed"
            db.commit()
            return
        removed = db.scalars(delete(AuditLog).where(AuditLog.id.in_(ids)).returning(AuditLog.id)).all()
        if removed:
            job.deleted += len(removed)
            log_action(db, None, "audit_logs.cleanup.manual", "audit_log", None, {
                "job_id": job.id, "requested_by": job.requested_by,
                "before": job.before.isoformat(), "deleted": len(removed),
            })
        db.commit()


def process_pending_cleanup_jobs() -> None:
    """Process queued/running jobs with one database-backed worker at a time."""
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID})
        connection.commit()
        if not acquired:
            return
        try:
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
                while True:
                    job = db.scalar(
                        select(AuditCleanupJob)
                        .where(AuditCleanupJob.state.in_(("queued", "running")))
                        .order_by(AuditCleanupJob.created_at, AuditCleanupJob.id)
                        .limit(1)
                    )
                    if job is None:
                        return
                    try:
                        _run_job(db, job)
                    except Exception as exc:  # noqa: BLE001 — report durable failure, then process next job.
                        logger.exception("Audit cleanup job %s failed", job.id)
                        db.rollback()
                        job = db.get(AuditCleanupJob, job.id)
                        if job is not None:
                            job.state = "failed"
                            job.error = str(exc)[:1000]
                            db.commit()
        finally:
            connection.scalar(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})
            connection.commit()
