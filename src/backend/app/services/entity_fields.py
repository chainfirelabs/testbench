import json
import logging
import re
from datetime import date
from hashlib import sha1
from copy import deepcopy

from sqlalchemy import Boolean, Numeric, cast, func, select, text
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import EntityField

logger = logging.getLogger(__name__)

ENTITIES = ("devices", "software", "tests", "vendor_devices")
FIELD_TYPES = {"text", "password", "textarea", "number", "boolean", "date", "select", "json"}
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
EXTRA_FIELDS_KEY = "__extra_fields"


def _field(
    key: str,
    label: str,
    field_type: str = "text",
    *,
    required: bool = False,
    writable: bool = True,
    storage: str = "column",
    options: list[str] | None = None,
    description: str | None = None,
    role: str | None = None,
    indexed: bool = False,
    unique_value: bool = False,
    visible: bool = True,
    sensitive: bool = False,
) -> dict:
    return {
        "key": key,
        "label": label,
        "field_type": field_type,
        "required": required,
        "writable": writable,
        "storage": storage,
        "options": options or [],
        "description": description,
        "role": role,
        "indexed": indexed,
        "unique_value": unique_value,
        "visible": visible,
        "sensitive": sensitive,
    }


DEFAULT_FIELDS: dict[str, list[dict]] = {
    "devices": [
        _field("unique_id", "Unique ID", required=True, role="identifier", indexed=True, unique_value=True),
        _field("status", "Status", "select", required=True, role="status", indexed=True,
               options=["available", "checked_out", "inventory", "missing", "broken"]),
        _field("location", "Location", indexed=True),
        _field("make", "Make"),
        _field("model", "Model"),
        _field("serial_number", "Serial Number", indexed=True),
        _field("imei", "IMEI", indexed=True),
        _field("firmware_version", "Firmware Version", role="discovery_firmware"),
        _field("hardware_version", "Hardware Version", role="discovery_hardware"),
        _field("architecture", "Architecture", "select",
               options=["x86_64", "x86", "mipsbe", "mipsel", "arm", "arm64", "aarch64", "ppc", "tilegx", "lexra_mips"]),
        _field("wan_ip", "WAN IP", role="scan_address_wan"),
        _field("lan_ip", "LAN IP", role="scan_address_lan"),
        _field("wan_mac", "WAN MAC", role="discovery_wan_mac", indexed=True),
        _field("lan_mac", "LAN MAC", role="discovery_lan_mac", indexed=True),
        _field("username", "Username", storage="data", role="device_username",
               description="Username used by device automation plugins."),
        _field("password", "Password", "text", storage="data", role="device_password",
               sensitive=True, description="Device password used by automation plugins; stored as plaintext in PostgreSQL."),
        _field("online_status", "Online", "boolean", writable=False, role="scan_state"),
        _field("last_seen_online", "Last Seen", writable=False, role="last_seen"),
        _field("checked_out_by_username", "Checked Out By", writable=False),
        _field("checked_out_at", "Checked Out At", "date", writable=False, role="checkout_started"),
        _field("checkout_due", "Due Back", "date", indexed=True, role="checkout_due"),
        _field("checkout_purpose", "Checkout Purpose", "textarea", role="checkout_purpose"),
        _field("misc_data", "Misc Data", "json"),
    ],
    "software": [
        _field("name", "Name", required=True, role="identifier", indexed=True),
        _field("version", "Version", required=True, role="version", indexed=True),
        _field("vendor_device_count", "Vendor Devices", "number", writable=False),
        _field("version_count", "Versions", "number", writable=False),
        _field("misc_data", "Misc Data", "json"),
    ],
    "tests": [
        _field("device_unique_id", "Device", required=True),
        _field("software_name", "Software", required=True),
        _field("software_version", "Software Version"),
        _field("component_name", "Component"),
        _field("component_version", "Component Version"),
        _field("outcome", "Outcome", "select", required=True, role="outcome", indexed=True, options=["pass", "fail", "warn"]),
        _field("tag", "Tag", "select", options=["adhoc", "acceptance", "end-to-end", "automated"]),
        _field("run_at", "Run At", "date", role="run_date", indexed=True),
        _field("notes", "Notes", "textarea"),
        _field("misc_data", "Misc Data", "json"),
        _field("created_at", "Created", writable=False),
        _field("created_by_username", "Created By", writable=False),
    ],
    "vendor_devices": [
        _field("make", "Make"),
        _field("model", "Model"),
        _field("firmware_version", "Firmware Version"),
        _field("hardware_version", "Hardware Version"),
        _field("architecture", "Architecture"),
        _field("support_status", "Support", "select", required=True,
               options=["supported", "partial", "unsupported", "planned"]),
        _field("source", "Source"),
        _field("notes", "Notes", "textarea"),
        _field("misc_data", "Misc Data", "json"),
    ],
}

