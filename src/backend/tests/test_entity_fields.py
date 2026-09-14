import json
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import settings
from app.models import Device, EntityField, Software, Test
from app.api.entity_fields import EntityFieldLayoutItem, EntityFieldLayoutUpdate, update_entity_field_layout
from app.services.entity_fields import (
    DEFAULT_FIELDS, ON_DEMAND_DEVICE_FIELDS, _configured_fields, field_payload, merge_extra_columns,
)


class EntityFieldConfigurationTests(unittest.TestCase):
    def test_relationship_fields_are_marked_protected(self):
        relationship = EntityField(
            entity="tests", key="device_unique_id", label="Device", field_type="text",
            required=True, visible=True, list_visible=True, sensitive=False, writable=True,
            storage="column", position=0, options=[], indexed=False, unique_value=False,
        )
        additional = EntityField(
            entity="tests", key="lab", label="Lab", field_type="text",
            required=False, visible=True, list_visible=True, sensitive=False, writable=True,
            storage="data", position=10, options=[], indexed=False, unique_value=False,
        )
        self.assertTrue(field_payload(relationship)["protected"])
        self.assertTrue(field_payload(relationship)["list_visibility_locked"])
        self.assertFalse(field_payload(additional)["protected"])
        self.assertFalse(field_payload(additional)["list_visibility_locked"])

    def test_layout_reorders_and_hides_fields_from_lists_atomically(self):
        name = EntityField(
            id="name", entity="software", key="name", label="Name", field_type="text",
            required=True, visible=True, list_visible=True, sensitive=False, writable=True,
            storage="column", position=0, options=[], indexed=False, unique_value=False,
        )
        version = EntityField(
            id="version", entity="software", key="version", label="Version", field_type="text",
            required=False, visible=True, list_visible=True, sensitive=False, writable=True,
            storage="column", position=10, options=[], indexed=False, unique_value=False,
        )

        class Scalars:
            def all(self):
                return sorted((name, version), key=lambda field: field.position)

        class DB:
            def scalars(self, _query):
                return Scalars()

            def commit(self):
                pass

        result = update_entity_field_layout(
            EntityFieldLayoutUpdate(fields=[
                EntityFieldLayoutItem(id="version", list_visible=False),
                EntityFieldLayoutItem(id="name", list_visible=True),
            ]),
            "software", DB(), None,
        )

        self.assertEqual([field["key"] for field in result], ["version", "name"])
        self.assertFalse(result[0]["list_visible"])
        self.assertEqual([field["position"] for field in result], [0, 10])

    def test_alembic_revision_ids_fit_database_column(self):
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        scripts = ScriptDirectory.from_config(config)
        for revision in scripts.walk_revisions():
            self.assertLessEqual(len(revision.revision), 32, revision.revision)

    def configured(self, value):
        with patch.object(settings, "entity_fields_json", json.dumps(value)):
            return _configured_fields()

    def test_unconfigured_builtins_exist_but_only_required_fields_are_visible(self):
        with patch.object(settings, "entity_fields_json", ""):
            configured = _configured_fields()

        for entity, defaults in DEFAULT_FIELDS.items():
            fields = {field["key"]: field for field in configured[entity]}
            expected = {field["key"] for field in defaults}
            if entity == "devices":
                expected -= ON_DEMAND_DEVICE_FIELDS
            self.assertEqual(set(fields), expected)
        devices = {field["key"]: field for field in configured["devices"]}
        self.assertTrue(devices["unique_id"]["visible"])
        self.assertNotIn("username", devices)
        self.assertNotIn("password", devices)

    def test_minimal_catalog_and_custom_json_field(self):
        configured = self.configured({
            "devices": {"optional_fields": ["status"], "custom_fields": [{"key": "rack", "label": "Rack"}]},
            "software": {"optional_fields": [], "custom_fields": []},
            "tests": {"optional_fields": [], "custom_fields": [{"key": "ping", "label": "Ping"}]},
        })

        device_by_key = {field["key"]: field for field in configured["devices"]}
        self.assertEqual(
            set(device_by_key),
            ({field["key"] for field in DEFAULT_FIELDS["devices"]} - ON_DEMAND_DEVICE_FIELDS) | {"rack"},
        )
        self.assertTrue(device_by_key["unique_id"]["visible"])
        self.assertTrue(device_by_key["status"]["visible"])
        self.assertTrue(device_by_key["rack"]["visible"])
        self.assertFalse(device_by_key["location"]["visible"])
        self.assertFalse(device_by_key["location"]["required"])
        self.assertEqual(device_by_key["rack"]["storage"], "data")
        test_by_key = {field["key"]: field for field in configured["tests"]}
        self.assertEqual(test_by_key["ping"]["storage"], "data")
        status = configured["devices"][1]
        self.assertNotIn("checked_out", status["options"])

    def test_device_credentials_are_predefined_with_plaintext_storage(self):
        configured = self.configured({
            "devices": {"optional_fields": ["username", "password"]},
            "software": {}, "tests": {},
        })
        fields = {field["key"]: field for field in configured["devices"]}
        self.assertEqual(fields["username"]["role"], "device_username")
        self.assertEqual(fields["password"]["role"], "device_password")
        self.assertEqual(fields["password"]["field_type"], "text")
        self.assertTrue(fields["password"]["sensitive"])
        self.assertFalse(fields["password"]["required"])

    def test_plugin_device_fields_are_absent_until_explicitly_selected(self):
        configured = self.configured({"devices": {}, "software": {}, "tests": {}})
        keys = {field["key"] for field in configured["devices"]}
        self.assertTrue({
            "imei", "architecture", "wan_ip", "lan_ip", "wan_mac", "lan_mac", "username", "password",
            "firmware_version", "hardware_version", "online_status", "last_seen_online",
        }.isdisjoint(keys))

    def test_device_identifiers_are_predefined_indexed_optional_fields(self):
        configured = self.configured({
            "devices": {"optional_fields": ["imei", "serial_number", "lan_mac", "wan_mac"]},
            "software": {}, "tests": {},
        })
        fields = {field["key"]: field for field in configured["devices"]}
        for key in ("imei", "serial_number", "lan_mac", "wan_mac"):
            self.assertTrue(fields[key]["visible"])
            self.assertTrue(fields[key]["indexed"])
            self.assertFalse(fields[key]["unique_value"])
        self.assertEqual(fields["wan_mac"]["role"], "discovery_wan_mac")
        self.assertEqual(fields["lan_mac"]["role"], "discovery_lan_mac")

    def test_required_fields_are_automatic_and_cannot_be_relisted(self):
        with self.assertRaisesRegex(RuntimeError, "software.name.*added automatically"):
            self.configured({
                "devices": {},
                "software": {"optional_fields": ["name"]},
                "tests": {},
            })

    def test_unknown_builtin_explains_how_to_make_it_custom(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown optional tests field 'result'.*under custom_fields"):
            self.configured({
                "devices": {},
                "software": {},
                "tests": {"optional_fields": ["result"]},
            })

    def test_builtin_cannot_be_declared_as_custom(self):
        with self.assertRaisesRegex(RuntimeError, "tests.notes.*under optional_fields"):
            self.configured({"tests": {"custom_fields": [{"key": "notes"}]}})


class ExtraColumnTests(unittest.TestCase):
    def test_unknown_columns_are_merged_into_json_bucket(self):
        row = merge_extra_columns(
            {"unique_id": "router-1", "status": "available", "ping": "50ms", "empty": None},
            {"unique_id", "status", "misc_data"},
            "misc_data",
        )

        self.assertEqual(row, {
            "unique_id": "router-1",
            "status": "available",
            "misc_data": {"ping": "50ms", "__extra_fields": ["ping"]},
        })

    def test_extra_column_cannot_silently_replace_explicit_json(self):
        with self.assertRaisesRegex(ValueError, "duplicate keys"):
            merge_extra_columns(
                {"unique_id": "router-1", "misc_data": {"ping": "25ms"}, "ping": "50ms"},
                {"unique_id", "misc_data"},
                "misc_data",
            )


class JsonDocumentModelTests(unittest.TestCase):
    def test_device_fields_share_one_document_without_leaking_into_misc_data(self):
        device = Device(unique_id="router-1", status="available", location="Lab", misc_data={"rack": "R7"})

        # unique_id is a real column: it is durable identity, used by URLs,
        # imports, test results and integrations, and it is the one thing about
        # a device that is not installation-defined.
        self.assertEqual(device.unique_id, "router-1")
        self.assertEqual(device._data, {"status": "available", "location": "Lab", "rack": "R7"})
        self.assertEqual(device.misc_data, {"rack": "R7"})

    def test_setting_misc_data_does_not_discard_the_rest_of_the_document(self):
        # It used to keep only the handful of keys the model flattens and drop
        # everything else, so writing one loose value destroyed the device's
        # credentials and every installation-defined field it held.
        device = Device(unique_id="router-1")
        device.merge_data({"username": "admin", "serial_number": "SN-1", "make": "Acme"})

        device.misc_data = {"rack": "R7"}

        self.assertEqual(device._data, {
            "username": "admin", "serial_number": "SN-1", "make": "Acme", "rack": "R7",
        })

    def test_merging_a_document_keeps_the_values_a_write_did_not_mention(self):
        # What makes a partial write partial. A device password supplied once
        # survives an edit that only changes the username — and a value whose
        # field is no longer on the layout survives too.
        device = Device(unique_id="router-1")
        device.merge_data({"username": "admin", "password": "secret", "retired": "keep"})
        device.merge_data({"username": "operator"})
        self.assertEqual(device._data,
                         {"username": "operator", "password": "secret", "retired": "keep"})

    def test_merging_none_removes_a_value(self):
        device = Device(unique_id="router-1")
        device.merge_data({"make": "Acme"})
        device.merge_data({"make": None})
        self.assertNotIn("make", device._data)

    def test_intentionally_blank_password_is_stored_as_plaintext(self):
        # An empty password is a configured credential: plenty of appliances
        # use a username with no password, and absence means something else.
        device = Device(unique_id="router-1", misc_data={"username": "admin", "password": ""})
        self.assertEqual(device._data["password"], "")
        self.assertEqual(device.misc_data["password"], "")

    def test_software_fields_share_one_document(self):
        software = Software(name="scanner", version="6.0.0", misc_data={"channel": "stable"})

        self.assertEqual(software._data, {"name": "scanner", "version": "6.0.0", "channel": "stable"})
        self.assertEqual(software.misc_data, {"channel": "stable"})

    def test_test_misc_data_exposes_measurements_not_system_values(self):
        test = Test(
            device_id="device-1", software_id="software-1", outcome="pass",
            tag="automated", notes=None, misc_data={"ping": "50ms"},
        )

        self.assertEqual(test._data, {"outcome": "pass", "tag": "automated", "ping": "50ms"})
        self.assertEqual(test.misc_data, {"ping": "50ms"})

    def test_omitted_predefined_csv_field_remains_visible_as_extra_data(self):
        test = Test(device_id="device-1", software_id="software-1", outcome="pass")
        test.misc_data = {"tag": "vendor-specific", "__extra_fields": ["tag"]}

        self.assertEqual(test.misc_data, {"tag": "vendor-specific"})


if __name__ == "__main__":
    unittest.main()
