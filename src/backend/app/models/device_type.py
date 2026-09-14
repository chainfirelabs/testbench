from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid


class DeviceType(TimestampMixin, Base):
    """One inventory category: its own page, columns, rules and plugins.

    The type holds identity and presentation only. Which fields it shows and
    which plugins may act on it live in `device_field_assignments` and
    `device_type_plugins`, so a field can be defined once and assigned to many
    types without being copied into each of them.
    """

    __tablename__ = "device_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Immutable and used in URLs, imports, exports and plugin contracts.
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which side owns this row: the admin GUI, or a versioned YAML document.
    # Displayed in the UI so nobody edits something a reconciler will revert.
    configuration_source: Mapped[str] = mapped_column(String(10), nullable=False, default="gui")
    source_revision: Mapped[str | None] = mapped_column(String(100))

    devices: Mapped[list["Device"]] = relationship("Device", back_populates="device_type")


from .device import Device  # noqa: E402,F401
