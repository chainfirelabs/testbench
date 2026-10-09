"""CronJob entry point for recovery of manual audit cleanup jobs."""

from .services.audit_cleanup_jobs import process_pending_cleanup_jobs


if __name__ == "__main__":
    process_pending_cleanup_jobs()
