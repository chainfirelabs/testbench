from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ReplicationTombstone(Base):
    """A durable deletion marker, independent of audit retention."""

    __tablename__ = "replication_tombstones"

    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    identity: Mapped[str] = mapped_column(String(700), primary_key=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
