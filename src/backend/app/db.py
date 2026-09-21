from datetime import date, datetime, timezone
from uuid import UUID

import uuid6
from sqlalchemy import create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB

from .config import settings

engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def new_uuid() -> str:
    return str(uuid6.uuid7())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Read a stored timestamp as an aware UTC one.

    The timestamp columns are TIMESTAMP WITHOUT TIME ZONE, so a value loaded
    from the database comes back naive while `utcnow()` is aware, and comparing
    the two raises TypeError. Anything that compares a loaded timestamp against
    now goes through here first.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def jsonable(value):
    """A value as a JSONB column can store it.

    Dates and times are the ones that reach here. They are not a formatting
    problem: `json.dumps` has no encoder for them, so one landing in a JSONB
    column is a 500 on the write that carried it, raised from inside the
    flush where nothing can say which field was at fault.

    Everything else the models hold is already a JSON primitive and passes
    through untouched.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(default=None, onupdate=utcnow)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Re-export for models
__all__ = ["Base", "JSONB", "jsonable", "new_uuid", "utcnow", "as_utc", "get_db", "UUID"]
