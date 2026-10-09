from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class AuditRetention(Base):
    __tablename__ = "audit_retention"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_audit_retention_singleton"),
        CheckConstraint("days >= 0", name="ck_audit_retention_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
