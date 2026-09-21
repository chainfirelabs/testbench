"""The one place the published device configuration is resolved.

Everything that needs to know what a device looks like — the grid, the forms,
imports, exports, search, plugin availability, every write path — asks here.
No endpoint reproduces the merge, because two implementations of "what does a
router look like" is how a UI and an API come to disagree about whether a save
is valid.

The merge, in order:

    protected system fields
      + global field assignments
      + type-specific field assignments
      + type-specific overrides
      - explicit exclusions, where policy allows them

Protected system fields are ordinary definitions with a flag; they arrive here
through their global assignment like everything else, and the flag is what
stops them being deleted or having their meaning changed underneath the
application code that reads them by name.
"""

from __future__ import annotations

import logging
import re
import threading
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime
from hashlib import sha1
from typing import Any, Iterable

from sqlalchemy import Boolean, Numeric, cast, func, or_, select, text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import engine, jsonable, utcnow
from .permissions import DEVICES_EDIT, caller_permissions, satisfies_role
from ..models import (
    Device,
    DeviceFieldAssignment,
    DeviceFieldDefinition,
    DeviceFieldIndex,
    DeviceSchemaRevision,
    DeviceType,
    DeviceTypePlugin,
    EntityField,
    User,
)

logger = logging.getLogger(__name__)

FIELD_TYPES = {"text", "textarea", "password", "number", "boolean", "date", "select", "json"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
TYPE_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,99}$")

# Fields the application itself depends on. They cannot be deleted, and their
# key, type and semantic role are fixed; an administrator may still relabel,
# reorder and — except where noted below — hide them.
PROTECTED_SYSTEM_FIELDS = {
    "unique_id", "status", "online_status", "last_seen_online", "last_scanned_at",
    "checked_out_by_username", "checked_out_at", "checkout_due", "checkout_purpose",
    "misc_data",
}

# Hiding these would disable behaviour the application cannot do without: a
# device with no identity cannot be addressed at all.
LOCKED_VISIBLE = {"unique_id"}

# Roles whose value is an address the device answers on, and so is a web page
# worth offering as a link by default. Only a default: `opens_web_page` is the
# flag the UI actually reads, and it is the administrator's to set on any field.
WEB_ADDRESS_ROLES = {"scan_address_wan", "scan_address_lan"}

# The schemes a link may use. Nothing else is offered and nothing else is
# accepted: a `javascript:` or `file:` link is not something an administrator
# should be able to configure any more than an operator can store one.
LINK_SCHEMES = ("http", "https")

# Where a field's value actually lives.
#   column   — a real devices column
#   derived  — read-only, resolved through a relationship
#   virtual  — not a value at all; a projection of the rest of the document
#   data     — the installation-defined JSONB document (everything else)
COLUMN_FIELDS = {"unique_id"}
DERIVED_FIELDS = {"checked_out_by_username"}
VIRTUAL_FIELDS = {"misc_data"}

# Keys that never belong in a device document, whatever a caller sends — and
# so also names a field may not be given, which is where this is enforced: a
# field whose key is one of these would have every value stripped out of the
# document by the envelope before it reached storage, leaving a column that
# looks writable and keeps nothing.
RESERVED_DOCUMENT_KEYS = {"id", "unique_id", "device_type", "device_type_id",
                          "device_type_key", "device_type_label", "misc_data",
                          "created_at", "updated_at", "created_by", "updated_by",
                          "checked_out_by", "checked_out_by_username",
                          "link_overrides"}


def storage_for(key: str) -> str:
    if key in COLUMN_FIELDS:
        return "column"
    if key in DERIVED_FIELDS:
        return "derived"
    if key in VIRTUAL_FIELDS:
        return "virtual"
    return "data"


@dataclass(frozen=True)
class EffectiveField:
    """One field as it applies to one device type, after the merge."""

    key: str
    label: str
    field_type: str
    description: str | None
    options: tuple[str, ...]
    validation: dict
    default_value: Any
    sensitive: bool
    indexed: bool
    unique_value: bool
    opens_web_page: bool
    link_scheme: str
    link_port: int | None
    role: str | None
    protected: bool
    visible: bool
    list_visible: bool
    required: bool
    writable: bool
    position: int
    scope: str
    configuration_source: str
    definition_id: str
    storage: str

    @property
    def stored(self) -> bool:
        """True when the field is a real value in the JSONB document."""
        return self.storage == "data"


class SchemaError(ValueError):
    """A configuration change that cannot be published as asked."""


class DeviceValidationError(ValueError):
    """A device document that does not satisfy its type's published schema."""