# Automation definitions are created from plugin manifests on first enablement.
# IMEI and Architecture are explicit opt-ins. Neither group belongs in the
# catalog of an installation that has not asked for it.
ON_DEMAND_DEVICE_FIELDS = {
    "imei", "architecture",
    "firmware_version", "hardware_version", "wan_ip", "lan_ip", "wan_mac", "lan_mac",
    "username", "password", "online_status", "last_seen_online", "last_scanned_at",
}

BUILTINS = {
    entity: {field["key"]: field for field in fields}
    for entity, fields in DEFAULT_FIELDS.items()
}
REQUIRED_FIELDS = {
    "devices": ("unique_id",),
    "software": ("name",),
    "tests": ("device_unique_id", "software_name", "outcome"),
    "vendor_devices": (),
}


def _configured_fields() -> dict[str, list[dict]]:
    if not settings.entity_fields_json.strip():
        raw = {}
    else:
        try:
            raw = json.loads(settings.entity_fields_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"TB_ENTITY_FIELDS_JSON is invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(
            "TB_ENTITY_FIELDS_JSON must be an object keyed by devices, software, tests and vendor_devices"
        )
    unknown_entities = sorted(set(raw) - set(ENTITIES))
    if unknown_entities:
        raise RuntimeError(f"TB_ENTITY_FIELDS_JSON has unknown entities: {', '.join(unknown_entities)}")

    result: dict[str, list[dict]] = {}
    for entity in ENTITIES:
        section = raw.get(entity, {})
        if not isinstance(section, dict):
            raise RuntimeError(
                f"TB_ENTITY_FIELDS_JSON.{entity} must be an object with optional_fields and custom_fields lists; "
                "required fields are added automatically"
            )
        unknown_sections = sorted(set(section) - {"optional_fields", "custom_fields", "builtins", "custom"})
        if unknown_sections:
            raise RuntimeError(
                f"TB_ENTITY_FIELDS_JSON.{entity} has unknown sections: {', '.join(unknown_sections)}"
            )
        if "optional_fields" in section and "builtins" in section:
            raise RuntimeError(f"TB_ENTITY_FIELDS_JSON.{entity} cannot contain both optional_fields and builtins")
        if "custom_fields" in section and "custom" in section:
            raise RuntimeError(f"TB_ENTITY_FIELDS_JSON.{entity} cannot contain both custom_fields and custom")
        builtins = section.get("optional_fields", section.get("builtins", []))
        custom = section.get("custom_fields", section.get("custom", []))
        if not isinstance(builtins, list) or not all(isinstance(key, str) for key in builtins):
            raise RuntimeError(f"TB_ENTITY_FIELDS_JSON.{entity}.optional_fields must be a list of field names")
        if not isinstance(custom, list) or not all(isinstance(spec, dict) for spec in custom):
            raise RuntimeError(f"TB_ENTITY_FIELDS_JSON.{entity}.custom_fields must be a list of field objects")

        fields = []
        seen = set(REQUIRED_FIELDS[entity])
        selected = set(REQUIRED_FIELDS[entity])
        # These fields predate the configurable catalog. Preserve the existing
        # vendor-device form and table on both new installs and upgrades.
        if entity == "vendor_devices" and not section:
            selected.update(BUILTINS[entity])
        ordered_selected = []
        for key in builtins:
            key = key.strip()
            if key in REQUIRED_FIELDS[entity]:
                raise RuntimeError(
                    f"{entity}.{key} is required and added automatically; do not list it under optional_fields"
                )
            if key not in BUILTINS[entity]:
                raise RuntimeError(
                    f"Unknown optional {entity} field {key!r}. To create it, place a field object under custom_fields"
                )
            if key in seen:
                raise RuntimeError(f"Duplicate built-in {entity} field: {key!r}")
            seen.add(key)
            selected.add(key)
            ordered_selected.append(key)

        # Every predefined field is a database catalog entry. Configuration
        # controls presentation and validation, not whether the field exists.
        ordered_keys = [*REQUIRED_FIELDS[entity], *ordered_selected]
        ordered_keys.extend(
            key for key in BUILTINS[entity]
            if key not in selected and not (entity == "devices" and key in ON_DEMAND_DEVICE_FIELDS)
        )
        for key in ordered_keys:
            field = deepcopy(BUILTINS[entity][key])
            field["visible"] = key in selected
            if not field["visible"]:
                field["required"] = False
            fields.append(field)

        for entry in custom:
            spec = dict(entry)
            if "unique" in spec and "unique_value" not in spec:
                spec["unique_value"] = spec["unique"]
            key = str(spec.get("key", "")).strip()
            if entity == "vendor_devices" and key == "vendor":
                raise RuntimeError("vendor_devices.vendor was removed; the software name identifies the vendor")
            if not KEY_RE.fullmatch(key) or key in seen:
                raise RuntimeError(f"Invalid or duplicate custom {entity} field key: {key!r}")
            if key in BUILTINS[entity]:
                raise RuntimeError(
                    f"{entity}.{key} is a predefined optional field; place it under optional_fields instead of custom_fields"
                )
            seen.add(key)
            base = _field(key, key.replace("_", " ").title(), storage="data")
            for option in ("label", "field_type", "required", "options", "description", "role", "indexed", "unique_value"):
                if option in spec:
                    base[option] = spec[option]
            if base["field_type"] not in FIELD_TYPES:
                raise RuntimeError(f"Unsupported field type for {entity}.{key}: {base['field_type']}")
            if base["field_type"] == "password":
                raise RuntimeError(
                    f"Custom password fields are not supported; use the predefined devices.password field"
                )
            for flag in ("required", "indexed", "unique_value"):
                if not isinstance(base[flag], bool):
                    raise RuntimeError(f"{entity}.{key}.{flag} must be true or false")
            if not isinstance(base["options"], list) or not all(isinstance(value, str) for value in base["options"]):
                raise RuntimeError(f"{entity}.{key}.options must be a list of strings")
            if base["unique_value"]:
                base["indexed"] = True
            base["visible"] = True
            fields.append(base)
        if entity == "devices" and "status" in selected and not {"checkout_purpose", "checkout_due"}.issubset(selected):
            status = next(field for field in fields if field["key"] == "status")
            status["options"] = [option for option in status["options"] if option != "checked_out"]
        result[entity] = fields
    return result


