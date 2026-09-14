"""Merge, validation and plugin-policy rules of the dynamic device schema.

These run without a database: the merge is pure, and so is everything that
decides whether a value is acceptable or an action may run. The database-backed
half — seeding, publishing, the endpoints — is in test_device_schema_api.py.
"""

import unittest
from unittest.mock import patch

from app.config import settings
from app.models import DeviceFieldAssignment, DeviceFieldDefinition
from app.services.device_schema import (
    DeviceValidationError,
    EffectiveField,
    LOCKED_VISIBLE,
    PROTECTED_SYSTEM_FIELDS,
    _action_blocker,
    _apply_override,
    _base_field,
    _check_rules,
    _coerce,
    _index_expression,
    _role_groups,
    _display_order,
    apply_defaults,
    field_payload,
    plugin_payload_for,
    role_map,
    storage_for,
)


def definition(key="serial_number", **kwargs) -> DeviceFieldDefinition:
    return DeviceFieldDefinition(**{
        "id": f"def-{key}", "key": key, "label": key.replace("_", " ").title(),
        "field_type": "text", "description": None, "options": [], "validation": {},
        "default_value": None, "sensitive": False, "indexed": False, "unique_value": False,
        "plugin_role": None, "protected_system_field": False, "enabled": True,
        "configuration_source": "gui", **kwargs,
    })


def assignment(**kwargs) -> DeviceFieldAssignment:
    return DeviceFieldAssignment(**{
        "field_definition_id": "def-serial_number", "device_type_id": None, "visible": None,
        "list_visible": None, "required": None, "writable": None, "position": None, "label_override": None,
        "description_override": None, "validation_override": None, "excluded": False,
        "configuration_source": "gui", **kwargs,
    })


def effective(key="serial_number", **kwargs) -> EffectiveField:
    base = {
        "key": key, "label": key.replace("_", " ").title(), "field_type": "text",
        "description": None, "options": (), "validation": {}, "default_value": None,
        "sensitive": False, "indexed": False, "unique_value": False, "role": None,
        "protected": False, "visible": True, "list_visible": True, "required": False, "writable": True,
        "position": 0, "scope": "global", "configuration_source": "gui",
        "definition_id": f"def-{key}", "storage": storage_for(key),
    }
    return EffectiveField(**{**base, **kwargs})


class Fake:
    """Just enough of a Device for the rules that read one."""

    def __init__(self, document=None, online=False, unique_id="dev-1", type_key="router"):
        self._data = document or {}
        self.id = "device-1"
        self.unique_id = unique_id
        self.online_status = online
        self.device_type_key = type_key
        self.device_type_label = "Routers"


