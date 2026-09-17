from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models.user import ROLE_RANK
from .models.vendor_device import VENDOR_SUPPORT_STATUSES

T = TypeVar("T")


# Long enough that a password is not trivially guessable, short enough that it
# does not read as a policy nobody can satisfy. The ceiling is a sanity bound,
# not a security one: bcrypt hashes at most 72 bytes and `core.security` slices
# to that, so characters past it never reach the hash either way.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def _validate_role(v: str) -> str:
    if v not in ROLE_RANK:
        raise ValueError(f"role must be one of {sorted(ROLE_RANK)}")
    return v


def _validate_username(v: str) -> str:
    """Trim, then insist something is left.

    A username with a leading space would be a different row from the one the
    admin thinks they made, and login does not trim what is typed into it.
    """
    v = str(v).strip()
    if not v:
        raise ValueError("username must not be blank")
    return v


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------- Devices ----------

class DeviceBase(BaseModel):
    """The structural envelope every device carries.

    Identity, type, ownership and timestamps are structural and stay here. The
    inventory attributes are installation-defined and live in `data`; the named
    fields below are a flattened projection of that same document, kept so
    existing clients, exports and grid code keep working while everything moves
    onto the envelope.
    """

    unique_id: str
    device_type_id: str | None = None
    status: str = "available"
    location: str | None = None
    make: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    architecture: str | None = None
    wan_ip: str | None = None
    lan_ip: str | None = None
    online_status: bool = False
    last_seen_online: datetime | None = None
    last_scanned_at: datetime | None = None
    # Why the device is out, and the day it is due back. Both are written by
    # hand and both are required to check a device out; both are cleared by the
    # API when it is checked back in, alongside checked_out_by/checked_out_at.
    checkout_purpose: str | None = None
    checkout_due: date | None = None
    misc_data: dict = Field(default_factory=dict)
    # The whole installation-defined document. Values here win over the
    # flattened compatibility fields above.
    data: dict = Field(default_factory=dict)


class DeviceCreate(DeviceBase):
    # Unknown keys are the point: an installation defines its own fields, and a
    # client that sends `imei` flattened must not be rejected by a model that
    # was written before that field existed. Everything lands in the document
    # and is checked against the published schema there.
    model_config = ConfigDict(extra="allow")

    # Imports and API clients address a type by its stable key; the UI sends
    # the id. Both are accepted, and both resolve to the same row.
    device_type: str | None = None


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    device_type: str | None = None
    data: dict | None = None
    unique_id: str | None = None
    device_type_id: str | None = None
    status: str | None = None
    location: str | None = None
    make: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    architecture: str | None = None
    wan_ip: str | None = None
    lan_ip: str | None = None
    online_status: bool | None = None
    last_seen_online: datetime | None = None
    checkout_purpose: str | None = None
    checkout_due: date | None = None
    misc_data: dict | None = None