def initialize_entity_fields() -> None:
    """Freeze the configured catalog the first time this database starts."""
    db = SessionLocal()
    try:
        if (db.scalar(select(func.count()).select_from(EntityField)) or 0) > 0:
            # Upgrades can introduce new predefined fields. Add them hidden so
            # an existing installation's UI does not change unexpectedly.
            existing = {(f.entity, f.key) for f in db.scalars(select(EntityField)).all()}
            for entity, defaults in DEFAULT_FIELDS.items():
                position = max((f.position for f in get_entity_fields(db, entity)), default=-1) + 1
                for spec in defaults:
                    if entity == "devices" and spec["key"] in ON_DEMAND_DEVICE_FIELDS:
                        continue
                    if (entity, spec["key"]) in existing:
                        continue
                    candidate = deepcopy(spec)
                    if entity == "vendor_devices":
                        candidate.update(visible=True)
                    else:
                        candidate.update(visible=False, required=False)
                    db.add(EntityField(entity=entity, position=position, **candidate))
                    position += 1
            db.commit()
            ensure_entity_field_indexes(db)
            return
        configured = _configured_fields()
        for entity, fields in configured.items():
            for position, spec in enumerate(fields):
                db.add(EntityField(entity=entity, position=position, **spec))
        db.commit()
        ensure_entity_field_indexes(db)
        for entity, fields in configured.items():
            required = [field["key"] for field in fields if field["key"] in REQUIRED_FIELDS[entity]]
            builtins = [
                field["key"] for field in fields
                if field["key"] in BUILTINS[entity] and field["key"] not in REQUIRED_FIELDS[entity]
            ]
            custom = [field["key"] for field in fields if field["storage"] == "data"]
            logger.info(
                "Initialized %s fields — required: %s; optional predefined: %s; custom JSON: %s",
                entity,
                ", ".join(required) or "none",
                ", ".join(builtins) or "none",
                ", ".join(custom) or "none",
            )
    finally:
        db.close()


