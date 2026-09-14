from datetime import date, datetime
from typing import Container

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid

DEVICE_STATUSES = ("available", "checked_out", "inventory", "missing", "broken")
DEVICE_STATUS_LABELS = {"available": "Available", "checked_out": "Checked Out", "inventory": "Inventory", "missing": "Missing", "broken": "Broken"}
DEVICE_ARCHITECTURES = ("x86_64", "x86", "mipsbe", "mipsel", "arm", "arm64", "aarch64", "ppc", "tilegx", "lexra_mips")
# Keys the model exposes as attributes on top of the JSON document. They are
# still ordinary catalog fields — this list only says which ones application
# code (checkout, scanning, hooks) reaches for by name.
DEVICE_SYSTEM_KEYS = {
    "status", "location", "make", "model", "firmware_version",
    "hardware_version", "architecture", "wan_ip", "lan_ip", "online_status",
    "last_seen_online", "last_scanned_at", "checked_out_at", "checkout_due",
    "checkout_purpose",
}


def _value(document: dict | None, key: str, default=None):
    value = (document or {}).get(key, default)
    return default if value is None else value


def _text_field(key: str, default=None):
    @hybrid_property
    def field(self):
        return _value(self._data, key, default)

    @field.setter
    def field(self, value):
        document = dict(self._data or {})
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
        self._data = document

    @field.expression
    def field(cls):
        return cls._data[key].astext

    return field


def _typed_field(key: str, sql_type, converter, default=None):
    @hybrid_property
    def field(self):
        value = _value(self._data, key, default)
        if value is None or isinstance(value, converter):
            return value
        return converter.fromisoformat(value) if converter in (date, datetime) else converter(value)

    @field.setter
    def field(self, value):
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        document = dict(self._data or {})
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
        self._data = document

    @field.expression
    def field(cls):
        return cast(cls._data[key].astext, sql_type)

    return field


class Device(TimestampMixin, Base):
    """Inventory identity plus one installation-defined JSON document."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # A real column, not a key in `data`: this is durable identity, used by
    # URLs, imports, test results, API clients and integrations, and it is what
    # a foreign reference to a device means. Everything else about a device is
    # installation-defined and lives in the document.
    unique_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    _data: Mapped[dict] = mapped_column("data", MutableDict.as_mutable(JSONB), default=dict)
    checked_out_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    device_type_id: Mapped[str | None] = mapped_column(ForeignKey("device_types.id"), index=True)

    status = _text_field("status", "available")
    location = _text_field("location")
    make = _text_field("make")
    model = _text_field("model")
    firmware_version = _text_field("firmware_version")
    hardware_version = _text_field("hardware_version")
    architecture = _text_field("architecture")
    wan_ip = _text_field("wan_ip")
    lan_ip = _text_field("lan_ip")
    online_status = _typed_field("online_status", Boolean, bool, False)
    last_seen_online = _typed_field("last_seen_online", DateTime, datetime)
    last_scanned_at = _typed_field("last_scanned_at", DateTime, datetime)
    checked_out_at = _typed_field("checked_out_at", Date, date)
    checkout_due = _typed_field("checkout_due", Date, date)
    checkout_purpose = _text_field("checkout_purpose")

    @property
    def data(self) -> dict:
        """The whole installation-defined document, as a plain copy."""
        return dict(self._data or {})

    def merge_data(self, values: dict) -> None:
        """Apply a validated partial document, deleting the keys set to None.

        Merging rather than replacing is what makes a partial write partial: a
        field the caller never mentioned keeps its value, including one that is
        not on the current layout at all.
        """
        document = dict(self._data or {})
        for key, value in (values or {}).items():
            if value is None:
                document.pop(key, None)
            else:
                document[key] = value
        self._data = document

    def misc_data_beyond(self, known: Container[str]) -> dict:
        """The document minus the keys a layout already accounts for.

        What `misc_data` is for: the values a device carries that no field on
        its page explains — a key from an import that was never defined, or one
        whose field has since been taken off the type. Everything else has a
        column of its own and does not belong in a spillover bucket as well.

        The caller supplies `known` because only it has the device type and the
        session needed to resolve a layout; see `layout_keys` in the schema
        service, which is where that resolution lives.
        """
        return {key: value for key, value in (self._data or {}).items() if key not in known}

    @property
    def misc_data(self) -> dict:
        """The legacy projection: the document minus the flattened attributes.

        Kept for callers with no session to resolve a layout with — a detached
        instance, a unit test constructing one by hand. It predates
        installation-defined fields and cannot see them, so every read path
        that serves a client goes through `misc_data_beyond` instead. Reaching
        for this one on a request path reports `username` and every field an
        installation invented as unexplained data.
        """
        return self.misc_data_beyond(DEVICE_SYSTEM_KEYS)

    @misc_data.setter
    def misc_data(self, value: dict | None):
        """Merge extra values into the document, keeping what is already there.

        It used to keep only `DEVICE_SYSTEM_KEYS` from the existing document
        and drop the rest, which on a stored device silently discarded
        `username`, `serial_number` and every field an installation had
        defined — the same frozen list that made the read projection wrong.
        Setting some values is not a statement about the ones you did not
        mention, and the API's own write path has always merged rather than
        replaced.
        """
        document = dict(self._data or {})
        document.update(value or {})
        self._data = document

    checked_out_user: Mapped["User | None"] = relationship("User", foreign_keys=[checked_out_by], lazy="selectin")
    tests: Mapped[list["Test"]] = relationship("Test", back_populates="device", cascade="all, delete-orphan")
    device_type: Mapped["DeviceType | None"] = relationship("DeviceType", back_populates="devices", lazy="selectin")

    @property
    def device_type_key(self) -> str | None:
        return self.device_type.key if self.device_type else None

    @property
    def device_type_label(self) -> str | None:
        return self.device_type.label if self.device_type else None

    @property
    def checked_out_by_username(self) -> str | None:
        return self.checked_out_user.username if self.checked_out_user else None


from .user import User  # noqa: E402
from .test import Test  # noqa: E402,F401
from .device_type import DeviceType  # noqa: E402,F401
