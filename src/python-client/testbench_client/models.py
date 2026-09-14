"""Typed records returned by the client.

Plain dataclasses, not Pydantic: this library is imported by test frameworks
that already carry their own dependency stack, and one HTTP client is a cheaper
thing to ask for than a validation framework.

Every record keeps the response body it was built from on `.raw`, so a field
added to the API later is reachable without waiting for this library to catch
up. Unknown fields are never dropped, only unmapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

DEVICE_STATUSES = ("available", "checked_out", "inventory", "missing", "broken")
TEST_OUTCOMES = ("pass", "fail", "warn")
TEST_TAGS = ("adhoc", "acceptance", "end-to-end", "automated")

PASS = "pass"
FAIL = "fail"
WARN = "warn"


def parse_datetime(value: Any) -> datetime | None:
    """Parse an API timestamp into an aware UTC datetime.

    The API stores timestamps in UTC in columns without a time zone, so they
    come back with no offset: "2026-08-25T18:51:04.484432". Parsed as-is they
    are naive, and the first time one is compared against `datetime.now(utc)`
    it raises `TypeError: can't compare offset-naive and offset-aware`. Every
    timestamp is therefore stamped UTC here, once, so nothing downstream has to
    remember.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        # fromisoformat gained "Z" support in 3.11; normalise for older ones.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def parse_date(value: Any) -> date | None:
    """Parse an API date ("2026-08-27") into a `date`.

    Dates are dates, not midnights: `checked_out_at`, `checkout_due` and a
    test's `run_at` are DATE columns, and returning a datetime for them would
    invite exactly the timezone arithmetic they exist to avoid.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def to_api_date(value: date | str | None) -> str | None:
    """Serialise a date for the API as `YYYY-MM-DD`."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def to_api_datetime(value: datetime | str | None) -> str | None:
    """Serialise a datetime for the API.

    Aware values are converted to UTC and the offset dropped, which is not the
    same as sending the offset and letting Postgres deal with it: casting an
    aware timestamp into a column without a time zone *truncates* the offset
    rather than converting it, so 12:00+02:00 would be stored as 12:00 UTC —
    silently two hours wrong. Naive values are assumed to already be UTC.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


@dataclass
class Identity:
    """Who the configured API key authenticates as."""

    id: str = ""
    username: str = ""
    role: str = ""
    email: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Identity":
        return cls(
            id=data.get("id", ""),
            username=data.get("username", ""),
            role=data.get("role", ""),
            email=data.get("email"),
            raw=data,
        )

    @property
    def can_write(self) -> bool:
        """Whether this key may create tests or change device status."""
        return self.role in ("tester", "admin")


@dataclass
class DeviceType:
    """One inventory category, with its own page, columns and rules."""

    id: str = ""
    key: str = ""
    label: str = ""
    description: str | None = None
    icon: str | None = None
    enabled: bool = True
    position: int = 0
    device_count: int = 0
    configuration_source: str = "gui"
    field_count: int = 0
    required_field_keys: list[str] = field(default_factory=list)
    plugin_ids: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceType":
        return cls(
            id=data.get("id", ""),
            key=data.get("key", ""),
            label=data.get("label", ""),
            description=data.get("description"),
            icon=data.get("icon"),
            enabled=bool(data.get("enabled", True)),
            position=int(data.get("position", 0)),
            device_count=int(data.get("device_count", 0)),
            configuration_source=data.get("configuration_source", "gui"),
            field_count=int(data.get("field_count", 0)),
            required_field_keys=list(data.get("required_field_keys") or []),
            plugin_ids=list(data.get("plugin_ids") or []),
            raw=data,
        )


@dataclass
class Device:
    """A device in the fleet.

    `status` is the workflow state a person sets; `online` comes from network
    scanning and is unrelated — a checked-out device can be online, and an
    available one offline.
    """

    id: str = ""
    unique_id: str = ""
    device_type_id: str | None = None
    device_type_key: str | None = None
    device_type_label: str | None = None
    status: str = ""
    location: str | None = None
    make: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    architecture: str | None = None
    wan_ip: str | None = None
    lan_ip: str | None = None
    online: bool = False
    last_seen_online: datetime | None = None
    last_scanned_at: datetime | None = None
    misc_data: dict = field(default_factory=dict)
    checked_out_by: str | None = None
    checked_out_by_username: str | None = None
    checked_out_at: date | None = None
    # Why the device is out, and the day it is due back. Both are required to
    # check a device out, and the API clears both on check-in.
    checkout_purpose: str | None = None
    checkout_due: date | None = None
    # Every installation-defined value, by its stable field key. The named
    # attributes above are a convenience projection of the same document; a
    # field this library has never heard of is reachable here without waiting
    # for a release.
    data: dict = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Populated only by `devices.get()`, which returns the detail record.
    # An empty list here means "not loaded" as much as "none exist"; use
    # `tests.for_device()` if you need to be sure.
    tests: list["Test"] = field(default_factory=list, repr=False)
    verified_tests: list["Test"] = field(default_factory=list, repr=False)
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        return cls(
            id=data.get("id", ""),
            unique_id=data.get("unique_id", ""),
            device_type_id=data.get("device_type_id"),
            device_type_key=data.get("device_type_key"),
            device_type_label=data.get("device_type_label"),
            status=data.get("status", ""),
            location=data.get("location"),
            make=data.get("make"),
            model=data.get("model"),
            firmware_version=data.get("firmware_version"),
            hardware_version=data.get("hardware_version"),
            architecture=data.get("architecture"),
            wan_ip=data.get("wan_ip"),
            lan_ip=data.get("lan_ip"),
            # Named `online_status` in the API; `online` is what it means, and
            # what every other surface of this system calls it.
            online=bool(data.get("online_status", False)),
            last_seen_online=parse_datetime(data.get("last_seen_online")),
            last_scanned_at=parse_datetime(data.get("last_scanned_at")),
            misc_data=data.get("misc_data") or {},
            checked_out_by=data.get("checked_out_by"),
            checked_out_by_username=data.get("checked_out_by_username"),
            checked_out_at=parse_date(data.get("checked_out_at")),
            checkout_purpose=data.get("checkout_purpose"),
            checkout_due=parse_date(data.get("checkout_due")),
            data=data.get("data") or {},
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            tests=[Test.from_dict(t) for t in data.get("all_tests") or []],
            verified_tests=[Test.from_dict(t) for t in data.get("verified_tests") or []],
            raw=data,
        )

    def field(self, key: str, default: Any = None) -> Any:
        """One installation-defined value, by its stable field key.

        Keys are stable; labels are presentation and may be changed by an
        administrator at any time, so nothing here is addressed by label.
        """
        if key in self.data:
            return self.data[key]
        return getattr(self, key, default) if hasattr(self, key) else default

    @property
    def is_available(self) -> bool:
        return self.status == "available"

    @property
    def is_checked_out(self) -> bool:
        return self.status == "checked_out"

    @property
    def is_overdue(self) -> bool:
        """Past its return date and still out.

        Derived here the same way the API derives it, so a Device read from any
        endpoint can answer without a second call.
        """
        return (
            self.is_checked_out
            and self.checkout_due is not None
            and self.checkout_due < date.today()
        )

    @property
    def days_overdue(self) -> int:
        """How many days late, or 0 if it is not."""
        if not self.is_overdue:
            return 0
        return (date.today() - self.checkout_due).days

    def __str__(self) -> str:
        bits = [b for b in (self.make, self.model) if b]
        return f"{self.unique_id} ({' '.join(bits)})" if bits else self.unique_id


@dataclass
class Software:
    """A piece of software at one version.

    Rows sharing a name are one software; a bare name always addresses the
    CURRENT version, which is the highest numeric version in its name group.
    """

    id: str = ""
    name: str = ""
    version: str = ""
    version_count: int = 1
    is_latest: bool = True
    vendor_device_count: int = 0
    misc_data: dict = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Software":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            version=data.get("version") or "",
            version_count=data.get("version_count", 1),
            is_latest=bool(data.get("is_latest", True)),
            vendor_device_count=data.get("vendor_device_count", 0),
            misc_data=data.get("misc_data") or {},
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            raw=data,
        )

    def __str__(self) -> str:
        return f"{self.name} {self.version}".strip()


@dataclass
class Test:
    """One recorded run of a piece of software against a device.

    `software_version` is a snapshot taken when the run was recorded, not a
    live reference: the software moves on, the evidence stays attached to the
    build it was gathered on.
    """

    id: str = ""
    software_id: str = ""
    software_name: str | None = None
    software_version: str | None = None
    device_id: str = ""
    device_unique_id: str | None = None
    device_make: str | None = None
    device_model: str | None = None
    outcome: str = ""
    tag: str = ""
    misc_data: dict = field(default_factory=dict)
    notes: str | None = None
    run_at: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by_username: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> "Test":
        return cls(
            id=data.get("id", ""),
            software_id=data.get("software_id", ""),
            software_name=data.get("software_name"),
            software_version=data.get("software_version"),
            device_id=data.get("device_id", ""),
            device_unique_id=data.get("device_unique_id"),
            device_make=data.get("device_make"),
            device_model=data.get("device_model"),
            outcome=data.get("outcome", ""),
            tag=data.get("tag", ""),
            misc_data=data.get("misc_data") or {},
            notes=data.get("notes"),
            run_at=parse_date(data.get("run_at")),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            created_by_username=data.get("created_by_username"),
            raw=data,
        )

    @property
    def passed(self) -> bool:
        return self.outcome == PASS

    def __str__(self) -> str:
        return f"{self.software_name} on {self.device_unique_id}: {self.outcome}"