def _index_expression(field: EntityField) -> str:
    value = f"data->>'{field.key}'"
    if field.field_type == "number":
        return f"(NULLIF({value}, '')::numeric)"
    if field.field_type == "boolean":
        return f"(NULLIF({value}, '')::boolean)"
    if field.field_type == "date":
        # Values are validated and stored as ISO YYYY-MM-DD, whose text order
        # is chronological. The PostgreSQL date cast is not IMMUTABLE and
        # therefore cannot appear in an expression index.
        return f"({value})"
    if field.field_type == "json":
        return f"(data->'{field.key}')"
    return f"({value})"


def drop_entity_field_indexes(db: Session, field: EntityField) -> None:
    """Remove managed indexes for a field before it changes or is deleted."""
    suffix = sha1(f"{field.entity}:{field.key}:{field.field_type}".encode()).hexdigest()[:10]
    for unique_suffix in ("", "_uq"):
        db.execute(text(f'DROP INDEX IF EXISTS "ix_{field.entity}_field_{suffix}{unique_suffix}"'))


def ensure_entity_field_indexes(db: Session) -> None:
    """Create typed expression indexes requested by the frozen catalog.

    Devices are absent on purpose: their indexes are planned and applied from
    the published device schema (services/device_schema.py), which knows about
    fields this catalog never had.
    """
    for entity in ("software", "tests", "vendor_devices"):
        for field in get_entity_fields(db, entity):
            if not (field.indexed or field.unique_value):
                continue
            suffix = sha1(f"{entity}:{field.key}:{field.field_type}".encode()).hexdigest()[:10]
            name = f"ix_{entity}_field_{suffix}{'_uq' if field.unique_value else ''}"
            unique = "UNIQUE " if field.unique_value else ""
            if entity == "vendor_devices":
                value = f"misc_data->>'{field.key}'"
                expression = f"({value})"
                columns = f"software_id, {expression}" if field.unique_value else expression
                db.execute(text(
                    f'CREATE {unique}INDEX IF NOT EXISTS "{name}" '
                    f'ON vendor_devices ({columns})'
                ))
            else:
                db.execute(text(
                    f'CREATE {unique}INDEX IF NOT EXISTS "{name}" '
                    f'ON "{entity}" ({_index_expression(field)})'
                ))
    # Software identity plus version is a pair rather than two independently
    # unique values. Roles allow installations to rename either field.
    software = get_entity_fields(db, "software")
    identifier = next((field for field in software if field.role == "identifier"), None)
    version = next((field for field in software if field.role == "version"), None)
    if identifier and version:
        db.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_software_role_identity_version" '
            f'ON software (lower(data->>\'{identifier.key}\'), (data->>\'{version.key}\'))'
        ))
    db.commit()


def get_entity_fields(db: Session, entity: str, *, writable: bool | None = None,
                      visible: bool | None = None) -> list[EntityField]:
    q = select(EntityField).where(EntityField.entity == entity)
    if writable is not None:
        q = q.where(EntityField.writable == writable)
    if visible is not None:
        q = q.where(EntityField.visible == visible)
    return list(db.scalars(q.order_by(EntityField.position)).all())


def entity_field_expression(model, field: EntityField):
    """Typed SQL expression for one catalog field in an entity JSON document."""
    raw = model._data[field.key].astext
    if field.field_type == "number":
        return cast(func.nullif(raw, ""), Numeric)
    if field.field_type == "boolean":
        return cast(func.nullif(raw, ""), Boolean)
    if field.field_type == "date":
        # Validated ISO dates have chronological text order and match the
        # immutable expression used by their index.
        return raw
    return raw


def entity_field_map(db: Session, entity: str) -> dict[str, EntityField]:
    return {field.key: field for field in get_entity_fields(db, entity)}


def coerce_query_value(field: EntityField, value):
    if field.field_type == "boolean" and isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if field.field_type == "number" and isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value
    return value


def entity_order_by(db: Session, entity: str, model, sort: str, order: str):
    fields = entity_field_map(db, entity)
    if sort in {"id", "created_at", "updated_at"}:
        expression = getattr(model, sort)
    elif sort in fields and field_database_storage(fields[sort]) == "json":
        expression = entity_field_expression(model, fields[sort])
    else:
        expression = model.created_at
    return expression.desc() if order == "desc" else expression.asc()


