"""Loading a versioned DeviceSchema document, and reconciling it.

The database is the normal runtime source of truth. This module exists so an
installation can instead declare its device schema in a file — a ConfigMap
mounted by Helm, most usefully — and have that file own some or all of the
configuration.

Ownership is always explicit. Objects managed by merge or authoritative
reconciliation are stamped `configuration_source = "yaml"`, so the admin API
refuses to edit them and the GUI shows them as owned elsewhere. Bootstrap is a
one-time seed: after the document has been applied, its objects are handed to
the GUI. Nothing silently overwrites anything: in `merge` mode a disagreement
is reported as a conflict rather than resolved by guessing which side is newer.
"""

from __future__ import annotations

import logging
from hashlib import sha256
from pathlib import Path
from typing import Any

from .plugin_configuration import validate_plugin_configuration

import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    DeviceFieldAssignment,
    DeviceFieldDefinition,
    DeviceSchemaRevision,
    DeviceType,
    DeviceTypePlugin,
    EntityField,
)
from .entity_fields import (
    BUILTINS as ENTITY_BUILTINS,
    FIELD_TYPES as ENTITY_FIELD_TYPES,
    ensure_entity_field_indexes,
)
from .device_schema import (
    FIELD_TYPES,
    KEY_RE,
    PROTECTED_SYSTEM_FIELDS,
    RESERVED_DOCUMENT_KEYS,
    TYPE_KEY_RE,
    invalidate_cache,
    publish_revision,
    refresh_managed_indexes,
)

logger = logging.getLogger(__name__)

API_VERSION = "testbench.chainfirelabs.com/v1"
KIND = "DeviceSchema"
MODES = ("bootstrap", "merge", "authoritative")

# What the last reconciliation did, in the shape an operator (or Flux, through
# the API) needs in order to tell "applied", "up to date" and "broken" apart.
_status: dict[str, Any] = {
    "mode": None,
    "configured": False,
    "state": "disabled",
    "generation": None,
    "applied_revision": None,
    "message": "No DeviceSchema document is configured",
    "errors": [],
    "conflicts": [],
}


def status() -> dict:
    return dict(_status)


class DocumentError(ValueError):
    """A DeviceSchema document that cannot be understood or applied."""


def _export_assignment(row: DeviceFieldAssignment, key: str) -> dict:
    item: dict[str, Any] = {"key": key}
    for source, target in (
        ("visible", "visible"), ("list_visible", "listVisible"),
        ("required", "required"), ("writable", "writable"),
        ("position", "position"), ("label_override", "label"),
        ("description_override", "description"),
        ("validation_override", "validation"),
    ):
        value = getattr(row, source)
        if value is not None:
            item[target] = value
    if row.excluded:
        item["excluded"] = True
    return item