def normalize_link_overrides(value: Any) -> dict:
    """One device's per-field link overrides, checked and tidied.

    Shape: `{field_key: {"scheme": "https", "port": 8443}}`. Both members are
    optional — an override that sets only a port keeps the field's scheme — and
    an entry that ends up saying nothing is dropped rather than stored, so
    clearing the controls in the UI removes the override instead of leaving an
    empty one behind to puzzle over later.

    Validated rather than trusted even though it never reaches a field value:
    it does reach an `href`, and `scheme` is the half of a URL that decides
    whether a link is a link or a script. Only `http` and `https` are accepted,
    which is the same whitelist the frontend applies to a parsed value.

    The field key is not checked against the published schema on purpose. An
    override for a field that this type does not show, or does not show *yet*,
    is the operator's data in exactly the way an unknown document key is: it
    costs nothing to keep and is wrong to throw away on a type change.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DeviceValidationError("link_overrides must be an object keyed by field")
    result: dict[str, dict] = {}
    for key, entry in value.items():
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise DeviceValidationError(f"link_overrides.{key} must be an object")
        override: dict[str, Any] = {}
        scheme = entry.get("scheme")
        if scheme not in (None, ""):
            scheme = str(scheme).lower().rstrip(":/")
            if scheme not in LINK_SCHEMES:
                raise DeviceValidationError(
                    f"link_overrides.{key}.scheme must be one of {', '.join(LINK_SCHEMES)}"
                )
            override["scheme"] = scheme
        port = entry.get("port")
        if port not in (None, ""):
            try:
                port = int(port)
            except (TypeError, ValueError):
                raise DeviceValidationError(f"link_overrides.{key}.port must be a number") from None
            if not 1 <= port <= 65535:
                raise DeviceValidationError(
                    f"link_overrides.{key}.port must be between 1 and 65535"
                )
            override["port"] = port
        if override:
            result[str(key)] = override
    return result


# ---------------------------------------------------------------- revisions

def current_revision(db: Session) -> int:
    return db.scalar(select(func.max(DeviceSchemaRevision.id))) or 0


def publish_revision(
    db: Session,
    *,
    source: str = "gui",
    note: str | None = None,
    summary: dict | None = None,
    user: User | None = None,
) -> int:
    """Record a new published revision and invalidate every cached schema.

    Called by every mutation. Draft tables are not here yet, but the boundary
    is: readers resolve "the published revision", so adding a draft stage later
    changes what publishing means without changing anyone who reads it.
    """
    revision = DeviceSchemaRevision(
        created_at=utcnow(),
        created_by=user.id if user else None,
        source=source,
        note=note,
        summary=summary or {},
    )
    db.add(revision)
    db.flush()
    invalidate_cache()
    return revision.id


_cache_lock = threading.RLock()
_cache: dict[tuple[int, str | None], tuple[EffectiveField, ...]] = {}


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ------------------------------------------------------------------ merging

def _base_field(definition: DeviceFieldDefinition, assignment: DeviceFieldAssignment | None,
                *, scope: str, fallback_position: int) -> EffectiveField:
    assignment = assignment or DeviceFieldAssignment(field_definition_id=definition.id)
    return EffectiveField(
        key=definition.key,
        label=assignment.label_override or definition.label,
        field_type=definition.field_type,
        description=assignment.description_override or definition.description,
        options=tuple(definition.options or []),
        validation={**(definition.validation or {}), **(assignment.validation_override or {})},
        default_value=definition.default_value,
        sensitive=definition.sensitive,
        indexed=definition.indexed,
        unique_value=definition.unique_value,
        opens_web_page=definition.opens_web_page,
        link_scheme=definition.link_scheme or "http",
        link_port=definition.link_port,
        role=definition.plugin_role,
        protected=definition.protected_system_field,
        visible=True if assignment.visible is None else assignment.visible,
        list_visible=True if assignment.list_visible is None else assignment.list_visible,
        required=False if assignment.required is None else assignment.required,
        writable=True if assignment.writable is None else assignment.writable,
        position=fallback_position if assignment.position is None else assignment.position,
        scope=scope,
        configuration_source=assignment.configuration_source or definition.configuration_source,
        definition_id=definition.id,
        storage=storage_for(definition.key),
    )


def _apply_override(field: EffectiveField, assignment: DeviceFieldAssignment) -> EffectiveField:
    """Layer a type-specific row over the global one it overrides.

    Only the columns the administrator actually set are applied — null means
    inherit, which is why the override columns are nullable rather than
    carrying a copy of whatever the global row happened to say.
    """
    return replace(
        field,
        label=assignment.label_override or field.label,
        description=assignment.description_override or field.description,
        validation={**field.validation, **(assignment.validation_override or {})},
        visible=field.visible if assignment.visible is None else assignment.visible,
        list_visible=field.list_visible if assignment.list_visible is None else assignment.list_visible,
        required=field.required if assignment.required is None else assignment.required,
        writable=field.writable if assignment.writable is None else assignment.writable,
        position=field.position if assignment.position is None else assignment.position,
        scope="type",
        configuration_source=assignment.configuration_source or field.configuration_source,
    )


def _definitions(db: Session) -> dict[str, DeviceFieldDefinition]:
    return {
        definition.id: definition
        for definition in db.scalars(select(DeviceFieldDefinition).where(DeviceFieldDefinition.enabled.is_(True)))
    }


def _display_order(field: EffectiveField) -> tuple[int, int, str]:
    """Return the canonical column order for an effective field.

    ``unique_id`` is the immutable identity anchor for every device layout.
    A type-specific assignment can otherwise have the same (or an earlier)
    position than the inherited global assignment, allowing a field such as
    ``architecture`` to sort ahead of it.
    """
    if field.key == "unique_id":
        return (0, 0, "")
    return (1, field.position, field.key)


def _resolve(db: Session, device_type_id: str | None) -> tuple[EffectiveField, ...]:
    definitions = _definitions(db)
    scope = DeviceFieldAssignment.device_type_id.is_(None)
    if device_type_id is not None:
        # `IN (NULL, id)` is not this clause: SQL comparison against NULL is
        # never true, so it would silently drop every global assignment and a
        # type would inherit nothing.
        scope = or_(scope, DeviceFieldAssignment.device_type_id == device_type_id)
    assignments = list(db.scalars(select(DeviceFieldAssignment).where(scope)))
    globals_ = [a for a in assignments if a.device_type_id is None]
    specific = [a for a in assignments if a.device_type_id is not None]

    merged: dict[str, EffectiveField] = {}
    for index, assignment in enumerate(sorted(globals_, key=lambda a: (a.position if a.position is not None else 10_000, a.id))):
        definition = definitions.get(assignment.field_definition_id)
        if definition is None:
            continue
        merged[definition.key] = _base_field(definition, assignment, scope="global", fallback_position=index)

    for assignment in specific:
        definition = definitions.get(assignment.field_definition_id)
        if definition is None:
            continue
        existing = merged.get(definition.key)
        if assignment.excluded:
            # A global field taken back off one type. Refused for protected
            # fields, and only honoured at all when the installation has opted
            # in — otherwise "global" would not mean anything.
            if existing and not definition.protected_system_field and settings.device_schema_allow_global_exclusions:
                merged.pop(definition.key, None)
            continue
        if existing is not None:
            merged[definition.key] = _apply_override(existing, assignment)
        else:
            merged[definition.key] = _base_field(
                definition, assignment, scope="type", fallback_position=len(merged) + 1_000
            )

    # unique_id anchors every layout: it is the column a person reads a row by.
    for key in LOCKED_VISIBLE:
        if key in merged and not merged[key].visible:
            merged[key] = replace(merged[key], visible=True)

    return tuple(sorted(merged.values(), key=_display_order))


def get_effective_fields(db: Session, device_type_id: str | None) -> tuple[EffectiveField, ...]:
    """Every field that applies to one device type, in display order.

    Cached against the published revision, so a schema change is picked up on
    the next request everywhere without anyone restarting anything.
    """
    revision = current_revision(db)
    key = (revision, device_type_id)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    resolved = _resolve(db, device_type_id)
    with _cache_lock:
        if _cache and next(iter(_cache))[0] != revision:
            _cache.clear()
        _cache[key] = resolved
    return resolved


def layout_keys(
    db: Session,
    device_type_id: str | None,
    cache: dict[str | None, frozenset[str]] | None = None,
) -> frozenset[str]:
    """Every document key one device type's page accounts for.

    The complement of `misc_data`: a value whose key is in here has a field of
    its own and is shown there, so it is not unexplained data. `misc_data`
    itself is excluded because it is the projection, not a member of it.

    `cache` is a dict the caller keeps for the length of one request.
    `get_effective_fields` is cached against the published revision, but
    reaching that cache still reads the current revision, and serialising a
    page of a thousand devices otherwise asks that question a thousand times to
    answer it for the same handful of device types.
    """
    if cache is not None and device_type_id in cache:
        return cache[device_type_id]
    keys = frozenset(
        field.key for field in get_effective_fields(db, device_type_id) if field.key != "misc_data"
    )
    if cache is not None:
        cache[device_type_id] = keys
    return keys


def get_global_fields(db: Session) -> tuple[EffectiveField, ...]:
    """The fields every device type inherits, including future ones."""
    return get_effective_fields(db, None)


def effective_field_map(db: Session, device_type_id: str | None) -> dict[str, EffectiveField]:
    return {field.key: field for field in get_effective_fields(db, device_type_id)}


def union_field_map(db: Session) -> dict[str, EffectiveField]:
    """Every field any device type defines, global ones taking precedence.

    What a query over the whole fleet reads. Resolving a filter against the
    global scope alone would silently ignore `?carrier=Verizon` on the all
    devices page — carrier belongs to phones — and answering a filter by
    returning everything is worse than not offering it.
    """
    merged: dict[str, EffectiveField] = {}
    for item in db.scalars(select(DeviceType)):
        for field in get_effective_fields(db, item.id):
            merged.setdefault(field.key, field)
    # Global last so a type's override never redefines a shared column here.
    merged.update({field.key: field for field in get_global_fields(db)})
    return merged


def visible_fields(fields: Iterable[EffectiveField]) -> list[EffectiveField]:
    return [field for field in fields if field.visible]


def fields_for_device(db: Session, device: Device) -> tuple[EffectiveField, ...]:
    return get_effective_fields(db, device.device_type_id)


def role_map(fields: Iterable[EffectiveField]) -> dict[str, EffectiveField]:
    return {field.role: field for field in fields if field.role}


# --------------------------------------------------------------- validation

def _coerce(field: EffectiveField, value: Any) -> Any:
    try:
        if field.field_type == "number" and not isinstance(value, (int, float)) or (
            field.field_type == "number" and isinstance(value, bool)
        ):
            text_value = str(value)
            value = float(text_value) if "." in text_value or "e" in text_value.lower() else int(text_value)
        elif field.field_type == "boolean" and not isinstance(value, bool):
            folded = str(value).strip().lower()
            if folded not in {"true", "false", "1", "0", "yes", "no"}:
                raise ValueError
            value = folded in {"true", "1", "yes"}
        elif field.field_type == "date":
            if isinstance(value, datetime):
                value = value.date()
            value = value.isoformat() if isinstance(value, date) else date.fromisoformat(str(value)).isoformat()
        elif field.field_type == "json":
            if not isinstance(value, (dict, list)):
                raise ValueError
        elif field.field_type in {"text", "textarea", "password", "select"} and not isinstance(value, str):
            value = str(value)
        if field.field_type == "select" and field.options and value not in field.options:
            # Fold case and whitespace before refusing: an imported "X86_64" is
            # the option `x86_64` spelled differently, not a different value.
            folded = str(value).strip().lower()
            match = next((option for option in field.options if option.lower() == folded), None)
            if match is not None:
                value = match
    except (TypeError, ValueError) as exc:
        raise DeviceValidationError(f"{field.label} must be a valid {field.field_type}") from exc
    return value


def _check_rules(field: EffectiveField, value: Any) -> None:
    rules = field.validation or {}
    if field.field_type == "select" and field.options and value not in field.options:
        raise DeviceValidationError(f"{field.label} must be one of: {', '.join(field.options)}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if rules.get("min") is not None and value < rules["min"]:
            raise DeviceValidationError(f"{field.label} must be at least {rules['min']}")
        if rules.get("max") is not None and value > rules["max"]:
            raise DeviceValidationError(f"{field.label} must be at most {rules['max']}")
    if isinstance(value, str):
        if rules.get("min_length") is not None and len(value) < rules["min_length"]:
            raise DeviceValidationError(f"{field.label} must be at least {rules['min_length']} characters")
        if rules.get("max_length") is not None and len(value) > rules["max_length"]:
            raise DeviceValidationError(f"{field.label} must be at most {rules['max_length']} characters")
        pattern = rules.get("pattern")
        if pattern:
            try:
                matches = re.fullmatch(pattern, value) is not None
            except re.error as exc:
                raise SchemaError(f"{field.label} has an invalid validation pattern: {exc}") from exc
            if not matches:
                raise DeviceValidationError(f"{field.label} does not match the required format")


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def validate_device_document(
    db: Session,
    device_type_id: str | None,
    document: dict,
    *,
    partial: bool = False,
    device_id: str | None = None,
    existing: dict | None = None,
    enforce_required: bool | None = None,
) -> dict:
    """Normalise and check one device document against its published schema.

    Every write path goes through here — single writes, bulk, CSV and JSON
    imports, plugin callbacks and internal discovery updates — so there is one
    answer to whether a value is acceptable rather than one per entry point.

    On a partial write only the supplied keys are considered, and a required
    field is checked against what the device will hold afterwards rather than
    against this request alone. Keys not in the catalog are preserved as they
    are: a field taken off a layout keeps its stored values, which is the whole
    point of a configurable schema.

    `enforce_required` checks every required field even on a partial write
    and revalidates retained values against the destination schema. That is
    what moving a device to another type needs: the whole document must
    satisfy the type it is arriving at.
    """
    if enforce_required is None:
        enforce_required = not partial
    fields = effective_field_map(db, device_type_id)
    merged_view = {**(existing or {}), **document}
    normalized: dict[str, Any] = {}

    if partial and enforce_required:
        # A type change must check retained values against the destination's
        # types and overrides, not just check that required keys are present.
        for key, value in (existing or {}).items():
            field = fields.get(key)
            if field is None or not field.stored or _is_blank(value):
                continue
            if key in document and field.writable:
                continue
            coerced = _coerce(field, value)
            _check_rules(field, coerced)
            if field.writable:
                normalized[key] = coerced

    for key, value in document.items():
        if key in RESERVED_DOCUMENT_KEYS:
            continue
        field = fields.get(key)
        if field is None:
            if settings.device_schema_reject_unknown_fields:
                raise DeviceValidationError(f"Unknown device field: {key}")
            # Kept: a value whose field was removed from the layout is still
            # the operator's data. Made JSON-safe first, because this is the
            # one path into the document that does not go through `_coerce`,
            # and the API's own model parses `last_seen_online`, `checkout_due`
            # and `last_scanned_at` into Python date objects before they get
            # here. A device carrying one for a field this installation never
            # configured used to fail at the flush, as a 500 with no field
            # named — which is why nothing about the API said it was possible.
            normalized[key] = jsonable(value)
            continue
        if not field.stored:
            continue
        if not field.writable:
            # Scan and checkout columns are the server's to write. Silently
            # ignored rather than refused so that re-importing an export — which
            # carries them — is not an error.
            continue
        if _is_blank(value):
            # An explicitly blank sensitive value is a configured credential:
            # plenty of appliances use a username with no password at all, and
            # "stored as empty" has to stay distinguishable from "never set".
            # An explicit null still clears the field, for either kind.
            normalized[key] = "" if field.sensitive and value is not None else None
            continue
        coerced = _coerce(field, value)
        _check_rules(field, coerced)
        normalized[key] = coerced

    for key, field in fields.items():
        if not field.stored or not field.required:
            continue
        if partial and not enforce_required and key not in document:
            continue
        if field.sensitive:
            # Presence is what "configured" means for a credential, so a
            # required one is satisfied by a deliberate empty value.
            if key in normalized and normalized[key] is None:
                raise DeviceValidationError(f"{field.label} is required")
            if key not in normalized and merged_view.get(key) is None:
                raise DeviceValidationError(f"{field.label} is required")
            continue
        effective_value = normalized.get(key, merged_view.get(key))
        if _is_blank(effective_value):
            raise DeviceValidationError(f"{field.label} is required")

    _check_unique(db, fields, normalized, device_id)
    return normalized


def _check_unique(db: Session, fields: dict[str, EffectiveField], document: dict, device_id: str | None) -> None:
    for key, value in document.items():
        field = fields.get(key)
        if field is None or not field.unique_value or not field.stored or _is_blank(value):
            continue
        query = select(Device.unique_id).where(Device._data[key].astext == str(value))
        if device_id:
            query = query.where(Device.id != device_id)
        clash = db.scalar(query.limit(1))
        if clash:
            raise DeviceValidationError(f"{field.label} '{value}' is already used by {clash}")


def apply_defaults(fields: Iterable[EffectiveField], document: dict) -> dict:
    """Fill in the catalog's defaults for keys a create did not mention."""
    result = dict(document)
    for field in fields:
        if field.stored and field.default_value is not None and field.key not in result:
            result[field.key] = field.default_value
    return result