def field_database_storage(field: EntityField) -> str:
    if field.key in {"created_at", "updated_at"}:
        return "column"
    elif field.key in {"device_unique_id", "software_name", "component_name", "component_version", "created_by_username", "checked_out_by_username"}:
        return "relationship"
    elif field.key in {"vendor_device_count", "version_count"}:
        return "derived"
    elif field.storage == "column":
        return "column"
    return "json"


def field_payload(field: EntityField) -> dict:
    return {
        "id": field.id,
        "entity": field.entity,
        "key": field.key,
        "label": field.label,
        "type": field.field_type,
        "required": field.required,
        "visible": field.visible,
        "list_visible": field.list_visible,
        "sensitive": field.sensitive,
        "writable": field.writable,
        "storage": field.storage,
        "options": field.options or [],
        "description": field.description,
        "role": field.role,
        "indexed": field.indexed,
        "unique": field.unique_value,
        "database_storage": field_database_storage(field),
        "protected": field.key in REQUIRED_FIELDS[field.entity] or field.storage != "data",
        "list_visibility_locked": field.key in REQUIRED_FIELDS[field.entity],
        "position": field.position,
        "configuration_source": field.configuration_source,
    }


def project_fields(row: dict, fields: list[EntityField], data_key: str) -> dict:
    """Flatten configured JSON-backed values beside physical fields."""
    data = row.get(data_key) if isinstance(row.get(data_key), dict) else {}
    return {
        field.key: data.get(field.key) if field.storage == "data" else row.get(field.key)
        for field in fields
    }


def merge_extra_columns(row: dict, known: set[str], data_key: str) -> dict:
    """Move non-empty unknown CSV columns into an entity's JSON store."""
    standard = {key: value for key, value in row.items() if key in known}
    extra = {key: value for key, value in row.items() if key not in known and value is not None}
    if not extra:
        return standard
    existing = standard.get(data_key)
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError(f"{data_key} must be a JSON object when custom CSV columns are present")
    conflicts = sorted(set(existing) & set(extra))
    if conflicts:
        raise ValueError(
            f"custom CSV columns duplicate keys already in {data_key}: {', '.join(conflicts)}"
        )
    marked = set(existing.get(EXTRA_FIELDS_KEY, [])) if isinstance(existing.get(EXTRA_FIELDS_KEY), list) else set()
    standard[data_key] = {
        **existing,
        **extra,
        EXTRA_FIELDS_KEY: sorted(marked | set(extra)),
    }
    return standard


def validate_custom_values(
    db: Session,
    entity: str,
    payload: dict,
    data_key: str,
    *,
    partial: bool = False,
    field_overrides: dict[str, dict] | None = None,
) -> dict:
    """Validate and type JSON-backed catalog fields, returning a copied payload."""
    normalized = dict(payload)
    if partial and data_key not in normalized:
        return normalized
    document = dict(normalized.get(data_key) or {})
    for field in get_entity_fields(db, entity):
        if field.storage != "data":
            continue
        override = (field_overrides or {}).get(field.key, {})
        visible = override.get("visible", field.visible)
        required = override.get("required", field.required)
        present = field.key in document
        value = document.get(field.key)
        if visible and required and not partial and (not present or value is None or value == ""):
            raise ValueError(f"{field.label} is required")
        if not present or value is None or value == "":
            if present and not required:
                document.pop(field.key, None)
            continue
        try:
            if field.field_type == "number" and not isinstance(value, (int, float)):
                value = float(value) if "." in str(value) else int(value)
            elif field.field_type == "boolean" and not isinstance(value, bool):
                folded = str(value).strip().lower()
                if folded not in {"true", "false", "1", "0", "yes", "no"}:
                    raise ValueError()
                value = folded in {"true", "1", "yes"}
            elif field.field_type == "date":
                value = date.fromisoformat(str(value)).isoformat()
            elif field.field_type == "json" and not isinstance(value, dict):
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field.label} must be a valid {field.field_type}") from exc
        if field.field_type == "select" and field.options and value not in field.options:
            raise ValueError(f"{field.label} must be one of: {', '.join(field.options)}")
        document[field.key] = value
    normalized[data_key] = document
    return normalized
