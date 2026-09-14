from datetime import datetime

from sqlalchemy import BigInteger, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow


class AuditLog(Base):
    """Append-only record of every action. No UPDATE/DELETE is ever exposed."""

    __tablename__ = "audit_logs"

    # Login throttling counts failures by action within a time window; see
    # migration 0014 and services/login_guard.
    __table_args__ = (Index("ix_audit_logs_action_timestamp", "action", "timestamp"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[str | None]
    username: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), index=True)
    user_agent: Mapped[str | None] = mapped_column(Text)