class MergeTests(unittest.TestCase):
    def test_unique_id_is_always_the_first_display_field(self):
        fields = [effective("architecture", position=0), effective("unique_id", position=100)]
        self.assertEqual(sorted(fields, key=_display_order)[0].key, "unique_id")

    def test_defaults_apply_when_an_assignment_says_nothing(self):
        field = _base_field(definition(), assignment(), scope="global", fallback_position=3)
        self.assertTrue(field.visible)
        self.assertTrue(field.list_visible)
        self.assertFalse(field.required)
        self.assertTrue(field.writable)
        self.assertEqual(field.position, 3)

    def test_an_override_changes_only_what_it_sets(self):
        base = effective(label="Serial Number", required=False, visible=True, position=10)
        merged = _apply_override(base, assignment(required=True))
        self.assertTrue(merged.required)
        # Everything the override left null is inherited, not reset.
        self.assertTrue(merged.visible)
        self.assertEqual(merged.position, 10)
        self.assertEqual(merged.label, "Serial Number")
        self.assertEqual(merged.scope, "type")

    def test_list_visibility_is_independent_and_inheritable(self):
        base = effective(visible=True, list_visible=True)
        hidden_in_list = _apply_override(base, assignment(list_visible=False))
        self.assertTrue(hidden_in_list.visible)
        self.assertFalse(hidden_in_list.list_visible)
        self.assertFalse(_apply_override(hidden_in_list, assignment()).list_visible)

    def test_an_override_can_relabel_without_touching_the_definition(self):
        merged = _apply_override(effective(), assignment(label_override="Asset Number"))
        self.assertEqual(merged.label, "Asset Number")

    def test_validation_overrides_layer_onto_the_definitions_rules(self):
        base = effective(validation={"max_length": 20, "pattern": "[A-Z]+"})
        merged = _apply_override(base, assignment(validation_override={"max_length": 5}))
        self.assertEqual(merged.validation, {"max_length": 5, "pattern": "[A-Z]+"})

    def test_identity_is_never_optional_or_hidden(self):
        self.assertIn("unique_id", LOCKED_VISIBLE)
        self.assertIn("unique_id", PROTECTED_SYSTEM_FIELDS)

    def test_storage_separates_columns_from_the_document(self):
        self.assertEqual(storage_for("unique_id"), "column")
        self.assertEqual(storage_for("checked_out_by_username"), "derived")
        self.assertEqual(storage_for("misc_data"), "virtual")
        self.assertEqual(storage_for("serial_number"), "data")
        self.assertTrue(effective("serial_number").stored)
        self.assertFalse(effective("unique_id").stored)


class CoercionTests(unittest.TestCase):
    def test_numbers_are_typed_from_text(self):
        self.assertEqual(_coerce(effective(field_type="number"), "24"), 24)
        self.assertEqual(_coerce(effective(field_type="number"), "1.5"), 1.5)

    def test_booleans_accept_the_spellings_a_spreadsheet_produces(self):
        for value in ("true", "TRUE", "yes", "1"):
            self.assertIs(_coerce(effective(field_type="boolean"), value), True)
        for value in ("false", "no", "0"):
            self.assertIs(_coerce(effective(field_type="boolean"), value), False)

    def test_an_unparseable_value_names_the_field_and_the_type(self):
        with self.assertRaises(DeviceValidationError) as caught:
            _coerce(effective(field_type="number", label="Ports"), "many")
        self.assertIn("Ports", str(caught.exception))
        self.assertIn("number", str(caught.exception))

    def test_dates_normalise_to_iso_so_text_order_is_chronological(self):
        self.assertEqual(_coerce(effective(field_type="date"), "2026-01-02"), "2026-01-02")

    def test_a_select_value_is_matched_case_insensitively(self):
        # An imported "X86_64" is the option spelled differently, not a
        # different value; the old architecture validator folded case and the
        # catalog has to keep doing it.
        field = effective(field_type="select", options=("x86_64", "arm64"))
        self.assertEqual(_coerce(field, " X86_64 "), "x86_64")

    def test_a_value_outside_the_choices_is_still_refused(self):
        field = effective(field_type="select", options=("x86_64",))
        with self.assertRaises(DeviceValidationError):
            _check_rules(field, _coerce(field, "sparc"))


class RuleTests(unittest.TestCase):
    def test_range_bounds(self):
        field = effective(field_type="number", label="Ports", validation={"min": 1, "max": 48})
        _check_rules(field, 24)
        with self.assertRaises(DeviceValidationError):
            _check_rules(field, 0)
        with self.assertRaises(DeviceValidationError):
            _check_rules(field, 96)

    def test_length_bounds(self):
        field = effective(validation={"min_length": 2, "max_length": 4})
        _check_rules(field, "abc")
        with self.assertRaises(DeviceValidationError):
            _check_rules(field, "a")
        with self.assertRaises(DeviceValidationError):
            _check_rules(field, "abcde")

    def test_pattern(self):
        field = effective(label="IMEI", validation={"pattern": r"\d{4}"})
        _check_rules(field, "1234")
        with self.assertRaises(DeviceValidationError) as caught:
            _check_rules(field, "12x4")
        self.assertIn("IMEI", str(caught.exception))

    def test_defaults_fill_only_absent_keys(self):
        fields = [effective("rack", default_value="R1"), effective("make", default_value="Acme")]
        self.assertEqual(apply_defaults(fields, {"make": "MikroTik"}),
                         {"make": "MikroTik", "rack": "R1"})


