from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


class VendorDeviceComponentSupport(Base):
    """A component version's support claim for one vendor device."""

    __tablename__ = "vendor_device_component_support"

    vendor_device_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vendor_devices.id", ondelete="CASCADE"), primary_key=True,
    )
    component_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("software_components.id", ondelete="CASCADE"), primary_key=True,
    )
    support_status: Mapped[str] = mapped_column(String(20), nullable=False)

    component: Mapped["SoftwareComponent"] = relationship("SoftwareComponent", lazy="joined")

    @property
    def component_name(self) -> str:
        return self.component.name

    @property
    def component_version(self) -> str:
        return self.component.version


from .software_component import SoftwareComponent  # noqa: E402,F401
