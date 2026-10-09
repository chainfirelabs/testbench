from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class PluginRun(TimestampMixin, Base):
    __tablename__ = "plugin_runs"
    __table_args__ = (
        UniqueConstraint("plugin_id", "run_id", name="uq_plugin_run_identity"),
        Index("ix_plugin_runs_state", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