class IndexTests(unittest.TestCase):
    def test_typed_expressions_stay_immutable(self):
        self.assertEqual(_index_expression(effective("rack")), "(data->>'rack')")
        self.assertEqual(_index_expression(effective("ports", field_type="number")),
                         "(NULLIF(data->>'ports', '')::numeric)")
        self.assertEqual(_index_expression(effective("up", field_type="boolean")),
                         "(NULLIF(data->>'up', '')::boolean)")
        # A date cast is not IMMUTABLE and cannot appear in an expression
        # index; validated ISO text already sorts chronologically.
        self.assertEqual(_index_expression(effective("due", field_type="date")), "(data->>'due')")


class PluginPolicyTests(unittest.TestCase):
    FIELDS = (
        effective("username", role="device_username"),
        effective("password", role="device_password", sensitive=True),
        effective("wan_ip", role="scan_address_wan"),
        effective("serial_number"),
    )
    ACTION = {
        "id": "device-reboot.reboot", "entity": "devices", "requires_online": True,
        "required_roles": ["device_username", "device_password"],
        "required_role_groups": [["scan_address_lan", "scan_address_wan"]],
    }

    def device(self, **document):
        return Fake({"username": "admin", "password": "pw", "wan_ip": "10.0.0.1",
                     "serial_number": "SN-1", **document}, online=True)

    def test_a_fully_configured_device_is_allowed(self):
        self.assertIsNone(_action_blocker(None, self.ACTION, self.device(), self.FIELDS, None))

    def test_a_missing_value_names_the_field(self):
        device = self.device()
        del device._data["username"]
        self.assertIn("Username", _action_blocker(None, self.ACTION, device, self.FIELDS, None))

    def test_a_blank_sensitive_value_counts_as_configured(self):
        # Plenty of appliances use a username with no password at all, and an
        # operator who deliberately stored an empty one has configured it.
        device = self.device(password="")
        self.assertIsNone(_action_blocker(None, self.ACTION, device, self.FIELDS, None))

    def test_an_or_group_is_satisfied_by_either_side(self):
        fields = tuple(f for f in self.FIELDS if f.role != "scan_address_wan") + (
            effective("lan_ip", role="scan_address_lan"),
        )
        device = self.device()
        device._data["lan_ip"] = "192.168.1.1"
        self.assertIsNone(_action_blocker(None, self.ACTION, device, fields, None))

    def test_a_type_without_the_field_at_all_says_so(self):
        fields = tuple(f for f in self.FIELDS if f.role != "device_password")
        reason = _action_blocker(None, self.ACTION, self.device(), fields, None)
        self.assertIn("no field for", reason)
        self.assertIn("device_password", reason)

    def test_a_hidden_field_does_not_provide_its_role(self):
        # A field nobody can fill in is not a field the plugin can rely on.
        fields = tuple(
            f if f.role != "device_password" else effective("password", role="device_password", visible=False)
            for f in self.FIELDS
        )
        self.assertIn("no field for", _action_blocker(None, self.ACTION, self.device(), fields, None))

    def test_an_offline_device_is_refused_for_an_action_that_needs_one(self):
        device = self.device()
        device.online_status = False
        self.assertIn("offline", _action_blocker(None, self.ACTION, device, self.FIELDS, None))

    def test_a_readonly_user_cannot_run_anything(self):
        class User:
            role = "readonly"
        self.assertIn("write permission", _action_blocker(None, self.ACTION, self.device(), self.FIELDS, User()))

    def test_required_any_roles_is_read_as_a_group(self):
        self.assertEqual(_role_groups({"required_any_roles": ["a", "b"]}), [["a", "b"]])
        self.assertEqual(
            _role_groups({"required_role_groups": [["a", "b"]], "required_any_roles": ["c"]}),
            [["a", "b"], ["c"]],
        )