def export_document(db: Session, name: str = "testbench") -> dict:
    """Serialize the live catalog into a portable bootstrap document.

    Protected system fields are recreated by every installation and therefore
    stay out of the export. Values and credentials are never part of schema
    tables, so this document contains configuration only.
    """
    definitions = list(db.scalars(select(DeviceFieldDefinition).where(
        DeviceFieldDefinition.enabled.is_(True)
    ).order_by(DeviceFieldDefinition.key)))
    portable = [item for item in definitions if not item.protected_system_field]
    # System definitions need not be redeclared, but their layout assignments
    # are portable: every destination seeds the same protected keys first.
    by_id = {item.id: item for item in definitions}

    fields = []
    for definition in portable:
        item: dict[str, Any] = {
            "key": definition.key,
            "label": definition.label,
            "type": definition.field_type,
        }
        for key, value in (
            ("description", definition.description),
            ("options", list(definition.options or [])),
            ("validation", definition.validation or {}),
            ("default", definition.default_value),
            ("role", definition.plugin_role),
        ):
            if value not in (None, [], {}):
                item[key] = value
        if definition.sensitive:
            item["sensitive"] = True
        if definition.indexed:
            item["indexed"] = True
        if definition.unique_value:
            item["unique"] = True
        fields.append(item)

    assignments = list(db.scalars(select(DeviceFieldAssignment).order_by(
        DeviceFieldAssignment.position, DeviceFieldAssignment.id
    )))
    global_rows = [row for row in assignments if row.device_type_id is None and row.field_definition_id in by_id]
    global_ids = {row.field_definition_id for row in global_rows}
    specific: dict[str, list[DeviceFieldAssignment]] = {}
    for row in assignments:
        if row.device_type_id is not None and row.field_definition_id in by_id:
            specific.setdefault(row.device_type_id, []).append(row)

    device_types = []
    for device_type in db.scalars(select(DeviceType).order_by(DeviceType.position, DeviceType.key)):
        entry: dict[str, Any] = {"key": device_type.key, "label": device_type.label}
        if device_type.description:
            entry["description"] = device_type.description
        if not device_type.enabled:
            entry["enabled"] = False
        entry["position"] = device_type.position
        own_fields, overrides = [], []
        for row in specific.get(device_type.id, []):
            exported = _export_assignment(row, by_id[row.field_definition_id].key)
            (overrides if row.field_definition_id in global_ids else own_fields).append(exported)
        if own_fields:
            entry["fields"] = own_fields
        if overrides:
            entry["overrides"] = overrides
        plugins = []
        for row in db.scalars(select(DeviceTypePlugin).where(
            DeviceTypePlugin.device_type_id == device_type.id,
            DeviceTypePlugin.enabled.is_(True),
        ).order_by(DeviceTypePlugin.plugin_id)):
            plugin = {"id": row.plugin_id, "enabled": True}
            if row.configuration:
                # Device ids belong to this inventory, not a portable schema.
                # Ordered type rules are schema and remain in the export.
                portable_configuration = {
                    key: value for key, value in row.configuration.items()
                    if key != "_device_overrides"
                }
                if portable_configuration:
                    plugin["config"] = portable_configuration
            plugins.append(plugin)
        if plugins:
            entry["plugins"] = plugins
        device_types.append(entry)

    vendor_device_fields = []
    for field in db.scalars(select(EntityField).where(
        EntityField.entity == "vendor_devices",
    ).order_by(EntityField.position)):
        item = {
            "key": field.key,
            "label": field.label,
            "type": field.field_type,
            "visible": field.visible,
            "listVisible": field.list_visible,
            "required": field.required,
        }
        for source, target in (
            ("description", "description"), ("options", "options"),
            ("sensitive", "sensitive"), ("indexed", "indexed"),
            ("unique_value", "unique"),
        ):
            value = getattr(field, source)
            if value not in (None, [], False):
                item[target] = value
        vendor_device_fields.append(item)

    return {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {"name": name},
        "spec": {
            "fields": fields,
            "globalFields": [_export_assignment(row, by_id[row.field_definition_id].key) for row in global_rows],
            "deviceTypes": device_types,
            "vendorDeviceFields": vendor_device_fields,
        },
    }


# ------------------------------------------------------------------ parsing

def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DocumentError(message)


def _string(value: Any, what: str) -> str:
    _require(isinstance(value, str) and value.strip() != "", f"{what} must be a non-empty string")
    return value.strip()


