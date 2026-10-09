from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class AuditCleanupJob(TimestampMixin, Base):
    __tablename__ = "audit_cleanup_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    before: Mapped[datetime] = mapped_column(nullable=False)
    delete_device_changelogs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_by: Mapped[str] = mapped_column(String(150), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