# ------------------------------------------------------------- SQL bindings

def device_field_expression(field: EffectiveField):
    """A typed SQL expression for one catalog field, for filters and sorting."""
    if field.key == "unique_id":
        return Device.unique_id
    raw = Device._data[field.key].astext
    if field.field_type == "number":
        return cast(func.nullif(raw, ""), Numeric)
    if field.field_type == "boolean":
        return cast(func.nullif(raw, ""), Boolean)
    # Validated ISO dates sort chronologically as text, and match the immutable
    # expression their index is built on. A PostgreSQL date cast is not
    # IMMUTABLE and so cannot appear in an expression index at all.
    return raw


def coerce_filter_value(field: EffectiveField, value: Any) -> Any:
    if field.field_type == "boolean" and isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if field.field_type == "number" and isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value
    return value


def device_order_by(db: Session, sort: str, order: str):
    fields = effective_field_map(db, None)
    if sort in {"id", "created_at", "updated_at"}:
        expression = getattr(Device, sort)
    elif sort == "unique_id":
        expression = Device.unique_id
    elif sort in fields and fields[sort].stored:
        expression = device_field_expression(fields[sort])
    else:
        expression = Device.created_at
    return expression.desc() if order == "desc" else expression.asc()


# ---------------------------------------------------------- managed indexes