class DeviceOut(DeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_type_key: str | None = None
    device_type_label: str | None = None
    checked_out_by: str | None = None
    checked_out_by_username: str | None = None
    checked_out_at: date | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DeviceOverdueOut(DeviceOut):
    """A device past its return date, with how far past.

    `days_overdue` is derived at read time from the same `today()` the sweep
    uses, so the number in the list and the number in the notification agree.
    """

    days_overdue: int


class DeviceDetail(DeviceOut):
    verified_tests: list["TestOut"] = []
    all_tests: list["TestOut"] = []


class DeviceTypeBase(BaseModel):
    key: str
    label: str
    description: str | None = None
    enabled: bool = True
    position: int = 0


class DeviceTypeCreate(DeviceTypeBase):
    pass


class DeviceTypeUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    enabled: bool | None = None
    position: int | None = None


class DeviceTypeOut(DeviceTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    device_count: int = 0
    # Which side owns the row. The GUI marks a YAML-owned type read-only rather
    # than letting somebody edit something a reconciler will revert.
    configuration_source: str = "gui"
    field_count: int = 0
    required_field_keys: list[str] = Field(default_factory=list)
    plugin_ids: list[str] = Field(default_factory=list)


# ---------- Software ----------

class SoftwareBundleComponentIn(BaseModel):
    id: str | None = None
    name: str | None = None
    version: str | None = None
    required: bool = True
    position: int = 0


class SoftwareBundleComponentOut(BaseModel):
    id: str
    name: str
    version: str = ""
    required: bool = True
    position: int = 0


class SoftwareBase(BaseModel):
    name: str
    # Never None: the (name, version) unique constraint cannot see NULLs.
    # An unversioned software carries an empty string.
    version: str = ""
    misc_data: dict = Field(default_factory=dict)

    @field_validator("version", mode="before")
    def _blank_version(cls, v):
        return "" if v is None else v


class SoftwareCreate(SoftwareBase):
    bundle_components: list[SoftwareBundleComponentIn] | None = None


class SoftwareUpdate(BaseModel):
    @field_validator("version", mode="before")
    def _blank_version(cls, v):
        return "" if v is None else v

    # A rename moves every version of the software: rows sharing a name are one
    # software, and renaming a single row would split it in two.
    name: str | None = None
    version: str | None = None
    misc_data: dict | None = None
    bundle_components: list[SoftwareBundleComponentIn] | None = None


class SoftwareOut(SoftwareBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    vendor_device_count: int = 0
    # How many versions share this row's name, and whether this is the newest.
    version_count: int = 1
    is_latest: bool = True
    bundle_components: list[SoftwareBundleComponentOut] = Field(default_factory=list)
    bundle_parent_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class SoftwareVersionCreate(BaseModel):
    """Add a version to an existing software, starting from another version."""

    version: str
    # The version to copy vendor devices from. Defaults to the one being
    # branched from; set copy_vendor_devices=False to start empty.
    copy_vendor_devices: bool = True
    misc_data: dict | None = None


class SoftwareTestedDeviceOut(BaseModel):
    """A device this software has actually been tested against (from tests)."""

    device: DeviceOut
    test_count: int
    last_test_at: datetime | None = None
    outcomes: dict = Field(default_factory=dict)  # e.g. {"pass": 3, "fail": 1}
    component: SoftwareBundleComponentOut | None = None


class SoftwareTestedDevicesOut(BaseModel):
    software_id: str
    devices: list[SoftwareTestedDeviceOut]


class DeviceCompatibleSoftwareOut(BaseModel):
    """One software version the vendor claims runs on a given device."""

    software: SoftwareOut
    # The best status among the matching claims: a "supported" row outranks a
    # "partial" one describing the same device.
    support_status: str
    # Device fields the claims actually pinned down, e.g. ["make", "model"].
    # A short list means a broad claim ("any Cisco"), not a weak one.
    matched_on: list[str] = Field(default_factory=list)
    vendor_devices: list["VendorDeviceOut"] = Field(default_factory=list)


class DeviceCompatibilityOut(BaseModel):
    device_id: str
    items: list[DeviceCompatibleSoftwareOut]


# ---------- Vendor devices ----------
# Hardware a vendor claims their software works against. Not inventory: these never
# reference a row in `devices`.

def _validate_support_status(v: str | None) -> str | None:
    if v is not None and v not in VENDOR_SUPPORT_STATUSES:
        raise ValueError(f"support_status must be one of {VENDOR_SUPPORT_STATUSES}")
    return v


class VendorDeviceBase(BaseModel):
    make: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    architecture: str | None = None
    support_status: str = "supported"
    source: str | None = None
    notes: str | None = None
    misc_data: dict = Field(default_factory=dict)


class VendorDeviceCreate(VendorDeviceBase):
    @field_validator("support_status")
    def _check_support_status(cls, v):
        return _validate_support_status(v)


class VendorDeviceUpdate(BaseModel):
    @field_validator("support_status")
    def _check_support_status(cls, v):
        return _validate_support_status(v)

    make: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    architecture: str | None = None
    support_status: str | None = None
    source: str | None = None
    notes: str | None = None
    misc_data: dict | None = None


class VendorDeviceOut(VendorDeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    software_id: str
    created_at: datetime
    updated_at: datetime | None = None


# ---------- Tests ----------

class TestCreate(BaseModel):
    software_id: str
    component_id: str | None = None
    component_name: str | None = None
    component_version: str | None = None
    device_id: str
    # The software build the run exercised. Left unset, the API snapshots the
    # software's current version rather than leaving the run unattributed.
    software_version: str | None = None
    outcome: str
    tag: str = "adhoc"
    misc_data: dict = Field(default_factory=dict)
    notes: str | None = None
    run_at: date | None = None


class TestImportRow(BaseModel):
    """One row of an imported test file.

    Import files are filled in by hand from a template, so a row may name its
    software and device instead of quoting their UUIDs. Exactly one of each pair
    has to be present; the API resolves the names before creating the row.
    """

    id: str | None = None
    software_id: str | None = None
    software_name: str | None = None
    device_id: str | None = None
    device_unique_id: str | None = None
    software_version: str | None = None
    component_id: str | None = None
    component_name: str | None = None
    component_version: str | None = None
    outcome: str
    tag: str = "adhoc"
    misc_data: dict = Field(default_factory=dict)
    notes: str | None = None
    run_at: date | None = None

    @field_validator("run_at", mode="before")
    @classmethod
    def _import_run_at(cls, value):
        """Accept spreadsheet-friendly DD/MM/YY as well as ISO dates."""
        if isinstance(value, str) and "/" in value:
            try:
                return datetime.strptime(value.strip(), "%d/%m/%y").date()
            except ValueError as exc:
                raise ValueError("run_at must be YYYY-MM-DD or DD/MM/YY") from exc
        return value


class TestUpdate(BaseModel):
    component_id: str | None = None
    software_version: str | None = None
    outcome: str | None = None
    tag: str | None = None
    misc_data: dict | None = None
    notes: str | None = None
    run_at: date | None = None


class TestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    software_id: str
    software_name: str | None = None
    software_version: str | None = None
    component_id: str | None = None
    component_name: str | None = None
    component_version: str | None = None
    device_id: str
    device_unique_id: str | None = None
    device_make: str | None = None
    device_model: str | None = None
    outcome: str
    tag: str
    misc_data: dict
    notes: str | None = None
    run_at: date | None = None
    created_at: datetime
    updated_at: datetime | None = None
    created_by_username: str | None = None


# ---------- Notifications ----------

class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    title: str
    body: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class UnreadCountOut(BaseModel):
    unread: int


# ---------- Saved filters ----------

class SavedFilterBase(BaseModel):
    name: str
    entity: str
    # The screen shape this view was built for. Views are listed and defaulted
    # per platform, so a phone never opens a desktop's fourteen-column view.
    platform: str = "desktop"
    # AG Grid state: filters are a dict, sort/column state are lists
    filter_state: dict | list = Field(default_factory=dict)
    sort_state: dict | list = Field(default_factory=list)
    column_state: dict | list = Field(default_factory=list)
    page_size: int = Field(default=100, ge=1, le=500)
    quick_filter: str | None = None
    is_default: bool = False


class SavedFilterCreate(SavedFilterBase):
    pass


class SavedFilterUpdate(BaseModel):
    name: str | None = None
    platform: str | None = None
    filter_state: dict | list | None = None
    sort_state: dict | list | None = None
    column_state: dict | list | None = None
    page_size: int | None = Field(default=None, ge=1, le=500)
    quick_filter: str | None = None
    is_default: bool | None = None


class SavedFilterOut(SavedFilterBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime | None = None


# ---------- Audit ----------

class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    username: str
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    detail: dict
    ip_address: str | None = None


# ---------- Users ----------

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    auth_provider: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None
    # True while the user's most recent login is still within the token TTL
    # (stateless JWTs: a login is "current" until its tokens expire).
    is_online: bool = False


class UserCreate(BaseModel):
    """A local account, made by an admin on the Users page.

    SSO users never come through here — Authentik provisions those on first
    login — so `auth_provider` is not a field: anything created this way is
    local by definition.
    """

    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: str = "readonly"
    email: str | None = None

    @field_validator("username")
    def _clean_username(cls, v):
        return _validate_username(v)

    @field_validator("role")
    def _check_role(cls, v):
        return _validate_role(v)


class UserUpdate(BaseModel):
    is_active: bool | None = None
    email: str | None = None
    # Only meaningful for a local account. An SSO user's role is re-resolved
    # from their Authentik groups on every login, so a value written here would
    # be overwritten at their next sign-in; the endpoint rejects it outright
    # rather than accepting an edit that silently will not stick.
    role: str | None = None

    @field_validator("role")
    def _check_role(cls, v):
        return v if v is None else _validate_role(v)


class PasswordReset(BaseModel):
    """An admin setting another local account's password.

    Deliberately does not ask for the admin's own password: they already hold an
    admin session, which is the thing being trusted here.
    """

    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


# ---------- API keys ----------

class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # Defaults to the least privilege that exists. A key that can only read is
    # the right default for the thing most keys are for (scripts, the MCP
    # server); anything more has to be asked for deliberately.
    role: str = "readonly"
    # None means "does not expire". Capped at ten years so a typo cannot mint
    # something that outlives the deployment.
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("role")
    @classmethod
    def _role_known(cls, v: str) -> str:
        if v not in ROLE_RANK:
            raise ValueError(f"role must be one of {tuple(ROLE_RANK)}")
        return v

    @field_validator("label")
    @classmethod
    def _label_stripped(cls, v: str) -> str:
        label = v.strip()
        if not label:
            raise ValueError("label must not be blank")
        return label


class ApiKeyOut(BaseModel):
    """A key as listed. Never carries the secret — it does not exist any more."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    prefix: str
    role: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    # Computed rather than stored: "expired" is a function of the clock, and a
    # row that quietly went stale should not need a write to say so.
    is_active: bool = True


class ApiKeyCreated(BaseModel):
    """The create response, and the only time the plaintext is ever returned."""

    key: ApiKeyOut
    plaintext: str


# ---------- Auth ----------

class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Scan ----------

class DeviceScanProbe(BaseModel):
    """One address the scan tried, and what answered there."""

    role: str  # "wan" | "lan"
    ip: str
    online: bool
    services: list[str] = Field(default_factory=list)  # e.g. ["icmp", "ssh"]


class DeviceScanResult(BaseModel):
    device: DeviceOut
    online: bool
    checks: dict = Field(default_factory=dict)  # {ip: {icmp: bool, http: bool, ...}}
    # Per-address outcome. Empty means the device had no address to probe,
    # which is not the same as offline.
    probes: list[DeviceScanProbe] = Field(default_factory=list)
    error: str | None = None


class DeviceScanState(BaseModel):
    """A device's online state, and nothing else.

    What the fleet view needs to paint its online dots while a large scan is
    in flight, without pulling every column of every device every couple of
    seconds — and without a poll overwriting cells the user is part-way
    through editing.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    online_status: bool = False
    last_seen_online: datetime | None = None
    last_scanned_at: datetime | None = None


class ScanStatusOut(BaseModel):
    running: bool
    scanned: int = 0
    total: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    interval_minutes: int = 0


class DeviceInfoConfigOut(BaseModel):
    enabled: bool
    required_fields: list[str] = Field(default_factory=list)
    url_field: str


class DeviceInfoLaunchOut(BaseModel):
    container_id: str


# ---------- Global search ----------

class SearchResults(BaseModel):
    devices: list[DeviceOut]
    software: list[SoftwareOut]
    tests: list[TestOut]
    # Totals ignoring the per-group limit, so the UI can say "showing 50 of 132".
    devices_total: int = 0
    software_total: int = 0
    tests_total: int = 0


# ---------- Suggestions ----------

class SuggestionsOut(BaseModel):
    """Values a free-text field already holds, commonest first."""

    values: list[str]


# ---------- Bulk / import ----------

class BulkIds(BaseModel):
    """The body of a multi-select delete. Typed rather than a bare dict so a
    malformed payload is a 422 instead of an iteration over a string's
    characters."""

    ids: list[str] = Field(default_factory=list)


class BulkPayload(BaseModel):
    upserts: list[dict] = Field(default_factory=list)
    delete_ids: list[str] = Field(default_factory=list)


class ImportResult(BaseModel):
    created: int
    updated: int
    errors: list[dict]


DeviceDetail.model_rebuild()
