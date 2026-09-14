from sqlalchemy import String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from ..db import Base, TimestampMixin, new_uuid

SOFTWARE_SYSTEM_KEYS = {"name", "version"}
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


class Software(TimestampMixin, Base):
    __tablename__ = "software"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    _data: Mapped[dict] = mapped_column("data", MutableDict.as_mutable(JSONB), default=dict)
    name = _json_text("name")
    version = _json_text("version", "")

    @property
    def misc_data(self) -> dict:
        document = self._data or {}
        marked = set(document.get(EXTRA_FIELDS_KEY, []))
        visible = {key: value for key, value in document.items() if key not in {EXTRA_FIELDS_KEY, EXTRA_VALUES_KEY} and (key not in SOFTWARE_SYSTEM_KEYS or key in marked)}
        visible.update(document.get(EXTRA_VALUES_KEY, {}))
        return visible
    @misc_data.setter
    def misc_data(self, value: dict | None):
        incoming = dict(value or {})
        marked = set((self._data or {}).get(EXTRA_FIELDS_KEY, [])) | set(incoming.pop(EXTRA_FIELDS_KEY, []))
        extra_values = dict((self._data or {}).get(EXTRA_VALUES_KEY, {}))
        extra_values.update(incoming.pop(EXTRA_VALUES_KEY, {}))
        for key in marked & SOFTWARE_SYSTEM_KEYS:
            if key in incoming:
                extra_values[key] = incoming.pop(key)
        core = {key: item for key, item in (self._data or {}).items() if key in SOFTWARE_SYSTEM_KEYS}
        self._data = {**core, **incoming}
        if marked: self._data[EXTRA_FIELDS_KEY] = sorted(marked)
        if extra_values: self._data[EXTRA_VALUES_KEY] = extra_values

    vendor_devices: Mapped[list["VendorDevice"]] = relationship("VendorDevice", back_populates="software", cascade="all, delete-orphan")


from .vendor_device import VendorDevice  # noqa: E402

Software.vendor_device_count = column_property(
    select(func.count(VendorDevice.id)).where(VendorDevice.software_id == Software.id).correlate_except(VendorDevice).scalar_subquery()
)