def _index_expression(field: EffectiveField) -> str:
    value = f"data->>'{field.key}'"
    if field.field_type == "number":
        return f"(NULLIF({value}, '')::numeric)"
    if field.field_type == "boolean":
        return f"(NULLIF({value}, '')::boolean)"
    if field.field_type == "json":
        return f"(data->'{field.key}')"
    return f"({value})"


def plan_field_indexes(db: Session) -> list[DeviceFieldIndex]:
    """Reconcile the desired index set with what the catalog now asks for.

    Only the plan is written here. Creating and dropping runs outside the
    request transaction, because a CREATE INDEX on a large table is not
    something to do while a user waits on a save.
    """
    revision = current_revision(db)
    wanted: dict[str, DeviceFieldIndex] = {}
    seen: set[str] = set()
    for definition in db.scalars(select(DeviceFieldDefinition).where(DeviceFieldDefinition.enabled.is_(True))):
        if not (definition.indexed or definition.unique_value) or storage_for(definition.key) != "data":
            continue
        field = EffectiveField(
            key=definition.key, label=definition.label, field_type=definition.field_type,
            description=None, options=(), validation={}, default_value=None,
            sensitive=definition.sensitive, indexed=definition.indexed,
            unique_value=definition.unique_value,
            # Presentation, and an index plan is not presentation.
            opens_web_page=False, link_scheme="http", link_port=None,
            role=definition.plugin_role,
            protected=definition.protected_system_field, visible=True, list_visible=True, required=False,
            writable=True, position=0, scope="global",
            configuration_source=definition.configuration_source, definition_id=definition.id,
            storage="data",
        )
        suffix = sha1(f"devices:{definition.key}:{definition.field_type}".encode()).hexdigest()[:10]
        name = f"ix_devices_field_{suffix}{'_uq' if definition.unique_value else ''}"
        seen.add(definition.key)
        wanted[name] = DeviceFieldIndex(
            name=name, field_key=definition.key, expression=_index_expression(field),
            unique_index=definition.unique_value, desired=True, state="pending", revision=revision,
        )

    existing = {row.name: row for row in db.scalars(select(DeviceFieldIndex))}
    for name, planned in wanted.items():
        row = existing.get(name)
        if row is None:
            db.add(planned)
        elif not row.desired or row.expression != planned.expression or row.unique_index != planned.unique_index:
            row.desired, row.expression, row.unique_index = True, planned.expression, planned.unique_index
            row.state, row.error, row.revision = "pending", None, revision
    for name, row in existing.items():
        if name not in wanted and row.desired:
            row.desired, row.state, row.revision = False, "pending", revision
    db.flush()
    return list(db.scalars(select(DeviceFieldIndex)))


def apply_field_indexes(db: Session) -> None:
    """Create and drop the planned indexes, recording what happened.

    Runs with autocommit so each statement stands alone: one index that cannot
    be built — a duplicate value where a unique index was asked for — must not
    take the rest of the plan down with it, and the failure is recorded on the
    row so an administrator can see it and retry.
    """
    rows = list(db.scalars(select(DeviceFieldIndex).where(DeviceFieldIndex.state != "applied")))
    if not rows:
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for row in rows:
            try:
                if row.desired:
                    unique = "UNIQUE " if row.unique_index else ""
                    # The extra parentheses are PostgreSQL's, not decoration:
                    # an expression index needs its expression parenthesised
                    # inside the column list.
                    connection.execute(text(
                        f'CREATE {unique}INDEX IF NOT EXISTS "{row.name}" ON devices ({row.expression})'
                    ))
                else:
                    connection.execute(text(f'DROP INDEX IF EXISTS "{row.name}"'))
                row.state, row.error, row.applied_at = "applied", None, utcnow()
            except Exception as exc:  # noqa: BLE001
                row.state, row.error = "failed", str(exc)[:2000]
                logger.warning("Managed device index %s could not be applied: %s", row.name, exc)
    for row in rows:
        if not row.desired and row.state == "applied":
            db.delete(row)
    db.commit()