class PluginPayloadTests(unittest.TestCase):
    FIELDS = (
        effective("username", role="device_username"),
        effective("password", role="device_password", sensitive=True),
        effective("wan_ip", role="scan_address_wan"),
        effective("serial_number"),
        effective("notes", sensitive=True),
    )
    DEVICE = Fake({"username": "admin", "password": "pw", "wan_ip": "10.0.0.1",
                   "serial_number": "SN-1", "notes": "secret"})

    def test_only_the_declared_roles_are_sent(self):
        payload = plugin_payload_for(self.DEVICE, self.FIELDS, {
            "required_roles": ["device_username", "device_password"],
            "required_role_groups": [["scan_address_wan"]],
        })
        self.assertEqual(payload["_plugin_roles"], {
            "device_username": "admin", "device_password": "pw", "scan_address_wan": "10.0.0.1",
        })
        self.assertNotIn("serial_number", payload)
        self.assertNotIn("notes", payload)

    def test_scan_addresses_are_keyed_by_the_installations_own_field_name(self):
        payload = plugin_payload_for(self.DEVICE, self.FIELDS,
                                     {"required_role_groups": [["scan_address_wan"]]})
        self.assertEqual(payload["_scan_addresses"], {"wan_ip": "10.0.0.1"})

    def test_an_action_may_opt_into_the_visible_document(self):
        payload = plugin_payload_for(self.DEVICE, self.FIELDS, {
            "required_roles": ["device_username"], "include_document": "non_sensitive",
        })
        self.assertEqual(payload["serial_number"], "SN-1")
        # Opting in never widens the sensitive values an action already got.
        self.assertNotIn("notes", payload)
        self.assertNotIn("password", payload)

    def test_the_identity_a_plugin_reports_results_with_is_always_present(self):
        payload = plugin_payload_for(self.DEVICE, self.FIELDS, {})
        self.assertEqual(payload["id"], "device-1")
        self.assertEqual(payload["unique_id"], "dev-1")
        self.assertEqual(payload["device_type"], "router")

    def test_roles_are_resolved_by_meaning_not_by_field_name(self):
        renamed = (effective("mgmt_address", role="scan_address_wan"),)
        device = Fake({"mgmt_address": "172.16.0.1"})
        payload = plugin_payload_for(device, renamed, {"required_role_groups": [["scan_address_wan"]]})
        self.assertEqual(payload["_plugin_roles"]["scan_address_wan"], "172.16.0.1")
        self.assertEqual(payload["_scan_addresses"], {"mgmt_address": "172.16.0.1"})


class PayloadShapeTests(unittest.TestCase):
    def test_the_frontend_contract_is_stable(self):
        payload = field_payload(effective("rack", required=True, indexed=True))
        for key in ("key", "label", "type", "required", "visible", "sensitive", "writable",
                    "storage", "options", "role", "indexed", "unique", "validation",
                    "position", "scope", "protected", "configuration_source"):
            self.assertIn(key, payload)
        self.assertEqual(payload["storage"], "data")

    def test_role_map_ignores_fields_without_one(self):
        self.assertEqual(
            set(role_map([effective("a", role="x"), effective("b")])), {"x"},
        )


class ExclusionPolicyTests(unittest.TestCase):
    def test_exclusions_are_off_by_default(self):
        # "Global" has to keep meaning global unless an installation says
        # otherwise; the editor labels it an advanced override where it is on.
        self.assertFalse(settings.device_schema_allow_global_exclusions)

    def test_unknown_values_are_preserved_by_default(self):
        self.assertFalse(settings.device_schema_reject_unknown_fields)

    def test_the_setting_can_be_turned_on(self):
        with patch.object(settings, "device_schema_allow_global_exclusions", True):
            self.assertTrue(settings.device_schema_allow_global_exclusions)