def parse_document(text: str) -> dict:
    """Validate a DeviceSchema document and return it in normalised form.

    ConfigMap contents are untrusted configuration: everything is checked here,
    before a single row is written, so an invalid document leaves the last
    published schema exactly as it was.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DocumentError(f"invalid YAML: {exc}") from exc
    _require(isinstance(raw, dict), "the document must be a mapping")
    _require(raw.get("apiVersion") == API_VERSION, f"apiVersion must be {API_VERSION}")
    _require(raw.get("kind") == KIND, f"kind must be {KIND}")
    spec = raw.get("spec") or {}
    _require(isinstance(spec, dict), "spec must be a mapping")

    vendor_device_fields: list[dict] = []
    seen_vendor_fields: set[str] = set()
    for position, entry in enumerate(spec.get("vendorDeviceFields") or []):
        _require(isinstance(entry, dict), "each spec.vendorDeviceFields entry must be a mapping")
        key = _string(entry.get("key"), "a vendor-device field key")
        _require(key != "vendor",
                 "vendor-device field 'vendor' was removed; the software name identifies the vendor")
        _require(bool(KEY_RE.fullmatch(key)),
                 f"vendor-device field key '{key}' must be lowercase letters, digits or underscores")
        _require(key not in seen_vendor_fields, f"vendor-device field '{key}' is declared twice")
        seen_vendor_fields.add(key)
        field_type = entry.get("type", "text")
        _require(field_type in ENTITY_FIELD_TYPES,
                 f"vendor-device field '{key}' has unsupported type '{field_type}'")
        builtin = ENTITY_BUILTINS["vendor_devices"].get(key)
        if builtin:
            _require(field_type == builtin["field_type"],
                     f"built-in vendor-device field '{key}' must keep type '{builtin['field_type']}'")
        options = entry.get("options") or []
        _require(isinstance(options, list) and all(isinstance(value, str) for value in options),
                 f"vendor-device field '{key}' options must be a list of strings")
        if field_type == "select":
            _require(bool(options), f"vendor-device select field '{key}' needs at least one option")
        visible = bool(entry.get("visible", True))
        required = bool(entry.get("required", False))
        _require(visible or not required,
                 f"vendor-device field '{key}' cannot be required while hidden")
        vendor_device_fields.append({
            "key": key,
            "label": entry.get("label") or key.replace("_", " ").title(),
            "field_type": field_type,
            "description": entry.get("description"),
            "options": options,
            "required": required,
            "visible": visible,
            "list_visible": bool(entry.get("listVisible", visible)),
            "sensitive": bool(entry.get("sensitive", False)),
            "indexed": bool(entry.get("indexed", False)) or bool(entry.get("unique", False)),
            "unique_value": bool(entry.get("unique", False)),
            "position": entry.get("position", position * 10),
        })
        _require(isinstance(vendor_device_fields[-1]["position"], int),
                 f"vendor-device field '{key}' position must be an integer")

    fields: list[dict] = []
    seen_fields: set[str] = set()
    for entry in spec.get("fields") or []:
        _require(isinstance(entry, dict), "each spec.fields entry must be a mapping")
        key = _string(entry.get("key"), "a field key")
        _require(bool(KEY_RE.fullmatch(key)), f"field key '{key}' must be lowercase letters, digits or underscores")
        _require(
            key not in RESERVED_DOCUMENT_KEYS or key in PROTECTED_SYSTEM_FIELDS,
            f"field key '{key}' is reserved by the device envelope",
        )
        _require(key not in seen_fields, f"field '{key}' is declared twice")
        seen_fields.add(key)
        field_type = entry.get("type", "text")
        _require(field_type in FIELD_TYPES, f"field '{key}' has unsupported type '{field_type}'")
        options = entry.get("options") or []
        _require(isinstance(options, list) and all(isinstance(o, str) for o in options),
                 f"field '{key}' options must be a list of strings")
        validation = entry.get("validation") or {}
        _require(isinstance(validation, dict), f"field '{key}' validation must be a mapping")
        fields.append({
            "key": key,
            "label": entry.get("label") or key.replace("_", " ").title(),
            "field_type": field_type,
            "description": entry.get("description"),
            "options": options,
            "validation": validation,
            "default_value": entry.get("default"),
            "sensitive": bool(entry.get("sensitive", False)),
            "indexed": bool(entry.get("indexed", False)) or bool(entry.get("unique", False)),
            "unique_value": bool(entry.get("unique", False)),
            "plugin_role": entry.get("role"),
        })

    def _assignments(entries: Any, what: str) -> list[dict]:
        result = []
        for entry in entries or []:
            if isinstance(entry, str):
                entry = {"key": entry}
            _require(isinstance(entry, dict), f"each {what} entry must be a mapping or a field key")
            key = _string(entry.get("key"), f"a {what} key")
            result.append({
                "key": key,
                "visible": entry.get("visible"),
                "list_visible": entry.get("listVisible"),
                "required": entry.get("required"),
                "writable": entry.get("writable"),
                "position": entry.get("position"),
                "label_override": entry.get("label"),
                "description_override": entry.get("description"),
                "validation_override": entry.get("validation"),
                "excluded": bool(entry.get("excluded", False)),
            })
        return result

    types: list[dict] = []
    seen_types: set[str] = set()
    for entry in spec.get("deviceTypes") or []:
        _require(isinstance(entry, dict), "each spec.deviceTypes entry must be a mapping")
        key = _string(entry.get("key"), "a device type key")
        _require(bool(TYPE_KEY_RE.fullmatch(key)),
                 f"device type key '{key}' must be lowercase letters, digits or hyphens")
        _require(key != "uncategorized", "'uncategorized' is reserved")
        _require(key not in seen_types, f"device type '{key}' is declared twice")
        seen_types.add(key)
        plugins = []
        for plugin in entry.get("plugins") or []:
            _require(isinstance(plugin, dict), f"device type '{key}' plugins must be mappings")
            try:
                validate_plugin_configuration(plugin.get("id"), plugin.get("config") if plugin.get("config") is not None else {})
            except ValueError as exc:
                raise DocumentError(str(exc)) from exc
            plugins.append({
                "plugin_id": _string(plugin.get("id"), "a plugin id"),
                "enabled": bool(plugin.get("enabled", True)),
                "configuration": plugin.get("config") or {},
            })
        types.append({
            "key": key,
            "label": entry.get("label") or key.replace("-", " ").title(),
            "description": entry.get("description"),
            "enabled": bool(entry.get("enabled", True)),
            "position": entry.get("position"),
            # `fields` adds the type's own fields; `overrides` changes inherited
            # global ones. They are the same operation, named for what an
            # administrator is doing.
            "assignments": _assignments(entry.get("fields"), "device type field")
                           + _assignments(entry.get("overrides"), "device type override"),
            "plugins": plugins,
        })

    unknown_field_refs = sorted(
        {item["key"] for item in _assignments(spec.get("globalFields"), "globalFields")}
        | {item["key"] for entry in types for item in entry["assignments"]}
    )
    declared = seen_fields | PROTECTED_SYSTEM_FIELDS
    missing = [key for key in unknown_field_refs if key not in declared]
    _require(not missing, "assigned but never defined: " + ", ".join(missing))

    return {
        "name": (raw.get("metadata") or {}).get("name") or "default",
        "prune": bool(spec.get("prune", False)),
        "fields": fields,
        "global_assignments": _assignments(spec.get("globalFields"), "globalFields"),
        "device_types": types,
        "vendor_device_fields": vendor_device_fields,
        "generation": sha256(text.encode()).hexdigest()[:16],
    }


def parse_uploaded_document(text: str) -> dict:
    """Parse either a DeviceSchema or the ConfigMap produced by export.

    Keeping ConfigMap unwrapping here makes the downloaded file directly
    round-trippable without teaching the reconciler about Kubernetes objects.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DocumentError(f"invalid YAML: {exc}") from exc
    if isinstance(raw, dict) and raw.get("apiVersion") == "v1" and raw.get("kind") == "ConfigMap":
        data = raw.get("data")
        _require(isinstance(data, dict), "ConfigMap data must be a mapping")
        embedded = data.get("device-schema.yaml")
        _require(isinstance(embedded, str) and embedded.strip(),
                 "ConfigMap data.device-schema.yaml must contain a DeviceSchema document")
        return parse_document(embedded)
    return parse_document(text)


