from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class VendorDeviceFieldOverride(TimestampMixin, Base):
    """Per-software-version presentation and validation for a catalog field."""

    __tablename__ = "vendor_device_field_overrides"
    __table_args__ = (
        UniqueConstraint("software_id", "field_id", name="uq_vendor_device_field_override"),
        Index("ix_vendor_device_field_override_software", "software_id"),
        Index("ix_vendor_device_field_override_field", "field_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    software_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("software.id", ondelete="CASCADE"), nullable=False,
    )
    field_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entity_fields.id", ondelete="CASCADE"), nullable=False,
    )
    visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
