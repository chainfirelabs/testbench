from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid


class SoftwareComponent(TimestampMixin, Base):
    """A named component scoped to one software version, not standalone software."""

    __tablename__ = "software_components"
    __table_args__ = (
        UniqueConstraint("software_id", "name", "version", name="uq_software_component_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    software_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("software.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    software: Mapped["Software"] = relationship("Software")


from .software import Software  # noqa: E402,F401