def additive_import_plan(db: Session, document: dict) -> dict:
    """Describe the rows an additive GUI import would create.

    Identity is deliberately structural. Existing rows are skipped regardless
    of ownership or whether their attributes differ; the import never updates,
    enables, disables, or removes anything.
    """
    definitions = {row.key: row for row in db.scalars(select(DeviceFieldDefinition))}
    types = {row.key: row for row in db.scalars(select(DeviceType))}
    assignments = {
        (row.field_definition_id, row.device_type_id)
        for row in db.scalars(select(DeviceFieldAssignment))
    }
    plugins = {
        (row.device_type_id, row.plugin_id)
        for row in db.scalars(select(DeviceTypePlugin))
    }
    vendor_fields = {
        row.key for row in db.scalars(select(EntityField).where(
            EntityField.entity == "vendor_devices",
        ))
    }
    additions = {"fields": [], "device_types": [], "global_assignments": [],
                 "type_assignments": [], "plugins": [], "vendor_device_fields": []}
    skipped = {key: [] for key in additions}

    for spec in document["fields"]:
        (skipped if spec["key"] in definitions else additions)["fields"].append(spec["key"])
    for entry in document["device_types"]:
        (skipped if entry["key"] in types else additions)["device_types"].append(entry["key"])

    # Include would-be objects so nested additions under a newly created field
    # or type can be planned in the same pass.
    field_ids = {key: row.id for key, row in definitions.items()}
    type_ids = {key: row.id for key, row in types.items()}
    for spec in document["global_assignments"]:
        identity = (field_ids.get(spec["key"], f"new:{spec['key']}"), None)
        target = skipped if identity in assignments else additions
        target["global_assignments"].append(spec["key"])
    for entry in document["device_types"]:
        type_id = type_ids.get(entry["key"], f"new:{entry['key']}")
        for spec in entry["assignments"]:
            identity = (field_ids.get(spec["key"], f"new:{spec['key']}"), type_id)
            target = skipped if identity in assignments else additions
            target["type_assignments"].append(f"{entry['key']}/{spec['key']}")
        for spec in entry["plugins"]:
            target = skipped if (type_id, spec["plugin_id"]) in plugins else additions
            target["plugins"].append(f"{entry['key']}/{spec['plugin_id']}")
    for spec in document.get("vendor_device_fields", []):
        target = skipped if spec["key"] in vendor_fields else additions
        target["vendor_device_fields"].append(spec["key"])

    counts = {key: len(value) for key, value in additions.items()}
    return {"generation": document["generation"], "additions": additions,
            "skipped": skipped, "counts": counts, "total": sum(counts.values())}


