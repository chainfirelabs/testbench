from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid

# What the vendor claims about a device, not what we have measured.
VENDOR_SUPPORT_STATUSES = ("supported", "partial", "unsupported", "planned")

VENDOR_SUPPORT_LABELS = {
    "supported": "Supported",
    "partial": "Partial",
    "unsupported": "Unsupported",
    "planned": "Planned",
}

# The fields that identify a vendor device. Two imported rows agreeing on all
# of these are the same claim, so they collapse onto one row.
IDENTITY_FIELDS = (
    "make",
    "model",
    "firmware_version",
    "hardware_version",
    "architecture",
)


def build_match_key(values: dict) -> str:
    """Normalised dedupe key over the identity fields.

    A plain unique constraint over the columns themselves would not work:
    Postgres treats NULLs as distinct, so `(Cisco, ISR, NULL)` could be
    inserted any number of times. Folding the fields into one non-null,
    case-insensitive string makes repeated imports idempotent.
    """
    return "|".join((values.get(f) or "").strip().lower() for f in IDENTITY_FIELDS)


class VendorDevice(TimestampMixin, Base):
    """A device the vendor says their software works against.

    These are deliberately *not* rows in `devices`: they describe hardware the
    vendor claims support for, imported from a compatibility matrix or
    datasheet, whether or not we own one. Inventory lives in `devices`; what the
    software has actually been run against lives in `tests`.
    """

    __tablename__ = "vendor_devices"
    __table_args__ = (
        UniqueConstraint("software_id", "match_key", name="uq_vendor_devices_software_match"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    software_id: Mapped[str] = mapped_column(
        ForeignKey("software.id", ondelete="CASCADE"), nullable=False, index=True
    )

    make: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    firmware_version: Mapped[str | None] = mapped_column(String(100))
    hardware_version: Mapped[str | None] = mapped_column(String(100))
    architecture: Mapped[str | None] = mapped_column(String(100))
    support_status: Mapped[str] = mapped_column(String(20), default="supported", index=True)
    # Where the claim came from: a datasheet URL, a compatibility matrix, a name.
    source: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    misc_data: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Maintained by the API on every write; see build_match_key.
    match_key: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    software: Mapped["Software"] = relationship("Software", back_populates="vendor_devices")


from .software import Software  # noqa: E402,F401  (for type checkers)
