from datetime import date

from sqlalchemy import Date, ForeignKey, String, cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid

TEST_OUTCOMES = ("pass", "fail", "warn")
TEST_TAGS = ("adhoc", "acceptance", "end-to-end", "automated")
TEST_SYSTEM_KEYS = {"software_version", "outcome", "tag", "notes", "run_at"}
EXTRA_FIELDS_KEY = "__extra_fields"
EXTRA_VALUES_KEY = "__extra_values"


def _json_text(key: str, default=None):
    @hybrid_property
    def field(self):
        value = (self._data or {}).get(key, default)
        return default if value is None else value
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


class Test(TimestampMixin, Base):
    __tablename__ = "tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    software_id: Mapped[str] = mapped_column(ForeignKey("software.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    _data: Mapped[dict] = mapped_column("data", MutableDict.as_mutable(JSONB), default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))

    software_version = _json_text("software_version")
    outcome = _json_text("outcome")
    tag = _json_text("tag", "adhoc")
    notes = _json_text("notes")

    @hybrid_property
    def run_at(self):
        value = (self._data or {}).get("run_at")
        return date.fromisoformat(value) if isinstance(value, str) else value
    @run_at.setter
    def run_at(self, value):
        document = dict(self._data or {})
        if value is None:
            document.pop("run_at", None)
        else:
            document["run_at"] = value.isoformat() if isinstance(value, date) else value
        self._data = document
    @run_at.expression
    def run_at(cls):
        return cast(cls._data["run_at"].astext, Date)

    @property
    def misc_data(self) -> dict:
        document = self._data or {}
        marked = set(document.get(EXTRA_FIELDS_KEY, []))
        visible = {key: value for key, value in document.items() if key not in {EXTRA_FIELDS_KEY, EXTRA_VALUES_KEY} and (key not in TEST_SYSTEM_KEYS or key in marked)}
        visible.update(document.get(EXTRA_VALUES_KEY, {}))
        return visible
    @misc_data.setter
    def misc_data(self, value: dict | None):
        incoming = dict(value or {})
        marked = set((self._data or {}).get(EXTRA_FIELDS_KEY, [])) | set(incoming.pop(EXTRA_FIELDS_KEY, []))
        extra_values = dict((self._data or {}).get(EXTRA_VALUES_KEY, {}))
        extra_values.update(incoming.pop(EXTRA_VALUES_KEY, {}))
        for key in marked & TEST_SYSTEM_KEYS:
            if key in incoming:
                extra_values[key] = incoming.pop(key)
        core = {key: item for key, item in (self._data or {}).items() if key in TEST_SYSTEM_KEYS}
        self._data = {**core, **incoming}
        if marked: self._data[EXTRA_FIELDS_KEY] = sorted(marked)
        if extra_values: self._data[EXTRA_VALUES_KEY] = extra_values

    software: Mapped["Software"] = relationship("Software", lazy="selectin")
    device: Mapped["Device"] = relationship("Device", back_populates="tests", lazy="selectin")
    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by], lazy="selectin")

    @property
    def software_name(self): return self.software.name if self.software else None
    @property
    def device_unique_id(self): return self.device.unique_id if self.device else None
    @property
    def device_make(self): return self.device.make if self.device else None
    @property
    def device_model(self): return self.device.model if self.device else None
    @property
    def created_by_username(self): return self.creator.username if self.creator else None


from .device import Device  # noqa: E402
from .software import Software  # noqa: E402
from .user import User  # noqa: E402