def apply_additive_import(db: Session, document: dict, user=None) -> dict:
    """Create only missing schema objects and publish one GUI revision."""
    plan = additive_import_plan(db, document)
    generation = document["generation"]

    existing_vendor_fields = {
        row.key for row in db.scalars(select(EntityField).where(
            EntityField.entity == "vendor_devices",
        ))
    }
    _apply_vendor_device_fields(
        db, [
            spec for spec in document.get("vendor_device_fields", [])
            if spec["key"] not in existing_vendor_fields
        ],
    )

    definitions = {row.key: row for row in db.scalars(select(DeviceFieldDefinition))}
    for spec in document["fields"]:
        if spec["key"] not in definitions:
            row = DeviceFieldDefinition(**spec, protected_system_field=False, enabled=True,
                                        configuration_source="gui")
            db.add(row)
            definitions[spec["key"]] = row
    db.flush()

    types = {row.key: row for row in db.scalars(select(DeviceType))}
    for entry in document["device_types"]:
        if entry["key"] not in types:
            row = DeviceType(key=entry["key"], label=entry["label"], description=entry["description"],
                             enabled=entry["enabled"], position=entry["position"] or 0,
                             configuration_source="gui")
            db.add(row)
            types[entry["key"]] = row
    db.flush()

    existing_assignments = {
        (row.field_definition_id, row.device_type_id)
        for row in db.scalars(select(DeviceFieldAssignment))
    }

    def add_assignment(spec: dict, device_type: DeviceType | None, fallback: int) -> None:
        definition = definitions[spec["key"]]
        identity = (definition.id, device_type.id if device_type else None)
        if identity in existing_assignments:
            return
        db.add(DeviceFieldAssignment(
            field_definition_id=definition.id, device_type_id=identity[1],
            visible=spec["visible"], list_visible=spec["list_visible"], required=spec["required"],
            writable=spec["writable"], position=spec["position"] if spec["position"] is not None else fallback,
            label_override=spec["label_override"], description_override=spec["description_override"],
            validation_override=spec["validation_override"], excluded=spec["excluded"],
            configuration_source="gui",
        ))
        existing_assignments.add(identity)

    for position, spec in enumerate(document["global_assignments"]):
        add_assignment(spec, None, position * 10)
    for entry in document["device_types"]:
        device_type = types[entry["key"]]
        for position, spec in enumerate(entry["assignments"]):
            add_assignment(spec, device_type, position * 10)
    db.flush()

    existing_plugins = {
        (row.device_type_id, row.plugin_id)
        for row in db.scalars(select(DeviceTypePlugin))
    }
    for entry in document["device_types"]:
        device_type = types[entry["key"]]
        for spec in entry["plugins"]:
            identity = (device_type.id, spec["plugin_id"])
            if identity in existing_plugins:
                continue
            db.add(DeviceTypePlugin(
                device_type_id=device_type.id, plugin_id=spec["plugin_id"], enabled=spec["enabled"],
                configuration=spec["configuration"], configuration_source="gui",
            ))
            existing_plugins.add(identity)

    revision = None
    if plan["total"]:
        revision = publish_revision(
            db, source="gui", note=f"Imported DeviceSchema '{document['name']}' additively",
            summary={"generation": generation, "mode": "additive-import", "added": plan["counts"]},
            user=user,
        )
    return {**plan, "revision": revision}


def load_document(path: str | None = None) -> dict | None:
    path = path or settings.device_schema_path
    if not path:
        return None
    file = Path(path)
    if not file.exists():
        raise DocumentError(f"DeviceSchema document not found at {path}")
    return parse_document(file.read_text())


# ------------------------------------------------------------ reconciliation

def _gui_owned_exists(db: Session) -> bool:
    """Whether anyone has already configured this installation by hand.

    Seeded rows are stamped `system`, so a fresh installation answers no and a
    bootstrap import is safe; the moment an administrator saves anything, this
    answers yes and bootstrap stops importing.
    """
    for model in (DeviceFieldDefinition, DeviceFieldAssignment, DeviceTypePlugin, DeviceType):
        if db.scalar(select(func.count()).select_from(model).where(model.configuration_source == "gui")):
            return True
    return False


def _hand_bootstrap_objects_to_gui(db: Session, generation: str) -> None:
    """Transfer objects written by this bootstrap generation to the GUI.

    `configuration_source` becomes `gui` because that is what bootstrap
    promises: import once, then the administrator owns it and the admin API
    stops refusing to edit it.

    `source_revision` deliberately stays. The two columns answer different
    questions — who may change this row, and where it came from — and only the
    first is being handed over. Clearing the second as well would erase the
    only record that a document created the row, leaving it indistinguishable
    from one somebody typed in by hand, which is the distinction an
    authoritative pass needs to know what it may retire.
    """
    for model in (DeviceFieldDefinition, DeviceFieldAssignment, DeviceTypePlugin, DeviceType):
        for item in db.scalars(select(model).where(
            model.configuration_source == "yaml",
            model.source_revision == generation,
        )):
            item.configuration_source = "gui"


