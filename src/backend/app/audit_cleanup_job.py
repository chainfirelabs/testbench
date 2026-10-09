"""Daily Helm CronJob entry point. Retention is read from PostgreSQL each run."""

import logging

from .db import SessionLocal
from .services.audit_retention import run_scheduled_cleanup


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        deleted = run_scheduled_cleanup(db)
    logging.info("Audit cleanup deleted %d rows", deleted)


if __name__ == "__main__":
    main()
