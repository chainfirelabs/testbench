from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class EntityField(TimestampMixin, Base):
    """One logical field frozen into an installation's entity schema."""

    __tablename__ = "entity_fields"
    __table_args__ = (
        UniqueConstraint("entity", "key", name="uq_entity_fields_entity_key"),
        Index("ix_entity_fields_entity_position", "entity", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    list_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    writable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    storage: Mapped[str] = mapped_column(String(20), nullable=False, default="data")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(String(50))
    indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unique_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    configuration_source: Mapped[str] = mapped_column(String(10), nullable=False, default="system")
    source_revision: Mapped[str | None] = mapped_column(String(100))