def _apply_definition(db: Session, spec: dict, generation: str, mode: str,
                      conflicts: list[dict]) -> DeviceFieldDefinition | None:
    definition = db.scalar(select(DeviceFieldDefinition).where(DeviceFieldDefinition.key == spec["key"]))
    if definition is None:
        definition = DeviceFieldDefinition(**spec, protected_system_field=False, enabled=True,
                                           configuration_source="yaml", source_revision=generation)
        db.add(definition)
        db.flush()
        return definition
    if definition.protected_system_field:
        # The document may relabel a protected field; it may not redefine what
        # the application means by it.
        definition.label = spec["label"]
        definition.description = spec["description"] or definition.description
        return definition
    if mode == "merge" and definition.configuration_source == "gui":
        differs = any(getattr(definition, name) != value for name, value in spec.items() if name != "key")
        if differs:
            conflicts.append({
                "kind": "field", "key": spec["key"],
                "message": f"field '{spec['key']}' is owned by the admin GUI and differs from the document",
            })
        return definition
    for name, value in spec.items():
        setattr(definition, name, value)
    definition.enabled = True
    definition.configuration_source = "yaml"
    definition.source_revision = generation
    return definition


def _apply_assignment(db: Session, definition: DeviceFieldDefinition, device_type: DeviceType | None,
                      spec: dict, position: int, generation: str, mode: str,
                      conflicts: list[dict]) -> None:
    type_id = device_type.id if device_type else None
    row = db.scalar(select(DeviceFieldAssignment).where(
        DeviceFieldAssignment.field_definition_id == definition.id,
        DeviceFieldAssignment.device_type_id.is_(None) if type_id is None
        else DeviceFieldAssignment.device_type_id == type_id,
    ))
    if row is not None and mode == "merge" and row.configuration_source == "gui":
        conflicts.append({
            "kind": "assignment", "key": definition.key,
            "device_type": device_type.key if device_type else "global",
            "message": (
                f"the {definition.key} assignment on "
                f"{device_type.key if device_type else 'global'} is owned by the admin GUI"
            ),
        })
        return
    if row is None:
        row = DeviceFieldAssignment(field_definition_id=definition.id, device_type_id=type_id)
        db.add(row)
    row.visible = spec["visible"]
    row.list_visible = spec["list_visible"]
    row.required = spec["required"]
    row.writable = spec["writable"]
    row.position = spec["position"] if spec["position"] is not None else position
    row.label_override = spec["label_override"]
    row.description_override = spec["description_override"]
    row.validation_override = spec["validation_override"]
    row.excluded = spec["excluded"]
    row.configuration_source = "yaml"
    row.source_revision = generation


def _prune(db: Session, model, generation: str, keep_ids: set[str], hard: bool) -> int:
    """Retire rows a document created and this document no longer mentions.

    Eligibility is provenance, not current ownership: a row is a candidate if a
    document owns it now, or if one created it and a bootstrap has since handed
    it to the GUI. Both came from a document, and an authoritative pass is
    entitled to retire what a document put there.

    A row somebody created in the admin GUI has no `source_revision` and is
    never a candidate, however authoritative the mode. That is the point of
    asking about provenance at all — "the document is the source of truth" is a
    claim over the document's own objects, not a licence to switch off
    somebody's work because a file does not mention it.

    Disabling first is deliberate: a field dropped from a document by accident
    should stop appearing, not take its stored values with it. Deleting needs
    `spec.prune: true`, which is a decision somebody wrote down.
    """
    removed = 0
    for row in db.scalars(select(model).where(or_(
        model.configuration_source == "yaml",
        model.source_revision.is_not(None),
    ))):
        identity = getattr(row, "id", None) or getattr(row, "name", None)
        if identity in keep_ids:
            continue
        if hard or not hasattr(row, "enabled"):
            db.delete(row)
        else:
            row.enabled = False
            row.source_revision = generation
        removed += 1
    return removed


