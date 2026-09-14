from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class PluginArtifact(TimestampMixin, Base):
    """Versioned plugin-owned data attached to an inventory entity."""

    __tablename__ = "plugin_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "plugin_id", "entity_type", "entity_id", "artifact_type", "version",
            name="uq_plugin_artifact_version",
        ),
        Index("ix_plugin_artifact_lookup", "plugin_id", "entity_type", "entity_id", "artifact_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class PluginResultReceipt(TimestampMixin, Base):
    """Idempotency record for a plugin callback."""

    __tablename__ = "plugin_result_receipts"
    __table_args__ = (UniqueConstraint("plugin_id", "run_id", name="uq_plugin_result_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