def index_status(db: Session) -> list[dict]:
    return [
        {
            "name": row.name, "field": row.field_key, "unique": row.unique_index,
            "desired": row.desired, "state": row.state, "error": row.error,
            "revision": row.revision,
            "applied_at": row.applied_at.isoformat() if row.applied_at else None,
        }
        for row in db.scalars(select(DeviceFieldIndex).order_by(DeviceFieldIndex.field_key))
    ]


# ------------------------------------------------------------ plugin policy

class PluginPolicyError(Exception):
    """A plugin invocation the device-type policy does not permit."""

    def __init__(self, message: str, *, status_code: int = 409, reasons: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.reasons = reasons or []


def get_allowed_plugins(db: Session, device_type_id: str | None) -> dict[str, DeviceTypePlugin]:
    """The plugin allowlist for one device type, most specific row winning.

    Deny by default: a plugin the installation has deployed is still unavailable
    to a device type until a row here says otherwise. A device with no type gets
    nothing at all — there is no configuration to authorise it with.
    """
    if device_type_id is None:
        return {}
    rows = db.scalars(select(DeviceTypePlugin).where(or_(
        DeviceTypePlugin.device_type_id.is_(None),
        DeviceTypePlugin.device_type_id == device_type_id,
    ))).all()
    allowed: dict[str, DeviceTypePlugin] = {}
    for row in sorted(rows, key=lambda item: item.device_type_id is not None):
        # A manifest may refuse to be enabled for everything at once. Enforced
        # here rather than only where assignments are written, so a row that
        # reached the table some other way — a YAML document, a restored
        # backup, a plugin that became disruptive in a later version — still
        # cannot authorise itself across the whole fleet.
        if row.device_type_id is None and not _allows_global_assignment(row.plugin_id):
            continue
        allowed[row.plugin_id] = row
    return {plugin_id: row for plugin_id, row in allowed.items() if row.enabled}


def _allows_global_assignment(plugin_id: str) -> bool:
    from .plugin_host import registry

    manifest = registry.manifest(plugin_id)
    if manifest is None:
        return True
    return all(
        action.get("allow_global_assignment", True)
        for action in manifest.get("actions", [])
        if action.get("entity") == "devices"
    )


def plugin_assignments(db: Session, device_type_id: str | None) -> list[DeviceTypePlugin]:
    return list(db.scalars(
        select(DeviceTypePlugin)
        .where(DeviceTypePlugin.device_type_id.is_(None) if device_type_id is None
               else DeviceTypePlugin.device_type_id == device_type_id)
        .order_by(DeviceTypePlugin.plugin_id)
    ))


def _role_groups(action: dict) -> list[list[str]]:
    groups = [list(group) for group in action.get("required_role_groups", []) if group]
    # `required_any_roles` is the single-group spelling the shipped manifests
    # already use; both mean "any one of these satisfies the requirement".
    if action.get("required_any_roles"):
        groups.append(list(action["required_any_roles"]))
    return groups


def _value_for_role(device: Device, field: EffectiveField) -> Any:
    if field.storage == "column":
        return getattr(device, field.key, None)
    return (device._data or {}).get(field.key)


def output_roles_blocker(manifest: dict, fields: Iterable[EffectiveField]) -> str | None:
    roles = role_map(field for field in fields if field.visible)
    missing = sorted(set(manifest.get("required_output_roles", [])) - roles.keys())
    if missing:
        return "Required dynamic column roles are not configured: " + ", ".join(missing)
    optional = set(manifest.get("optional_output_roles", []))
    minimum = int(manifest.get("minimum_output_roles", 0))
    configured = len((set(manifest.get("required_output_roles", [])) | optional) & roles.keys())
    if configured < minimum:
        return f"Configure at least {minimum} dynamic column role(s) from: " + ", ".join(sorted(optional))
    return None


def _action_blocker(
    db: Session,
    action: dict,
    device: Device | None,
    fields: tuple[EffectiveField, ...],
    user: User | None,
) -> str | None:
    """Why this action cannot run here, or None when it can.

    The order is deliberate: policy first (is this plugin allowed to touch this
    kind of device at all), then configuration (does the type even have the
    fields the plugin needs), then data (are they filled in), then state.
    A person reading the tooltip should be told the thing they can fix.
    """
    if user is None:
        # No caller to check: the listing endpoints use this to describe an
        # action in the abstract, and the policy checks below stand on their own.
        held = None
    else:
        held = caller_permissions(db, user)
    required_user_role = action.get("required_user_role")
    if held is not None:
        if required_user_role and not satisfies_role(db, held, required_user_role):
            return f"{required_user_role} permission is required"
        # Running a plugin against a device writes to the device, so it needs
        # the permission that editing a device needs.
        if DEVICES_EDIT not in held:
            return "permission to edit devices is required"
    roles = role_map(field for field in fields if field.visible)
    missing_roles = [role for role in action.get("required_roles", []) if role not in roles]
    if missing_roles:
        return "the device type has no field for: " + ", ".join(missing_roles)
    for group in _role_groups(action):
        if not any(role in roles for role in group):
            return "the device type has no field for any of: " + ", ".join(group)
    if device is None:
        return None
    missing_values = [
        roles[role].label for role in action.get("required_roles", [])
        # A sensitive field counts as supplied when the key is present, even
        # empty: plenty of appliances use a username with no password at all.
        if not _role_present(device, roles[role])
    ]
    if missing_values:
        return "missing " + ", ".join(missing_values)
    for group in _role_groups(action):
        present = [role for role in group if role in roles and _role_present(device, roles[role])]
        if not present:
            labels = [roles[role].label for role in group if role in roles]
            return "add a value for " + " or ".join(labels)
    if action.get("requires_online") and device.online_status is not True:
        return "this device is offline; run Scan first"
    return None


def _role_present(device: Device, field: EffectiveField) -> bool:
    if field.sensitive and field.storage == "data":
        return field.key in (device._data or {})
    value = _value_for_role(device, field)
    return value is not None and value != ""


def get_available_actions(db: Session, device: Device, user: User | None = None) -> list[dict]:
    """Every plugin action for one device, each with why it can or cannot run.

    The intersection the plan describes: installed and healthy plugins, the
    device type's allowlist, the actions that plugin offers, the semantic roles
    the type's schema actually provides, the values this device carries, and
    what the caller is allowed to do. The frontend renders what comes back and
    decides nothing itself.
    """
    from .plugin_host import registry  # imported late: the registry reads settings at call time

    fields = fields_for_device(db, device)
    allowed = get_allowed_plugins(db, device.device_type_id)
    installed = {manifest["id"]: manifest for manifest in registry.manifests()}
    type_label = device.device_type_label or "Uncategorized"
    result: list[dict] = []

    for plugin_id, manifest in installed.items():
        assignment = allowed.get(plugin_id)
        for action in manifest.get("actions", []):
            if action.get("entity") != "devices":
                continue
            entry = {
                **action,
                "plugin_id": plugin_id,
                "risk": action.get("risk", "normal"),
                "available": False,
                "unavailable_reason": None,
            }
            if assignment is None:
                entry["unavailable_reason"] = (
                    f"{manifest.get('label', plugin_id)} is not enabled for the {type_label} device type"
                    if device.device_type_id
                    else "devices without a device type have no plugins enabled"
                )
            else:
                blocker = output_roles_blocker(manifest, fields) or _action_blocker(db, action, device, fields, user)
                entry["unavailable_reason"] = blocker
                entry["available"] = blocker is None
                entry["configuration"] = assignment.configuration or {}
            result.append(entry)

    # Assignments naming a plugin this deployment does not currently have are
    # kept and reported, so removing a plugin for an afternoon does not destroy
    # the configuration that authorised it.
    for plugin_id, assignment in allowed.items():
        if plugin_id not in installed:
            result.append({
                "plugin_id": plugin_id, "id": None, "entity": "devices",
                "available": False, "unavailable_reason": f"plugin '{plugin_id}' is not installed",
            })
    return result


def validate_plugin_invocation(
    db: Session,
    plugin_id: str,
    action_id: str,
    devices: list[Device],
    user: User | None = None,
) -> tuple[dict, list[Device]]:
    """Authorise one invocation, or refuse it with itemised reasons.

    Repeated on every invocation rather than trusted from the listing: a direct
    API call must not be able to reboot a phone just because the UI would never
    have offered the button.

    A mixed selection is refused outright and every ineligible device is named.
    Processing the eligible half silently would mean a person who selected forty
    devices and pressed Reboot has no idea which ones actually rebooted.
    """
    from .plugin_host import registry

    manifest = registry.manifest(plugin_id)
    if manifest is None:
        raise PluginPolicyError(f"Plugin '{plugin_id}' is not installed or is unhealthy", status_code=409)
    action = next((item for item in manifest.get("actions", []) if item.get("id") == action_id), None)
    if action is None:
        raise PluginPolicyError("Plugin action not found", status_code=404)
    if user is not None:
        held = caller_permissions(db, user)
        required_user_role = action.get("required_user_role")
        if required_user_role and not satisfies_role(db, held, required_user_role):
            raise PluginPolicyError(
                f"{required_user_role} permission is required", status_code=403,
            )
        if DEVICES_EDIT not in held:
            raise PluginPolicyError("Permission to edit devices is required", status_code=403)

    eligible: list[Device] = []
    rejected: list[dict] = []
    for device in devices:
        allowed = get_allowed_plugins(db, device.device_type_id)
        label = device.device_type_label or "Uncategorized"
        if plugin_id not in allowed:
            rejected.append({
                "device_id": device.id, "unique_id": device.unique_id,
                "reason": f"{manifest.get('label', plugin_id)} is not enabled for the {label} device type",
            })
            continue
        fields = fields_for_device(db, device)
        blocker = output_roles_blocker(manifest, fields) or _action_blocker(db, action, device, fields, user)
        if blocker:
            rejected.append({"device_id": device.id, "unique_id": device.unique_id, "reason": blocker})
            continue
        eligible.append(device)

    if action.get("scope") == "collection":
        # A collection action is not somebody's explicit selection, so it runs
        # over whatever the policy permits rather than refusing wholesale. It
        # still needs at least one device it may touch.
        if not eligible:
            raise PluginPolicyError(
                f"{manifest.get('label', plugin_id)} is not enabled for any device type",
                status_code=409,
            )
        return action, eligible

    if rejected:
        summary = "; ".join(f"{item['unique_id']}: {item['reason']}" for item in rejected[:10])
        raise PluginPolicyError(
            f"{len(rejected)} of {len(devices)} selected device(s) cannot run this action — {summary}",
            status_code=422,
            reasons=rejected,
        )
    if not eligible:
        raise PluginPolicyError("No devices were selected", status_code=422)
    return action, eligible


def missing_plugin_roles(db: Session, plugin_id: str, device_type_id: str | None) -> list[str]:
    """Semantic roles a type would have to provide before a plugin is useful.

    Shown before an assignment is enabled, so an administrator finds out that
    Reboot needs a username and a password while they are configuring it rather
    than when somebody presses the button.
    """
    from .plugin_host import registry

    manifest = registry.manifest(plugin_id)
    if manifest is None:
        return []
    roles = set(role_map(field for field in get_effective_fields(db, device_type_id) if field.visible))
    missing: list[str] = [role for role in manifest.get("required_output_roles", []) if role not in roles]
    optional = set(manifest.get("optional_output_roles", []))
    minimum = int(manifest.get("minimum_output_roles", 0))
    if len((set(manifest.get("required_output_roles", [])) | optional) & roles) < minimum:
        missing.append("one of: " + " or ".join(sorted(optional)))
    for action in manifest.get("actions", []):
        if action.get("entity") != "devices":
            continue
        missing.extend(role for role in action.get("required_roles", []) if role not in roles)
        for group in _role_groups(action):
            if not any(role in roles for role in group):
                missing.append(" or ".join(group))
    return sorted(set(missing))


def plugin_payload_for(
    device: Device, fields: tuple[EffectiveField, ...], action: dict,
    *, output_roles: Iterable[str] = (),
) -> dict:
    """The slice of a device one action is allowed to see.

    A plugin receives its declared roles and the identity it needs to report
    results back, not the whole document. Sensitive values reach a plugin only
    when the action names the role that holds them.

    An action may set `include_document: non_sensitive` to receive the visible
    document as well — the research agent needs the inventory record in order
    to identify hardware from it — and even then no sensitive value is added
    that the action did not already ask for by role.
    """
    roles = role_map(fields)
    wanted: set[str] = set(action.get("required_roles", []))
    for group in _role_groups(action):
        wanted.update(group)
    wanted.update(action.get("optional_roles", []))
    # Snapshot the manifest-authorized outputs before research starts so the
    # callback can distinguish an existing value from a concurrent edit.
    wanted.update(
        role for role in output_roles
        if role in roles and roles[role].visible
    )
    granted = {role: _value_for_role(device, roles[role]) for role in wanted if role in roles}
    payload = {
        "id": device.id,
        "unique_id": device.unique_id,
        "device_type": device.device_type_key,
        "device_type_label": device.device_type_label,
        "online_status": device.online_status,
        "_plugin_roles": granted,
        "_scan_addresses": {
            roles[role].key: granted[role]
            for role in ("scan_address_wan", "scan_address_lan")
            if role in granted
        },
    }
    if action.get("include_document") == "non_sensitive":
        for field in fields:
            if field.visible and not field.sensitive and field.storage in {"data", "column"}:
                payload.setdefault(field.key, _value_for_role(device, field))
    # Non-sensitive values the action asked for by key rather than by role.
    for key in action.get("fields", []):
        field = next((item for item in fields if item.key == key), None)
        if field and not field.sensitive:
            payload[key] = _value_for_role(device, field)
    return payload


# ------------------------------------------------------------- presentation

def field_payload(field: EffectiveField) -> dict:
    """One effective field, in the shape the frontend renderers consume."""
    return {
        "key": field.key,
        "label": field.label,
        "type": field.field_type,
        "required": field.required,
        "visible": field.visible,
        "list_visible": field.list_visible,
        "sensitive": field.sensitive,
        "writable": field.writable,
        "storage": "data" if field.stored else field.storage,
        "options": list(field.options),
        "description": field.description,
        "role": field.role,
        "indexed": field.indexed,
        "unique": field.unique_value,
        "opens_web_page": field.opens_web_page,
        "link_scheme": field.link_scheme,
        "link_port": field.link_port,
        "validation": field.validation,
        "default": field.default_value,
        "position": field.position,
        "scope": field.scope,
        "protected": field.protected,
        "configuration_source": field.configuration_source,
        "database_storage": field.storage,
    }


def definition_payload(definition: DeviceFieldDefinition, usage: dict | None = None) -> dict:
    return {
        "id": definition.id,
        "key": definition.key,
        "label": definition.label,
        "field_type": definition.field_type,
        "description": definition.description,
        "options": list(definition.options or []),
        "validation": definition.validation or {},
        "default_value": definition.default_value,
        "sensitive": definition.sensitive,
        "indexed": definition.indexed,
        "unique_value": definition.unique_value,
        "opens_web_page": definition.opens_web_page,
        "link_scheme": definition.link_scheme or "http",
        "link_port": definition.link_port,
        "plugin_role": definition.plugin_role,
        "protected_system_field": definition.protected_system_field,
        "enabled": definition.enabled,
        "configuration_source": definition.configuration_source,
        "storage": storage_for(definition.key),
        "usage": usage or {},
    }


def assignment_payload(assignment: DeviceFieldAssignment, key: str) -> dict:
    return {
        "id": assignment.id,
        "field_key": key,
        "field_definition_id": assignment.field_definition_id,
        "device_type_id": assignment.device_type_id,
        "visible": assignment.visible,
        "list_visible": assignment.list_visible,
        "required": assignment.required,
        "writable": assignment.writable,
        "position": assignment.position,
        "label_override": assignment.label_override,
        "description_override": assignment.description_override,
        "validation_override": assignment.validation_override,
        "excluded": assignment.excluded,
        "configuration_source": assignment.configuration_source,
    }


def schema_payload(db: Session, device_type: DeviceType | None) -> dict:
    """The published schema one page renders itself from."""
    fields = get_effective_fields(db, device_type.id if device_type else None)
    return {
        "revision": current_revision(db),
        "device_type": None if device_type is None else {
            "id": device_type.id,
            "key": device_type.key,
            "label": device_type.label,
            "description": device_type.description,
            "configuration_source": device_type.configuration_source,
        },
        "fields": [field_payload(field) for field in fields],
        "plugins": sorted(get_allowed_plugins(db, device_type.id if device_type else None)),
        "allow_global_exclusions": settings.device_schema_allow_global_exclusions,
    }


# ------------------------------------------------------------------ seeding

def _definition_from_entity_field(field: EntityField) -> DeviceFieldDefinition:
    return DeviceFieldDefinition(
        key=field.key,
        label=field.label,
        field_type=field.field_type,
        description=field.description,
        options=list(field.options or []),
        validation={},
        sensitive=field.sensitive,
        indexed=field.indexed,
        unique_value=field.unique_value,
        # An address the fleet reaches the device on is a web page often enough
        # that linking it is the useful default. Any other field starts off
        # unlinked and is opted in from the schema editor.
        opens_web_page=field.role in WEB_ADDRESS_ROLES,
        # Plain http by default, whatever the field: a device serving only
        # https almost always redirects from 80, while https against a
        # self-signed certificate warns even when the device is fine.
        link_scheme="http",
        link_port=None,
        plugin_role=field.role,
        protected_system_field=field.key in PROTECTED_SYSTEM_FIELDS,
        enabled=True,
        configuration_source="system",
    )


def seed_device_schema(db: Session) -> None:
    """Establish the device schema on an empty database, and keep it current.

    On a fresh installation the frozen entity-field catalog — which is what
    `TB_ENTITY_FIELDS_JSON` and the Helm `config.entityFields` values produce —
    is converted once into reusable definitions plus global assignments, so an
    installation's existing choice of columns carries straight over.

    On an installation that already has a schema this only adds definitions the
    application has learned to ship since, hidden, so an upgrade never changes
    a working layout on its own.
    """
    from .entity_fields import DEFAULT_FIELDS, ON_DEMAND_DEVICE_FIELDS, get_entity_fields

    first_run = not db.scalar(select(func.count()).select_from(DeviceFieldDefinition))
    existing = {definition.key: definition for definition in db.scalars(select(DeviceFieldDefinition))}

    if first_run:
        catalog = get_entity_fields(db, "devices")
        if not catalog:
            # No frozen catalog to convert (a database created before the
            # entity-field tables, or a test fixture): fall back to the
            # application's own defaults.
            catalog = [
                EntityField(entity="devices", position=position, **spec)
                for position, spec in enumerate(deepcopy(DEFAULT_FIELDS["devices"]))
                if spec["key"] not in ON_DEMAND_DEVICE_FIELDS
            ]
        for position, field in enumerate(catalog):
            definition = _definition_from_entity_field(field)
            db.add(definition)
            db.flush()
            db.add(DeviceFieldAssignment(
                field_definition_id=definition.id,
                device_type_id=None,
                visible=field.visible,
                required=field.required,
                writable=field.writable,
                position=position * 10,
                configuration_source="system",
            ))
            existing[definition.key] = definition
    else:
        catalog_position = db.scalar(select(func.max(DeviceFieldAssignment.position)).where(
            DeviceFieldAssignment.device_type_id.is_(None)
        )) or 0
        for spec in DEFAULT_FIELDS["devices"]:
            if spec["key"] in ON_DEMAND_DEVICE_FIELDS:
                continue
            if spec["key"] in existing:
                continue
            candidate = deepcopy(spec)
            candidate.pop("storage", None)
            candidate.pop("visible", None)
            candidate.pop("required", None)
            definition = DeviceFieldDefinition(
                key=candidate["key"], label=candidate["label"], field_type=candidate["field_type"],
                description=candidate.get("description"), options=list(candidate.get("options") or []),
                validation={}, sensitive=candidate.get("sensitive", False),
                indexed=candidate.get("indexed", False), unique_value=candidate.get("unique_value", False),
                opens_web_page=candidate.get("role") in WEB_ADDRESS_ROLES,
                link_scheme="http", link_port=None,
                plugin_role=candidate.get("role"),
                protected_system_field=candidate["key"] in PROTECTED_SYSTEM_FIELDS,
                enabled=True, configuration_source="system",
            )
            db.add(definition)
            db.flush()
            catalog_position += 10
            # Hidden: an upgrade that shipped a new field must not rearrange a
            # layout somebody is already using.
            db.add(DeviceFieldAssignment(
                field_definition_id=definition.id, device_type_id=None, visible=False,
                required=False, writable=candidate.get("writable", True),
                position=catalog_position, configuration_source="system",
            ))
            existing[definition.key] = definition

    for key in PROTECTED_SYSTEM_FIELDS:
        definition = existing.get(key)
        if definition is not None and not definition.protected_system_field:
            definition.protected_system_field = True

    if first_run or not current_revision(db):
        publish_revision(db, source="system", note="Initial device schema",
                         summary={"fields": len(existing)})
    db.commit()
    invalidate_cache()


def refresh_managed_indexes(db: Session) -> None:
    """Plan and apply the catalog's indexes. Safe to call repeatedly."""
    plan_field_indexes(db)
    db.commit()
    apply_field_indexes(db)


# ------------------------------------------------------- publish-time checks

def resolve_uncached(db: Session, device_type_id: str | None) -> tuple[EffectiveField, ...]:
    """Resolve without touching the cache.

    Used while analysing an unpublished change: the change is applied inside a
    transaction that is about to be rolled back, and caching what it resolves
    to would leave everyone else reading a schema that never existed.
    """
    return _resolve(db, device_type_id)


# How many device rows a type-compatibility sample reads. A publish check is
# meant to be quick and indicative; the write path is what actually enforces.
ANALYSIS_SAMPLE = 500


def analyze_schema(db: Session, *, uncached: bool = False) -> dict:
    """What the current configuration would do to the devices already stored.

    Reported before a publish so nobody discovers that a newly required field
    is empty on two hundred devices by watching their next save fail. Errors
    block a publish; warnings are things an administrator should see and may
    reasonably accept.
    """
    resolver = resolve_uncached if uncached else get_effective_fields
    errors: list[dict] = []
    warnings: list[dict] = []

    scopes: list[tuple[DeviceType | None, tuple[EffectiveField, ...]]] = [(None, resolver(db, None))]
    for item in db.scalars(select(DeviceType).order_by(DeviceType.position)):
        scopes.append((item, resolver(db, item.id)))

    for device_type, fields in scopes:
        label = device_type.label if device_type else "Uncategorized"
        clause = (
            Device.device_type_id == device_type.id if device_type
            else Device.device_type_id.is_(None)
        )
        for field in fields:
            if not field.stored:
                continue
            if field.required:
                missing = db.scalar(
                    select(func.count()).select_from(Device).where(
                        clause,
                        (Device._data[field.key].astext.is_(None)) | (Device._data[field.key].astext == ""),
                    )
                ) or 0
                if missing:
                    errors.append({
                        "kind": "missing_required", "device_type": label, "field": field.key,
                        "count": missing,
                        "message": f"{missing} {label} device(s) have no {field.label}",
                    })
            sample = db.execute(
                select(Device.unique_id, Device._data[field.key].astext)
                .where(clause, Device._data[field.key].astext.isnot(None))
                .limit(ANALYSIS_SAMPLE)
            ).all()
            incompatible = []
            for unique_id, raw in sample:
                if raw is None or raw == "":
                    continue
                try:
                    coerced = _coerce(field, raw)
                    _check_rules(field, coerced)
                except (DeviceValidationError, SchemaError):
                    incompatible.append(unique_id)
            if incompatible:
                errors.append({
                    "kind": "incompatible_value", "device_type": label, "field": field.key,
                    "count": len(incompatible), "examples": incompatible[:5],
                    "message": (
                        f"{len(incompatible)} {label} device(s) hold a {field.label} value that is not a "
                        f"valid {field.field_type}"
                    ),
                })

    for definition in db.scalars(select(DeviceFieldDefinition).where(
        DeviceFieldDefinition.enabled.is_(True), DeviceFieldDefinition.unique_value.is_(True)
    )):
        if storage_for(definition.key) != "data":
            continue
        duplicates = db.execute(
            select(Device._data[definition.key].astext, func.count())
            .where(Device._data[definition.key].astext.isnot(None),
                   Device._data[definition.key].astext != "")
            .group_by(Device._data[definition.key].astext)
            .having(func.count() > 1)
            .limit(10)
        ).all()
        if duplicates:
            errors.append({
                "kind": "duplicate_value", "field": definition.key,
                "count": len(duplicates), "examples": [value for value, _ in duplicates[:5]],
                "message": (
                    f"{definition.label} is marked unique but {len(duplicates)} value(s) appear on more "
                    "than one device"
                ),
            })

    for device_type, fields in scopes:
        if device_type is None:
            continue
        for plugin_id in get_allowed_plugins(db, device_type.id):
            missing = missing_plugin_roles(db, plugin_id, device_type.id)
            if missing:
                warnings.append({
                    "kind": "plugin_missing_roles", "device_type": device_type.label,
                    "plugin_id": plugin_id, "roles": missing,
                    "message": (
                        f"{plugin_id} is enabled for {device_type.label} but the type provides no field for: "
                        + ", ".join(missing)
                    ),
                })

    for key in LOCKED_VISIBLE:
        definition = db.scalar(select(DeviceFieldDefinition).where(DeviceFieldDefinition.key == key))
        if definition is None or not definition.enabled:
            errors.append({
                "kind": "missing_system_field", "field": key,
                "message": f"{key} is required by the application and cannot be disabled",
            })

    for row in db.scalars(select(DeviceFieldIndex).where(DeviceFieldIndex.state == "failed")):
        warnings.append({
            "kind": "index_failed", "field": row.field_key, "index": row.name,
            "message": f"The managed index on {row.field_key} could not be applied: {row.error}",
        })

    return {"errors": errors, "warnings": warnings, "ok": not errors}


REDACTED = "***"


def redact_sensitive(db: Session, device_type_id: str | None, document: dict) -> dict:
    """A copy of a document with secret values masked.

    Used wherever a document is recorded rather than acted on — audit entries
    above all. An audit trail should say that the password changed; it should
    not be a second place the password is stored, readable by everyone who can
    read the log. Presence is preserved, because "a credential was configured"
    is exactly what the entry is recording.
    """
    fields = effective_field_map(db, device_type_id)
    return {
        key: (REDACTED if fields.get(key) is not None and fields[key].sensitive and value not in (None, "") else value)
        for key, value in (document or {}).items()
    }