def _apply_vendor_device_fields(
    db: Session, specs: list[dict], *, source: str = "yaml", generation: str | None = None,
) -> int:
    """Apply the shared vendor-device catalog carried by DeviceSchema.

    Core fields retain physical-column storage; additional fields use
    misc_data. Reconciliation updates definitions named by the document but
    deliberately does not delete omitted definitions or stored values.
    """
    changed = 0
    existing = {
        field.key: field for field in db.scalars(select(EntityField).where(
            EntityField.entity == "vendor_devices",
        ))
    }
    builtins = ENTITY_BUILTINS["vendor_devices"]
    for spec in specs:
        field = existing.get(spec["key"])
        if field is None:
            field = EntityField(
                entity="vendor_devices", key=spec["key"], writable=True,
                storage="column" if spec["key"] in builtins else "data",
                configuration_source=source,
            )
            db.add(field)
            existing[spec["key"]] = field
        before = (
            field.label, field.field_type, field.description, field.options,
            field.required, field.visible, field.list_visible, field.sensitive,
            field.indexed, field.unique_value, field.position,
        )
        for name, value in spec.items():
            if name != "key":
                setattr(field, name, value)
        field.configuration_source = source
        field.source_revision = generation
        after = (
            field.label, field.field_type, field.description, field.options,
            field.required, field.visible, field.list_visible, field.sensitive,
            field.indexed, field.unique_value, field.position,
        )
        changed += before != after
    return changed


