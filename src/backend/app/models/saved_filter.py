import re

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid

FILTER_ENTITIES = (
    "devices", "software", "tests", "audit", "users",
    # The cross-software vendor claim catalogue. Unscoped, unlike the
    # per-software "vendor_devices:<id>" below: this page is one list of
    # every claim, so there is only ever one view of it to save.
    "vendor_device_catalog",
)

# Device inventory pages differ per device type — a router page and a phone
# page show different columns from the same table — so their saved views are
# scoped by type as "devices:<type key>". Without that, saving a router layout
# would overwrite the phone layout and the columns in it would not even exist.
DEVICE_SCOPED_ENTITY = re.compile(r"^devices:[a-z][a-z0-9-]{0,49}$")
SOFTWARE_DETAIL_SCOPED_ENTITY = re.compile(
    r"^(?:vendor_devices|tested_devices):"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_filter_entity(value: str) -> bool:
    return (
        value in FILTER_ENTITIES
        or bool(DEVICE_SCOPED_ENTITY.fullmatch(value))
        or bool(SOFTWARE_DETAIL_SCOPED_ENTITY.fullmatch(value))
    )

# A view is saved for the shape of screen it was built on. A desktop view names
# fourteen columns and a hundred rows a page; the same view on a phone is a wall
# of cards. Scoping them by platform lets one user keep a sensible default for
# each, and the default-view rule below becomes one per (user, entity, platform).
FILTER_PLATFORMS = ("desktop", "mobile")


class SavedFilter(TimestampMixin, Base):
    """A named, per-user AG Grid filter/sort/column profile."""

    __tablename__ = "saved_filters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Base entities, devices:<type key>, or vendor/tested devices:<software UUID>.
    entity: Mapped[str] = mapped_column(String(60), nullable=False)
    # Defaults to desktop so every view saved before platforms existed keeps
    # behaving exactly as it did.
    platform: Mapped[str] = mapped_column(String(10), nullable=False, default="desktop", server_default="desktop")
    filter_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    sort_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    column_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    page_size: Mapped[int] = mapped_column(default=100)
    quick_filter: Mapped[str] = mapped_column(String(255), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