def reconcile(db: Session, document: dict | None = None, mode: str | None = None) -> dict:
    """Apply a DeviceSchema document according to the configured mode.

    Returns the status an operator reads: which generation was seen, which
    revision it produced, and any conflict that stopped part of it applying.
    """
    global _status
    mode = mode or settings.device_schema_reconciliation
    if mode not in MODES:
        _status = {**_status, "state": "error", "mode": mode,
                   "message": f"device_schema_reconciliation must be one of {', '.join(MODES)}",
                   "errors": [f"unknown reconciliation mode '{mode}'"]}
        return status()
    if document is None:
        _status = {**_status, "mode": mode, "configured": False, "state": "disabled",
                   "message": "No DeviceSchema document is configured"}
        return status()

    generation = document["generation"]
    already = db.scalar(
        select(func.count()).select_from(DeviceSchemaRevision).where(
            DeviceSchemaRevision.source == "yaml",
            DeviceSchemaRevision.summary["generation"].astext == generation,
        )
    )

    if mode == "bootstrap":
        applied_before = db.scalar(select(func.count()).select_from(DeviceSchemaRevision).where(
            DeviceSchemaRevision.source == "yaml"))
        if applied_before or _gui_owned_exists(db):
            # Releases before the bootstrap ownership handoff left their
            # imported rows stamped as YAML. Repair those installations when
            # the same bootstrap document is seen again, without claiming
            # objects that came from merge or authoritative reconciliation.
            bootstrapped_generation = db.scalar(
                select(func.count()).select_from(DeviceSchemaRevision).where(
                    DeviceSchemaRevision.source == "yaml",
                    DeviceSchemaRevision.summary["generation"].astext == generation,
                    DeviceSchemaRevision.summary["mode"].astext == "bootstrap",
                )
            )
            if bootstrapped_generation:
                _hand_bootstrap_objects_to_gui(db, generation)
                db.commit()
                invalidate_cache()
            _status = {
                "mode": mode, "configured": True, "state": "skipped", "generation": generation,
                "applied_revision": None, "errors": [], "conflicts": [],
                "message": (
                    "Bootstrap has already run, or this installation has GUI-owned configuration. "
                    "The database and admin GUI own the schema from here."
                ),
            }
            return status()
    elif already and mode == "authoritative":
        # Still reconciled below: authoritative mode restores drift, and drift
        # is exactly the case where the generation has not changed.
        pass

    conflicts: list[dict] = []
    keep_definitions: set[str] = set()
    keep_types: set[str] = set()
    keep_plugins: set[str] = set()

    vendor_fields_changed = _apply_vendor_device_fields(
        db, document.get("vendor_device_fields", []), generation=generation,
    )
    db.flush()

    for spec in document["fields"]:
        definition = _apply_definition(db, spec, generation, mode, conflicts)
        if definition is not None:
            keep_definitions.add(definition.id)
    db.flush()

    by_key = {d.key: d for d in db.scalars(select(DeviceFieldDefinition))}

    for position, spec in enumerate(document["global_assignments"]):
        definition = by_key.get(spec["key"])
        if definition is None:
            conflicts.append({"kind": "assignment", "key": spec["key"],
                              "message": f"globalFields references undefined field '{spec['key']}'"})
            continue
        _apply_assignment(db, definition, None, spec, position * 10, generation, mode, conflicts)
    db.flush()

    for entry in document["device_types"]:
        item = db.scalar(select(DeviceType).where(DeviceType.key == entry["key"]))
        if item is None:
            item = DeviceType(key=entry["key"], configuration_source="yaml")
            db.add(item)
        elif mode == "merge" and item.configuration_source == "gui":
            conflicts.append({"kind": "device_type", "key": entry["key"],
                              "message": f"device type '{entry['key']}' is owned by the admin GUI"})
            continue
        item.label = entry["label"]
        item.description = entry["description"]
        item.enabled = entry["enabled"]
        if entry["position"] is not None:
            item.position = entry["position"]
        item.configuration_source = "yaml"
        item.source_revision = generation
        db.flush()
        keep_types.add(item.id)

        for position, spec in enumerate(entry["assignments"]):
            definition = by_key.get(spec["key"])
            if definition is None:
                conflicts.append({"kind": "assignment", "key": spec["key"], "device_type": entry["key"],
                                  "message": f"device type '{entry['key']}' references undefined field '{spec['key']}'"})
                continue
            _apply_assignment(db, definition, item, spec, position * 10, generation, mode, conflicts)

        for spec in entry["plugins"]:
            row = db.scalar(select(DeviceTypePlugin).where(
                DeviceTypePlugin.device_type_id == item.id,
                DeviceTypePlugin.plugin_id == spec["plugin_id"],
            ))
            if row is not None and mode == "merge" and row.configuration_source == "gui":
                conflicts.append({"kind": "plugin", "key": spec["plugin_id"], "device_type": entry["key"],
                                  "message": f"the {spec['plugin_id']} assignment on '{entry['key']}' is GUI-owned"})
                continue
            if row is None:
                row = DeviceTypePlugin(device_type_id=item.id, plugin_id=spec["plugin_id"])
                db.add(row)
            row.enabled = spec["enabled"]
            row.configuration = spec["configuration"]
            row.configuration_source = "yaml"
            row.source_revision = generation
            db.flush()
            keep_plugins.add(row.id)
    db.flush()

    # Assignments are identified by what this pass stamped rather than
    # collected as they are written: an assignment may be created inside either
    # the global or the per-type loop, and both stamp the same generation.
    keep_assignments = {
        row.id for row in db.scalars(select(DeviceFieldAssignment).where(
            DeviceFieldAssignment.configuration_source == "yaml",
            DeviceFieldAssignment.source_revision == generation,
        ))
    }

    removed = 0
    if mode == "authoritative":
        removed += _prune(db, DeviceFieldAssignment, generation, keep_assignments, hard=True)
        removed += _prune(db, DeviceTypePlugin, generation, keep_plugins, hard=True)
        removed += _prune(db, DeviceType, generation, keep_types, hard=document["prune"])
        removed += _prune(db, DeviceFieldDefinition, generation, keep_definitions, hard=document["prune"])

    if mode == "bootstrap":
        _hand_bootstrap_objects_to_gui(db, generation)

    revision = publish_revision(
        db, source="yaml",
        note=f"Reconciled DeviceSchema '{document['name']}' in {mode} mode",
        summary={
            "generation": generation, "mode": mode, "conflicts": len(conflicts),
            "retired": removed, "vendor_device_fields": vendor_fields_changed,
        },
    )
    db.commit()
    invalidate_cache()
    refresh_managed_indexes(db)
    ensure_entity_field_indexes(db)

    _status = {
        "mode": mode,
        "configured": True,
        "state": "conflict" if conflicts else "applied",
        "generation": generation,
        "applied_revision": revision,
        "errors": [],
        "conflicts": conflicts,
        "message": (
            f"Applied generation {generation} as revision {revision}"
            + (f" with {len(conflicts)} conflict(s)" if conflicts else "")
        ),
    }
    logger.info("DeviceSchema reconciliation: %s", _status["message"])
    return status()


def reconcile_from_settings(db: Session) -> dict:
    """Load and reconcile at startup, never letting a bad file break boot."""
    global _status
    try:
        document = load_document()
    except DocumentError as exc:
        _status = {
            "mode": settings.device_schema_reconciliation, "configured": True, "state": "error",
            "generation": None, "applied_revision": None, "errors": [str(exc)], "conflicts": [],
            "message": f"The DeviceSchema document was not applied: {exc}. The last published schema is still active.",
        }
        logger.error("DeviceSchema document rejected: %s", exc)
        return status()
    try:
        return reconcile(db, document)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        invalidate_cache()
        _status = {
            "mode": settings.device_schema_reconciliation, "configured": True, "state": "error",
            "generation": (document or {}).get("generation"), "applied_revision": None,
            "errors": [str(exc)], "conflicts": [],
            "message": f"Reconciliation failed: {exc}. The last published schema is still active.",
        }
        logger.exception("DeviceSchema reconciliation failed")
        return status()
