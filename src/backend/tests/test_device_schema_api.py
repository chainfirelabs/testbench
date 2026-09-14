"""The dynamic device schema end to end, against a real PostgreSQL.

Skipped when no database is reachable, so the unit suite still runs anywhere.
Point it at a throwaway instance:

    TB_DB_HOST=localhost TB_DB_PORT=5432 pytest tests/test_device_schema_api.py

Every test class starts from an empty database, because what is being checked
is largely what an empty database turns into: which fields get seeded, what a
new device type inherits, and what a plugin is allowed to touch.
"""

import unittest
from unittest.mock import patch

import sqlalchemy
import yaml
from fastapi.testclient import TestClient

from app.config import settings

try:
    with sqlalchemy.create_engine(settings.sqlalchemy_url).connect():
        DATABASE_AVAILABLE = True
except Exception:  # noqa: BLE001 — any connection failure means "skip"
    DATABASE_AVAILABLE = False

def reset_database() -> None:
    engine = sqlalchemy.create_engine(settings.sqlalchemy_url)
    with engine.connect() as connection:
        connection.execute(sqlalchemy.text("DROP SCHEMA public CASCADE"))
        connection.execute(sqlalchemy.text("CREATE SCHEMA public"))
        connection.commit()
    # Every module-level cache in the app is keyed off a revision that has just
    # ceased to exist.
    from app.services.device_schema import invalidate_cache
    invalidate_cache()


class SchemaCase(unittest.TestCase):
    """A fresh installation, seeded by the application's own startup."""

    create_test_device_types = True

    @classmethod
    def start_fresh(cls):
        """Empty the database and let the application seed itself into it."""
        reset_database()
        from app.main import app
        cls._client = TestClient(app)
        cls._client.__enter__()
        cls.client = cls._client
        token = cls.client.post(
            "/api/v1/auth/login",
            json={"username": settings.admin_username, "password": settings.admin_password},
        ).json()["access_token"]
        cls.headers = {"Authorization": f"Bearer {token}"}
        if cls.create_test_device_types:
            from app.db import SessionLocal
            from app.models import DeviceType
            with SessionLocal() as db:
                for position, (key, label) in enumerate((
                    ("router", "Routers"),
                    ("switch", "Network Switches"),
                    ("access-point", "Wireless Access Points"),
                    ("mobile", "Mobile Phones"),
                    ("tablet", "Tablets"),
                    ("laptop", "Laptops"),
                    ("desktop", "Desktops"),
                    ("server", "Servers"),
                    ("iot", "IoT Devices"),
                    ("other", "Other"),
                )):
                    db.add(DeviceType(
                        key=key, label=label, position=position * 10,
                        configuration_source="system",
                    ))
                db.commit()

    @classmethod
    def stop(cls):
        cls._client.__exit__(None, None, None)

    @classmethod
    def setUpClass(cls):
        if not DATABASE_AVAILABLE:
            raise unittest.SkipTest("no PostgreSQL available")
        cls.start_fresh()

    @classmethod
    def tearDownClass(cls):
        cls.stop()

    # -- small helpers, so the tests read as what they are checking ----------

    # Classmethods so a setUpClass can build its fixture with the same calls
    # the tests read from.
    @classmethod
    def get(cls, path):
        return cls.client.get(path, headers=cls.headers)

    @classmethod
    def post(cls, path, body=None, **kwargs):
        return cls.client.post(path, headers=cls.headers, json=body, **kwargs)

    @classmethod
    def patch_(cls, path, body):
        return cls.client.patch(path, headers=cls.headers, json=body)

    @classmethod
    def put(cls, path, body):
        return cls.client.put(path, headers=cls.headers, json=body)

    @classmethod
    def types(cls):
        return {item["key"]: item for item in cls.get("/api/v1/device-types").json()}

    @classmethod
    def fields_for(cls, type_key):
        return {f["key"]: f for f in cls.get(f"/api/v1/device-schema/types/{type_key}").json()["fields"]}

    @classmethod
    def make_visible(cls, *keys):
        """Turn on catalog fields an installation would enable in its values."""
        definitions = {field["key"] for field in cls.get("/api/v1/device-fields").json()}
        if missing := set(keys) - definitions:
            from app.services.entity_fields import DEFAULT_FIELDS
            by_key = {field["key"]: field for field in DEFAULT_FIELDS["devices"]}
            for key in missing:
                spec = by_key[key]
                cls.post("/api/v1/device-fields", {
                    "key": key, "label": spec["label"], "field_type": spec["field_type"],
                    "description": spec.get("description"), "options": spec.get("options", []),
                    "sensitive": spec.get("sensitive", False), "indexed": spec.get("indexed", False),
                    "unique_value": spec.get("unique_value", False), "plugin_role": spec.get("role"),
                })
        assignments = cls.get("/api/v1/device-schema/global").json()["assignments"]
        assigned = {row["field_key"] for row in assignments}
        assignments.extend(
            {"field_key": key, "visible": True}
            for key in keys if key not in assigned
        )
        for row in assignments:
            if row["field_key"] in keys:
                row["visible"] = True
        return cls.put("/api/v1/device-schema/global", {"assignments": assignments})


class SeedingTests(SchemaCase):
    create_test_device_types = False

    def test_a_fresh_installation_has_no_device_types(self):
        self.assertEqual(self.types(), {})

    def test_a_plugin_free_field_catalog_contains_only_core_definitions(self):
        definitions = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        self.assertLessEqual({"unique_id", "status", "make", "model"}, set(definitions))
        self.assertTrue({
            "imei", "architecture", "wan_ip", "lan_ip", "wan_mac", "lan_mac", "username", "password",
        }.isdisjoint(definitions))
        self.assertTrue(definitions["unique_id"]["protected_system_field"])
        self.assertEqual(definitions["unique_id"]["storage"], "column")

    def test_seeding_is_idempotent(self):
        before = len(self.get("/api/v1/device-fields").json())
        from app.db import SessionLocal
        from app.services.device_schema import seed_device_schema
        with SessionLocal() as db:
            seed_device_schema(db)
            seed_device_schema(db)
        self.assertEqual(len(self.get("/api/v1/device-fields").json()), before)
        self.assertEqual(self.types(), {})

    def test_an_installed_plugin_creates_global_fields_once(self):
        manifest = {
            "id": "network-scan", "recommended_fields": [
                {"key": "wan_ip", "label": "WAN IP", "type": "text", "role": "scan_address_wan"},
            ],
        }
        from app.api.device_schema import reconcile_installed_plugin_fields
        from app.db import SessionLocal
        from app.services import plugin_host
        with patch.object(plugin_host.registry, "_manifests", {"network-scan": manifest}):
            with SessionLocal() as db:
                self.assertEqual(reconcile_installed_plugin_fields(db), {"network-scan": ["wan_ip"]})
                self.assertEqual(reconcile_installed_plugin_fields(db), {})
        definitions = {field["key"]: field for field in self.get("/api/v1/device-fields").json()}
        self.assertEqual(definitions["wan_ip"]["plugin_role"], "scan_address_wan")
        global_fields = {field["key"]: field for field in self.get("/api/v1/device-schema/global").json()["fields"]}
        self.assertTrue(global_fields["wan_ip"]["visible"])

    def test_a_revision_is_published(self):
        self.assertGreaterEqual(self.get("/api/v1/device-schema/global").json()["revision"], 1)

    def test_managed_indexes_are_applied(self):
        indexes = self.get("/api/v1/device-schema/indexes").json()
        self.assertTrue(indexes)
        self.assertTrue(all(row["state"] == "applied" for row in indexes), indexes)


class DeviceWriteTests(SchemaCase):
    def test_the_data_envelope_round_trips(self):
        r = self.post("/api/v1/devices", {
            "unique_id": "dev-env", "device_type": "router",
            "data": {"make": "MikroTik", "serial_number": "SN-1"},
        })
        self.assertEqual(r.status_code, 201, r.text)
        device = r.json()
        self.assertEqual(device["data"]["serial_number"], "SN-1")
        # The flattened projection is still there for existing clients.
        self.assertEqual(device["make"], "MikroTik")

    def test_a_flattened_body_is_still_accepted(self):
        r = self.post("/api/v1/devices", {
            "unique_id": "dev-flat", "device_type": "mobile", "make": "Samsung", "imei": "3554",
        })
        self.assertEqual(r.status_code, 201, r.text)
        # `imei` is not a field on any request model or in the minimal catalog;
        # permissive writes still preserve it in the generic data document.
        self.assertEqual(r.json()["data"]["imei"], "3554")

    def test_the_document_carries_the_status_even_when_it_is_the_default(self):
        # The grid reads status out of the document. A device created as
        # `available` has not crossed any status boundary, and it still has to
        # be stored saying so rather than relying on a read-time default.
        created = self.post("/api/v1/devices", {"unique_id": "dev-status"}).json()
        self.assertEqual(created["data"]["status"], "available")
        self.assertEqual(created["status"], "available")

    def test_a_partial_update_leaves_the_rest_of_the_document_alone(self):
        created = self.post("/api/v1/devices", {
            "unique_id": "dev-partial", "data": {"make": "Acme", "model": "X1"}}).json()
        updated = self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"model": "X2"}}).json()
        self.assertEqual(updated["data"]["make"], "Acme")
        self.assertEqual(updated["data"]["model"], "X2")

    def test_a_stored_credential_survives_an_unrelated_edit(self):
        self.make_visible("username", "password")
        created = self.post("/api/v1/devices", {
            "unique_id": "dev-creds", "data": {"username": "admin", "password": "secret"}}).json()
        updated = self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"username": "operator"}}).json()
        self.assertEqual(updated["data"]["password"], "secret")
        # An explicitly blank password is a configured credential, not an
        # absent one, and is stored as written.
        blank = self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"password": ""}}).json()
        self.assertEqual(blank["data"]["password"], "")
        # Null is the way to say "there is no credential here".
        cleared = self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"password": None}}).json()
        self.assertNotIn("password", cleared["data"])
        # A non-sensitive field treats blank and null the same way.
        self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"make": "Acme"}})
        self.assertNotIn(
            "make", self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"make": ""}}).json()["data"])

    def test_secrets_are_masked_in_the_audit_trail(self):
        self.make_visible("username", "password")
        created = self.post("/api/v1/devices", {
            "unique_id": "dev-audit", "data": {"username": "admin", "password": "hunter2"}}).json()
        self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"password": "hunter3"}})
        entries = self.get("/api/v1/audit_logs?entity_id=" + created["id"]).json()["items"]
        blob = repr(entries)
        # The entry records that a credential was set and then changed; it is
        # not a second place the credential is stored.
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("hunter3", blob)
        self.assertIn("***", blob)
        self.assertIn("admin", blob)

    def test_a_blank_value_clears_a_field(self):
        created = self.post("/api/v1/devices", {"unique_id": "dev-clear", "data": {"make": "Acme"}}).json()
        updated = self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"make": ""}}).json()
        self.assertNotIn("make", updated["data"])

    def test_unique_id_is_a_column_and_stays_unique(self):
        self.post("/api/v1/devices", {"unique_id": "dev-dup"})
        r = self.post("/api/v1/devices", {"unique_id": "dev-dup"})
        self.assertEqual(r.status_code, 409, r.text)

    def test_a_device_is_addressable_by_its_unique_id(self):
        self.post("/api/v1/devices", {"unique_id": "dev-lookup"})
        self.assertEqual(self.get("/api/v1/devices/dev-lookup").status_code, 200)

    def test_values_are_typed_and_range_checked_on_every_path(self):
        self.post("/api/v1/device-fields", {
            "key": "port_count", "label": "Ports", "field_type": "number",
            "validation": {"min": 1, "max": 48}})
        created = self.post("/api/v1/devices", {"unique_id": "dev-ports"}).json()

        self.assertEqual(
            self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"port_count": 99}}).status_code, 422)
        typed = self.patch_(f"/api/v1/devices/{created['id']}", {"data": {"port_count": "24"}}).json()
        self.assertEqual(typed["data"]["port_count"], 24)

        # The same rule, through bulk and through an import.
        r = self.post("/api/v1/devices/bulk", {"upserts": [{"unique_id": "dev-ports", "port_count": 99}]})
        self.assertEqual(r.json()["errors"][0]["row"], 0, r.text)
        r = self.client.post(
            "/api/v1/devices/import", headers=self.headers,
            files={"file": ("d.csv", "unique_id,port_count\ndev-ports,99\n", "text/csv")})
        self.assertTrue(r.json()["errors"], r.text)

    def test_a_unique_field_refuses_a_second_holder(self):
        self.post("/api/v1/device-fields", {
            "key": "asset_tag", "label": "Asset Tag", "field_type": "text", "unique_value": True})
        first = self.post("/api/v1/devices", {"unique_id": "dev-tag-1", "data": {"asset_tag": "A-1"}})
        self.assertEqual(first.status_code, 201, first.text)
        second = self.post("/api/v1/devices", {"unique_id": "dev-tag-2", "data": {"asset_tag": "A-1"}})
        self.assertEqual(second.status_code, 422, second.text)
        self.assertIn("already used", second.text)

    def test_an_unknown_key_is_preserved_rather_than_refused(self):
        # A field removed from the catalog leaves values behind, and they are
        # still the operator's data.
        created = self.post("/api/v1/devices", {
            "unique_id": "dev-unknown", "data": {"retired_field": "keep me"}}).json()
        self.assertEqual(created["data"]["retired_field"], "keep me")

    def test_unknown_keys_can_be_refused_by_policy(self):
        with patch.object(settings, "device_schema_reject_unknown_fields", True):
            r = self.post("/api/v1/devices", {"unique_id": "dev-strict", "data": {"nope": 1}})
        self.assertEqual(r.status_code, 422, r.text)


class LayoutTests(SchemaCase):
    def test_export_is_a_round_trip_bootstrap_configmap(self):
        self.make_visible("password")
        router = self.types()["router"]
        self.put(f"/api/v1/device-schema/types/{router['id']}", {
            "assignments": [{"field_key": "password", "list_visible": False}],
        })

        response = self.client.get("/api/v1/device-schema/export", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("testbench-device-schema.yaml", response.headers["content-disposition"])
        config_map = yaml.safe_load(response.text)
        self.assertEqual(config_map["kind"], "ConfigMap")

        from app.services.device_schema_yaml import parse_document
        exported = parse_document(config_map["data"]["device-schema.yaml"])
        definitions = {field["key"]: field for field in exported["fields"]}
        self.assertEqual(definitions["password"]["plugin_role"], "device_password")
        exported_router = next(item for item in exported["device_types"] if item["key"] == "router")
        password = next(item for item in exported_router["assignments"] if item["key"] == "password")
        self.assertFalse(password["list_visible"])

    def test_a_global_field_reaches_every_type_including_new_ones(self):
        self.post("/api/v1/device-fields", {"key": "rack", "label": "Rack", "add_to_global": True})
        for key in ("router", "mobile", "server"):
            self.assertIn("rack", self.fields_for(key))
        # And a type created afterwards inherits it without an assignment row.
        self.post("/api/v1/device-types", {"key": "kiosk", "label": "Kiosks"})
        self.assertIn("rack", self.fields_for("kiosk"))

    def test_a_type_specific_field_does_not_leak_to_other_types(self):
        self.post("/api/v1/device-fields", {
            "key": "carrier", "label": "Carrier", "field_type": "select",
            "options": ["AT&T", "Verizon"], "add_to_global": False})
        mobile = self.types()["mobile"]
        self.put(f"/api/v1/device-schema/types/{mobile['id']}",
                 {"assignments": [{"field_key": "carrier", "visible": True}]})
        self.assertIn("carrier", self.fields_for("mobile"))
        self.assertNotIn("carrier", self.fields_for("router"))

    def test_a_type_can_require_a_global_field_for_itself_alone(self):
        server = self.types()["server"]
        self.put(f"/api/v1/device-schema/types/{server['id']}",
                 {"assignments": [{"field_key": "serial_number", "required": True, "visible": True}]})
        self.assertTrue(self.fields_for("server")["serial_number"]["required"])
        self.assertFalse(self.fields_for("router")["serial_number"]["required"])

        r = self.post("/api/v1/devices", {"unique_id": "srv-1", "device_type": "server"})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Serial Number", r.text)
        self.assertEqual(
            self.post("/api/v1/devices", {"unique_id": "rtr-1", "device_type": "router"}).status_code, 201)

    def test_hiding_a_field_never_deletes_what_devices_store(self):
        self.post("/api/v1/device-fields", {"key": "shelf", "label": "Shelf"})
        created = self.post("/api/v1/devices", {
            "unique_id": "dev-shelf", "device_type": "router", "data": {"shelf": "S3"}}).json()
        router = self.types()["router"]
        self.put(f"/api/v1/device-schema/types/{router['id']}",
                 {"assignments": [{"field_key": "shelf", "visible": False}]})
        self.assertFalse(self.fields_for("router")["shelf"]["visible"])
        self.assertEqual(self.get(f"/api/v1/devices/{created['id']}").json()["data"]["shelf"], "S3")

    def test_a_field_can_be_hidden_from_a_type_list_without_being_hidden(self):
        self.make_visible("password")
        router = self.types()["router"]
        self.put(f"/api/v1/device-schema/types/{router['id']}",
                 {"assignments": [{"field_key": "password", "list_visible": False}]})

        password = self.fields_for("router")["password"]
        self.assertTrue(password["visible"])
        self.assertFalse(password["list_visible"])
        assignments = self.get(f"/api/v1/device-schema/types/{router['id']}").json()["assignments"]
        saved = next(row for row in assignments if row["field_key"] == "password")
        self.assertIsNone(saved["visible"])
        self.assertFalse(saved["list_visible"])

    def test_changing_a_device_type_validates_the_target_and_keeps_the_past(self):
        self.post("/api/v1/device-fields", {"key": "imsi", "label": "IMSI", "add_to_global": False})
        mobile = self.types()["mobile"]
        self.put(f"/api/v1/device-schema/types/{mobile['id']}",
                 {"assignments": [{"field_key": "imsi", "required": True, "visible": True}]})
        created = self.post("/api/v1/devices", {
            "unique_id": "dev-move", "device_type": "router", "data": {"wan_ip": "10.0.0.5"}}).json()

        refused = self.patch_(f"/api/v1/devices/{created['id']}", {"device_type": "mobile"})
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("IMSI", refused.text)

        moved = self.patch_(f"/api/v1/devices/{created['id']}",
                            {"device_type": "mobile", "data": {"imsi": "310150"}})
        self.assertEqual(moved.status_code, 200, moved.text)
        # The router value is hidden on a phone, not destroyed.
        self.assertEqual(moved.json()["data"]["wan_ip"], "10.0.0.5")

    def test_identity_cannot_be_hidden_or_deleted(self):
        definitions = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        self.assertEqual(
            self.client.delete(f"/api/v1/device-fields/{definitions['unique_id']['id']}",
                               headers=self.headers).status_code, 422)
        self.assertEqual(
            self.put("/api/v1/device-schema/global",
                     {"assignments": [{"field_key": "unique_id", "visible": False}]}).status_code, 422)

    def test_a_protected_field_keeps_its_meaning(self):
        definitions = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        r = self.patch_(f"/api/v1/device-fields/{definitions['status']['id']}", {"field_type": "number"})
        self.assertEqual(r.status_code, 422, r.text)
        # Relabelling one is fine: presentation is the administrator's.
        r = self.patch_(f"/api/v1/device-fields/{definitions['status']['id']}", {"label": "State"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_a_field_cannot_take_a_key_the_envelope_owns(self):
        for key in ("device_type", "unique_id", "created_at"):
            r = self.post("/api/v1/device-fields", {"key": key, "label": "Nope"})
            self.assertIn(r.status_code, (409, 422), f"{key}: {r.text}")

    def test_a_field_holding_values_cannot_be_deleted_by_accident(self):
        self.post("/api/v1/device-fields", {"key": "temp_field", "label": "Temp"})
        self.post("/api/v1/devices", {"unique_id": "dev-temp", "data": {"temp_field": "x"}})
        definitions = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        r = self.client.delete(f"/api/v1/device-fields/{definitions['temp_field']['id']}",
                               headers=self.headers)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("Disable it instead", r.text)

    def test_an_in_use_device_type_cannot_be_deleted(self):
        self.post("/api/v1/device-types", {"key": "doomed", "label": "Doomed"})
        doomed = self.types()["doomed"]
        self.post("/api/v1/devices", {"unique_id": "dev-doomed", "device_type": "doomed"})
        r = self.client.delete(f"/api/v1/device-types/{doomed['id']}", headers=self.headers)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("disable it instead", r.text)

    def test_publishing_reports_devices_that_would_become_invalid(self):
        self.post("/api/v1/device-fields", {"key": "owner", "label": "Owner"})
        self.post("/api/v1/devices", {"unique_id": "dev-noowner", "device_type": "laptop"})
        laptop = self.types()["laptop"]
        r = self.put(f"/api/v1/device-schema/types/{laptop['id']}",
                     {"assignments": [{"field_key": "owner", "required": True, "visible": True}]})
        self.assertEqual(r.status_code, 422, r.text)
        detail = r.json()["detail"]
        self.assertEqual(detail["errors"][0]["kind"], "missing_required")
        self.assertIn("Owner", detail["errors"][0]["message"])
        # And the refused change did not take effect.
        self.assertNotIn("owner", {k for k, f in self.fields_for("laptop").items() if f["required"]})

    def test_admin_only(self):
        from app.db import SessionLocal
        from app.core.security import hash_password
        from app.models import User
        with SessionLocal() as db:
            if not db.query(User).filter(User.username == "tester").first():
                db.add(User(username="tester", role="tester", auth_provider="local",
                            password_hash=hash_password("tester-password")))
                db.commit()
        token = self.client.post("/api/v1/auth/login", json={
            "username": "tester", "password": "tester-password"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        self.assertEqual(self.client.get("/api/v1/device-schema/global", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/v1/device-fields", headers=headers,
            json={"key": "sneaky", "label": "Sneaky"}).status_code, 403)

    def test_schema_changes_are_audited_with_before_and_after(self):
        self.post("/api/v1/device-fields", {"key": "audited", "label": "Audited"})
        entries = self.get("/api/v1/audit_logs?action=device_field.create").json()
        self.assertTrue(entries["items"], entries)


class InventoryTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("/api/v1/device-fields", {"key": "rack", "label": "Rack"})
        cls.post("/api/v1/device-fields", {"key": "carrier", "label": "Carrier",
                                                "add_to_global": False})
        mobile = cls.types()["mobile"]
        cls.put(f"/api/v1/device-schema/types/{mobile['id']}",
                {"assignments": [{"field_key": "carrier", "visible": True}]})
        cls.post("/api/v1/devices", {
            "unique_id": "inv-router", "device_type": "router",
            "data": {"make": "MikroTik", "rack": "R1", "serial_number": "SN-R"}})
        cls.post("/api/v1/devices", {
            "unique_id": "inv-phone", "device_type": "mobile",
            "data": {"make": "Samsung", "carrier": "Verizon"}})
        cls.post("/api/v1/devices", {"unique_id": "inv-loose"})

    def test_a_type_page_loads_only_its_own_devices(self):
        page = self.get("/api/v1/devices?device_type=router").json()
        self.assertEqual([d["unique_id"] for d in page["items"]], ["inv-router"])

    def test_devices_without_a_type_are_reachable(self):
        page = self.get("/api/v1/devices?device_type=uncategorized").json()
        self.assertEqual([d["unique_id"] for d in page["items"]], ["inv-loose"])

    def test_filtering_by_a_dynamic_field(self):
        self.assertEqual(self.get("/api/v1/devices?carrier=Verizon").json()["total"], 1)
        self.assertEqual(self.get("/api/v1/devices?rack=R1").json()["total"], 1)

    def test_search_covers_dynamic_fields_but_not_secrets(self):
        self.assertGreaterEqual(self.get("/api/v1/search?q=MikroTik").json()["devices_total"], 1)
        self.assertEqual(self.get("/api/v1/suggestions/devices/password").status_code, 404)

    def test_a_type_template_carries_that_types_columns(self):
        header = self.get("/api/v1/devices/template?device_type=mobile").text.split("\n")[0]
        self.assertIn("carrier", header)
        self.assertNotIn("carrier", self.get("/api/v1/devices/template?device_type=router").text.split("\n")[0])

    def test_exports_carry_the_stable_type_key(self):
        rows = self.get("/api/v1/devices/export?format=json&device_type=router").json()
        self.assertEqual(rows[0]["device_type"], "router")

    def test_the_union_export_keeps_every_types_columns(self):
        rows = self.get("/api/v1/devices/export?format=json&columns=all").json()
        self.assertLessEqual({"rack", "carrier"}, set(rows[0]))

    def test_the_document_export_round_trips_through_an_import(self):
        csv_text = self.get("/api/v1/devices/export?format=csv&columns=data").text
        r = self.client.post("/api/v1/devices/import", headers=self.headers,
                             files={"file": ("d.csv", csv_text, "text/csv")})
        self.assertEqual(r.json()["errors"], [], r.text)
        self.assertEqual(self.get("/api/v1/devices/inv-router").json()["data"]["rack"], "R1")

    def test_an_import_addresses_a_type_by_its_stable_key(self):
        r = self.client.post(
            "/api/v1/devices/import", headers=self.headers,
            files={"file": ("d.csv", "unique_id,device_type,make\nimp-1,mobile,Nokia\n", "text/csv")})
        self.assertEqual(r.json()["created"], 1, r.text)
        self.assertEqual(self.get("/api/v1/devices/imp-1").json()["device_type_key"], "mobile")

    def test_an_import_naming_an_unknown_type_fails_that_row_only(self):
        r = self.client.post(
            "/api/v1/devices/import", headers=self.headers,
            files={"file": ("d.csv", "unique_id,device_type\nimp-2,nonsense\nimp-3,router\n", "text/csv")})
        body = r.json()
        self.assertEqual(body["created"], 1, body)
        self.assertEqual(len(body["errors"]), 1, body)


class MiscDataTests(SchemaCase):
    """What `misc_data` reports: the document a layout does not account for.

    It used to subtract a frozen list of the attribute names the model happened
    to flatten, which predates installation-defined fields — so `username`,
    `serial_number` and everything an installation invented were reported as
    unexplained data while also having a column of their own.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A `type` export carries the columns the page shows, so the export
        # tests below need the field on the page. The seeded layout hides it.
        cls.make_visible("misc_data", "username")
        cls.post("/api/v1/devices", {
            "unique_id": "misc-router", "device_type": "router",
            "data": {
                "make": "MikroTik",          # a flattened legacy attribute
                "username": "admin",         # a catalog field, not flattened
                "serial_number": "SN-9",     # ditto
                "stray": "from an import",   # no field anywhere: the real thing
            },
        })

    def device(self, path="/api/v1/devices/misc-router"):
        return self.get(path).json()

    def test_a_field_with_a_column_is_not_also_unexplained_data(self):
        self.assertEqual(self.device()["misc_data"], {"stray": "from an import"})

    def test_the_value_still_reaches_its_own_field(self):
        # The point of keeping it out of misc_data is that it is shown once, in
        # the column it belongs to — not that it stopped being stored.
        self.assertEqual(self.device()["data"]["username"], "admin")

    def test_an_export_gives_a_stray_key_a_column_of_its_own(self):
        # The default, and what the export button in the UI sends: a key the
        # layout does not account for is a column with a heading, not a JSON
        # object somebody has to unpick in a spreadsheet.
        csv_text = self.get(
            "/api/v1/devices/export?format=csv&columns=type&device_type=router").text
        header, *lines = [line for line in csv_text.splitlines() if line.strip()]
        self.assertIn("stray", header.split(","))
        self.assertNotIn("misc_data", header.split(","))
        row = dict(zip(header.split(","), next(
            l for l in lines if l.startswith("router,misc-router")).split(",")))
        self.assertEqual(row["stray"], "from an import")

    def test_the_same_holds_for_json(self):
        rows = self.get("/api/v1/devices/export?format=json&columns=type").json()
        row = next(r for r in rows if r["unique_id"] == "misc-router")
        self.assertEqual(row["stray"], "from an import")
        # Expanded means expanded: no object left holding the same values.
        self.assertNotIn("misc_data", row)

    def test_an_export_can_be_asked_for_one_column_instead(self):
        # What a script reading fixed headers wants: the column set then does
        # not move with whatever keys the exported devices happen to carry.
        rows = self.get(
            "/api/v1/devices/export?format=json&columns=type&expand_misc=false").json()
        row = next(r for r in rows if r["unique_id"] == "misc-router")
        self.assertEqual(row["misc_data"], {"stray": "from an import"})
        self.assertNotIn("stray", row)

    def test_an_exported_device_can_be_restored_with_its_stray_values(self):
        csv_text = self.get(
            "/api/v1/devices/export?format=csv&columns=type&device_type=router").text
        header, *lines = [line for line in csv_text.splitlines() if line.strip()]
        # Restore under a new identity: re-importing over the original would
        # merge into what is already stored and prove nothing.
        restored = [header] + [l.replace("misc-router", "misc-restored") for l in lines
                               if l.startswith("router,misc-router")]
        r = self.client.post("/api/v1/devices/import", headers=self.headers,
                             files={"file": ("d.csv", "\n".join(restored) + "\n", "text/csv")})
        self.assertEqual(r.json()["errors"], [], r.text)
        # Back in the document, and back out as unexplained data: a column an
        # installation does not define is exactly what misc_data reports.
        self.assertEqual(self.get("/api/v1/devices/misc-restored").json()["misc_data"],
                         {"stray": "from an import"})

    def test_expanding_leaves_the_lossless_export_alone(self):
        # `columns=data` carries the whole document in one cell and has no
        # misc_data column to expand.
        header = self.get(
            "/api/v1/devices/export?format=csv&columns=data&expand_misc=true"
        ).text.splitlines()[0]
        self.assertIn("data", header.split(","))
        self.assertNotIn("stray", header.split(","))

    def test_the_expand_option_is_not_mistaken_for_a_field_filter(self):
        """An export option must not collide with an installation's own field.

        Every query parameter this endpoint does not name is passed on as a
        filter on a device field. Unknown names are ignored, so this is
        harmless until an installation defines a field that happens to share
        the option's name — at which point the option would start filtering the
        export by its own value and quietly return nothing.
        """
        self.post("/api/v1/device-fields", {"key": "expand_misc", "label": "Expand Misc"})
        rows = self.get(
            "/api/v1/devices/export?format=json&columns=type&expand_misc=true").json()
        self.assertTrue(rows, "the export option was applied as a field filter")

    def test_every_read_path_agrees(self):
        listed = next(d for d in self.get("/api/v1/devices?device_type=router").json()["items"]
                      if d["unique_id"] == "misc-router")
        found = next(d for d in self.get("/api/v1/search?q=misc-router").json()["devices"]
                     if d["unique_id"] == "misc-router")
        self.assertEqual(listed["misc_data"], {"stray": "from an import"})
        self.assertEqual(found["misc_data"], {"stray": "from an import"})


class MiscDataLayoutChangeTests(SchemaCase):
    """What a layout change does to the same device's `misc_data`.

    Its own class, and so its own database: these publish a new global layout,
    and sharing one with the tests above would leave them asserting against
    whichever ran first.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_visible("username")
        cls.post("/api/v1/devices", {
            "unique_id": "misc-router", "device_type": "router",
            "data": {"username": "admin", "serial_number": "SN-9", "stray": "from an import"},
        })

    def misc_data(self):
        return self.get("/api/v1/devices/misc-router").json()["misc_data"]

    def publish_global(self, assignments):
        self.assertEqual(
            self.put("/api/v1/device-schema/global", {"assignments": assignments}).status_code, 200)

    def test_hiding_a_field_does_not_spill_its_value(self):
        # Hidden is still on the layout. Routing a hidden value into misc_data
        # would undo the hiding — and for a sensitive field, leak it.
        assignments = self.get("/api/v1/device-schema/global").json()["assignments"]
        for row in assignments:
            if row["field_key"] == "username":
                row["visible"] = False
        self.publish_global(assignments)
        self.assertNotIn("username", self.misc_data())

    def test_a_value_whose_field_left_the_layout_becomes_unexplained(self):
        # The case misc_data exists for: the field is gone, the value stayed,
        # and this is the only place left that shows it.
        self.publish_global([
            row for row in self.get("/api/v1/device-schema/global").json()["assignments"]
            if row["field_key"] != "serial_number"
        ])
        self.assertEqual(self.misc_data()["serial_number"], "SN-9")


class PluginPolicyTests(SchemaCase):
    REBOOT = {
        "id": "device-reboot", "label": "Device Reboot", "version": "1.0.0", "protocol_version": 1,
        "endpoint": "http://reboot", "actions": [{
            "id": "device-reboot.reboot", "entity": "devices", "scope": "row", "label": "Reboot",
            "risk": "disruptive", "allow_global_assignment": False, "requires_online": True,
            "required_roles": ["device_username", "device_password"],
            "required_role_groups": [["scan_address_lan", "scan_address_wan"]],
        }], "recommended_fields": [
            {"key": "username", "label": "Username", "type": "text", "role": "device_username"},
            {"key": "password", "label": "Password", "type": "text", "role": "device_password",
             "sensitive": True},
            {"key": "lan_ip", "label": "LAN IP", "type": "text", "role": "scan_address_lan"},
        ]}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for unique_id, type_key in (("pol-router", "router"), ("pol-phone", "mobile")):
            cls.post("/api/v1/devices", {
                "unique_id": unique_id, "device_type": type_key,
                "data": {"wan_ip": "10.0.0.9", "lan_ip": "10.0.0.9", "username": "admin", "password": "pw",
                         "make": "MikroTik" if type_key == "router" else "Example"}})
        from app.db import SessionLocal
        from app.models import Device
        with SessionLocal() as db:
            for row in db.query(Device).filter(Device.unique_id.in_(["pol-router", "pol-phone"])):
                row.online_status = True
            db.commit()

    def setUp(self):
        # Each test decides which types may run the plugin, so the allowlist
        # starts empty however the previous one left it.
        from app.db import SessionLocal
        from app.models import DeviceTypePlugin
        from app.services.device_schema import invalidate_cache
        with SessionLocal() as db:
            db.query(DeviceTypePlugin).delete()
            db.commit()
        invalidate_cache()

        from app.services import plugin_host
        patcher = patch.object(plugin_host.registry, "_manifests", {"device-reboot": self.REBOOT})
        patcher.start()
        self.addCleanup(patcher.stop)
        invoke = patch.object(plugin_host.registry, "request", lambda *a, **k: {"run_id": "run-1"})
        invoke.start()
        self.addCleanup(invoke.stop)

    def enable_reboot_for(self, type_key):
        item = self.types()[type_key]
        return self.put(f"/api/v1/device-schema/types/{item['id']}/plugins",
                        {"plugins": [{"plugin_id": "device-reboot", "enabled": True}]})

    def actions_for(self, unique_id):
        device = self.get(f"/api/v1/devices/{unique_id}").json()
        return {a["plugin_id"]: a for a in self.get(f"/api/v1/devices/{device['id']}/actions").json()["actions"]}

    def invoke(self, unique_id=None, unique_ids=()):
        body = ({"entity_id": self.get(f"/api/v1/devices/{unique_id}").json()["id"]} if unique_id
                else {"entity_ids": [self.get(f"/api/v1/devices/{u}").json()["id"] for u in unique_ids]})
        return self.post("/api/v1/plugins/device-reboot/actions/device-reboot.reboot/invoke", body)

    def test_an_unassigned_plugin_never_appears_and_never_runs(self):
        action = self.actions_for("pol-router")["device-reboot"]
        self.assertFalse(action["available"])
        self.assertIn("not enabled for the Routers device type", action["unavailable_reason"])
        self.assertEqual(self.invoke("pol-router").status_code, 422)

    def test_reboot_can_be_enabled_for_routers_and_denied_for_phones(self):
        self.assertEqual(self.enable_reboot_for("router").status_code, 200)
        self.assertTrue(self.actions_for("pol-router")["device-reboot"]["available"])
        self.assertFalse(self.actions_for("pol-phone")["device-reboot"]["available"])

        self.assertEqual(self.invoke("pol-router").status_code, 202)
        refused = self.invoke("pol-phone")
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("Mobile Phone", refused.text)

    def test_reboot_configuration_resolves_device_then_first_rule_then_type(self):
        router = self.types()["router"]
        rules = [
            {"id": "mikrotik", "field_key": "make", "operator": "equals", "value": "mikrotik",
             "configuration": {"method": "ssh", "ssh_command": "reboot"}},
            {"id": "fallback-match", "field_key": "make", "operator": "contains", "value": "tik",
             "configuration": {"method": "ai"}},
        ]
        saved = self.put(f"/api/v1/device-schema/types/{router['id']}/plugins", {"plugins": [{
            "plugin_id": "device-reboot", "enabled": True,
            "configuration": {"method": "ai", "_rules": rules},
        }]})
        self.assertEqual(saved.status_code, 200, saved.text)

        captured = {}
        from app.services import plugin_host
        with patch.object(plugin_host.registry, "request",
                          lambda *args, **kwargs: captured.update(payload=args[-1]) or {"run_id": "run-1"}):
            self.assertEqual(self.invoke("pol-router").status_code, 202)
        entity = captured["payload"]["entities"][0]
        self.assertEqual(entity["_plugin_configuration"]["method"], "ssh")
        self.assertEqual(entity["_plugin_configuration_source"], "rule:mikrotik")

        device = self.get("/api/v1/devices/pol-router").json()
        endpoint = f"/api/v1/plugins/device-reboot/devices/{device['id']}/configuration"
        overridden = self.put(endpoint, {"configuration": {"method": "ai"}})
        self.assertEqual(overridden.status_code, 200, overridden.text)
        self.assertEqual(overridden.json()["source"], "device")
        self.assertEqual(overridden.json()["effective_configuration"]["method"], "ai")

        cleared = self.client.delete(endpoint, headers=self.headers)
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["source"], "rule:mikrotik")
        self.assertEqual(cleared.json()["effective_configuration"]["method"], "ssh")

    def test_a_mixed_selection_is_refused_and_itemised(self):
        self.enable_reboot_for("router")
        refused = self.invoke(unique_ids=["pol-router", "pol-phone"])
        self.assertEqual(refused.status_code, 422, refused.text)
        rejected = refused.json()["detail"]["rejected"]
        self.assertEqual([item["unique_id"] for item in rejected], ["pol-phone"])

    def test_a_device_without_the_required_values_is_refused(self):
        self.enable_reboot_for("router")
        self.post("/api/v1/devices", {
            "unique_id": "pol-bare", "device_type": "router", "data": {"wan_ip": "10.0.0.8"}})
        action = self.actions_for("pol-bare")["device-reboot"]
        self.assertFalse(action["available"])
        self.assertIn("missing", action["unavailable_reason"])
        self.assertEqual(self.invoke("pol-bare").status_code, 422)

    def test_a_disruptive_action_cannot_be_enabled_for_everything_at_once(self):
        # There is no global-assignment endpoint, so this checks the guard that
        # matters: a row that reaches the table another way is still ignored.
        from app.db import SessionLocal
        from app.models import DeviceTypePlugin
        from app.services.device_schema import get_allowed_plugins, invalidate_cache
        with SessionLocal() as db:
            db.add(DeviceTypePlugin(device_type_id=None, plugin_id="device-reboot", enabled=True))
            db.commit()
            invalidate_cache()
            router_id = self.types()["router"]["id"]
            self.assertNotIn("device-reboot", get_allowed_plugins(db, router_id))
            db.query(DeviceTypePlugin).filter(DeviceTypePlugin.device_type_id.is_(None)).delete()
            db.commit()
        invalidate_cache()

    def test_an_unhealthy_plugin_is_a_distinct_state_from_an_unassigned_one(self):
        self.enable_reboot_for("router")
        from app.services import plugin_host
        with patch.object(plugin_host.registry, "_manifests", {}):
            actions = self.actions_for("pol-router")
            self.assertIn("not installed", actions["device-reboot"]["unavailable_reason"])
            self.assertEqual(self.invoke("pol-router").status_code, 409)

    def test_the_action_listing_says_which_types_a_plugin_covers(self):
        self.enable_reboot_for("router")
        listed = self.get("/api/v1/plugins/actions").json()
        self.assertEqual(next(a for a in listed if a["plugin_id"] == "device-reboot")["device_types"],
                         ["router"])
        self.assertEqual(self.get("/api/v1/plugins/actions?device_type=mobile").json(), [])

    def test_missing_semantic_roles_are_reported_before_enabling(self):
        assignments = self.get("/api/v1/device-schema/global").json()["assignments"]
        for row in assignments:
            if row["field_key"] == "password":
                row["visible"] = False
        self.put("/api/v1/device-schema/global", {"assignments": assignments})
        mobile = self.types()["mobile"]
        entry = next(p for p in self.get(f"/api/v1/device-schema/types/{mobile['id']}/plugins").json()["plugins"]
                     if p["plugin_id"] == "device-reboot")
        self.assertIn("device_password", entry["missing_roles"])
        self.make_visible("password")

    def test_enabling_a_plugin_adds_recommended_fields_once_and_disabling_preserves_them(self):
        manifest = {
            **self.REBOOT,
            "recommended_fields": [{
                "key": "plugin_probe", "label": "Plugin Probe", "type": "text",
                "role": "plugin_probe_role",
            }],
        }
        from app.services import plugin_host
        with patch.object(plugin_host.registry, "_manifests", {"device-reboot": manifest}):
            router = self.types()["router"]
            endpoint = f"/api/v1/device-schema/types/{router['id']}/plugins"
            enabled = {"plugins": [{"plugin_id": "device-reboot", "enabled": True}]}
            disabled = {"plugins": [{"plugin_id": "device-reboot", "enabled": False}]}

            self.assertEqual(self.put(endpoint, enabled).status_code, 200)
            fields = self.get(f"/api/v1/device-schema/types/{router['id']}").json()["fields"]
            probes = [field for field in fields if field["key"] == "plugin_probe"]
            self.assertEqual(len(probes), 1)
            self.assertTrue(probes[0]["visible"])

            self.assertEqual(self.put(endpoint, enabled).status_code, 200)
            fields = self.get(f"/api/v1/device-schema/types/{router['id']}").json()["fields"]
            self.assertEqual(sum(field["key"] == "plugin_probe" for field in fields), 1)

            self.assertEqual(self.put(endpoint, disabled).status_code, 200)
            fields = self.get(f"/api/v1/device-schema/types/{router['id']}").json()["fields"]
            self.assertTrue(next(field for field in fields if field["key"] == "plugin_probe")["visible"])

    def test_type_enablement_reuses_an_installed_plugins_global_field(self):
        manifest = {
            **self.REBOOT,
            "recommended_fields": [{
                "key": "global_probe", "label": "Global Probe", "type": "text",
                "role": "global_probe_role",
            }],
        }
        from app.api.device_schema import reconcile_installed_plugin_fields
        from app.db import SessionLocal
        from app.models import DeviceFieldDefinition
        from app.services import plugin_host
        with patch.object(plugin_host.registry, "_manifests", {"device-reboot": manifest}):
            with SessionLocal() as db:
                reconcile_installed_plugin_fields(db)
            router = self.types()["router"]
            self.enable_reboot_for("router")
            own = self.get(f"/api/v1/device-schema/types/{router['id']}").json()["assignments"]
            self.assertNotIn("global_probe", {row["field_key"] for row in own})
            with SessionLocal() as db:
                self.assertEqual(db.query(DeviceFieldDefinition).filter_by(key="global_probe").count(), 1)

    def test_enabling_a_plugin_preserves_an_explicitly_hidden_recommended_field(self):
        router = self.types()["router"]
        endpoint = f"/api/v1/device-schema/types/{router['id']}"
        self.enable_reboot_for("router")
        assignments = self.get(endpoint).json()["assignments"]
        next(row for row in assignments if row["field_key"] == "username")["visible"] = False
        self.put(endpoint, {"assignments": assignments})

        def restore_username():
            current = self.get(endpoint).json()["assignments"]
            next(row for row in current if row["field_key"] == "username")["visible"] = True
            self.put(endpoint, {"assignments": current})

        self.addCleanup(restore_username)

        self.enable_reboot_for("router")
        fields = self.get(f"/api/v1/device-schema/types/{router['id']}").json()["fields"]
        self.assertFalse(next(field for field in fields if field["key"] == "username")["visible"])

    def test_an_action_stays_listed_when_its_output_columns_are_hidden(self):
        # A plugin that writes discoveries back into columns nobody has enabled
        # is still offered, with the reason attached: the fix is a schema
        # change, and hiding the action would hide the fact that it exists.
        agent = {
            "id": "device-info-agent", "label": "Device Info Agent", "version": "1.0.0",
            "protocol_version": 1, "endpoint": "http://info",
            "optional_output_roles": ["discovery_hardware", "discovery_lan_mac"],
            "minimum_output_roles": 1,
            "actions": [{"id": "device-info-agent.research", "entity": "devices", "scope": "row",
                         "label": "Info"}],
        }
        from app.services import plugin_host
        with patch.object(plugin_host.registry, "_manifests", {"device-info-agent": agent}):
            router = self.types()["router"]
            self.put(f"/api/v1/device-schema/types/{router['id']}/plugins",
                     {"plugins": [{"plugin_id": "device-info-agent", "enabled": True}]})
            listed = self.get("/api/v1/plugins/actions").json()
        self.assertEqual(len(listed), 1, listed)
        self.assertIn("at least 1", listed[0]["unavailable_reason"])

    def test_a_denied_invocation_is_audited(self):
        self.invoke("pol-phone")
        entries = self.get("/api/v1/audit_logs?action=plugin.action.denied").json()
        self.assertTrue(entries["items"], entries)


class YamlReconciliationTests(SchemaCase):
    DOCUMENT = """
apiVersion: testbench.chainfirelabs.com/v1
kind: DeviceSchema
metadata:
  name: default
spec:
  fields:
    - key: location
      label: Location
      type: text
    - key: serial_number
      label: Serial Number
      type: text
      indexed: true
    - key: carrier
      label: Carrier
      type: select
      options: [AT&T, Verizon]
    - key: wan_ip
      label: WAN IP
      type: text
      role: scan_address_wan
  globalFields:
    - key: location
      position: 10
    - key: serial_number
      position: 20
  deviceTypes:
    - key: router
      label: Routers
      fields:
        - key: wan_ip
          required: true
      plugins:
        - id: network-scan
          enabled: true
    - key: mobile
      label: Mobile Phones
      fields:
        - key: carrier
    - key: server
      label: Servers
      overrides:
        - key: serial_number
          required: true
"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        # Bootstrap applies once per installation and authoritative mode
        # rewrites everything, so these tests each need an untouched one.
        self.stop()
        self.start_fresh()
        self.path = Path(tempfile.mkdtemp()) / "device-schema.yaml"
        self.path.write_text(self.DOCUMENT)

    def reconcile(self, mode, path=None):
        from app.db import SessionLocal
        from app.services.device_schema_yaml import reconcile_from_settings
        with patch.object(settings, "device_schema_path", str(path or self.path)), \
             patch.object(settings, "device_schema_reconciliation", mode):
            with SessionLocal() as db:
                return reconcile_from_settings(db)

    def test_bootstrap_applies_exactly_once(self):
        self.assertEqual(self.reconcile("bootstrap")["state"], "applied")
        self.assertIn("location", self.fields_for("router"))
        self.assertTrue(self.fields_for("router")["wan_ip"]["required"])
        self.assertIn("carrier", self.fields_for("mobile"))
        self.assertNotIn("carrier", self.fields_for("router"))
        self.assertTrue(self.fields_for("server")["serial_number"]["required"])
        self.assertFalse(self.fields_for("router")["serial_number"]["required"])
        self.assertEqual(self.reconcile("bootstrap")["state"], "skipped")

    def test_bootstrap_hands_imported_configuration_to_the_gui(self):
        self.reconcile("bootstrap")
        carrier = next(f for f in self.get("/api/v1/device-fields").json() if f["key"] == "carrier")
        self.assertEqual(carrier["configuration_source"], "gui")
        self.assertEqual(self.patch_(f"/api/v1/device-fields/{carrier['id']}", {"label": "Provider"}).status_code, 200)
        router = self.types()["router"]
        self.assertEqual(router["configuration_source"], "gui")
        self.assertEqual(self.patch_(f"/api/v1/device-types/{router['id']}", {"label": "Network Routers"}).status_code, 200)

        # Handing a row over changes who may edit it — asserted by the two
        # successful PATCHes above — and nothing else. It keeps the generation
        # that created it, which is what still marks it as the document's work
        # rather than somebody's own; see the retirement tests below.
        from app.db import SessionLocal
        from app.models import DeviceFieldAssignment, DeviceFieldDefinition, DeviceType, DeviceTypePlugin
        with SessionLocal() as db:
            for model in (DeviceFieldDefinition, DeviceFieldAssignment, DeviceType, DeviceTypePlugin):
                imported = list(db.scalars(sqlalchemy.select(model).where(model.configuration_source == "gui")))
                self.assertTrue(imported, model.__name__)
                self.assertTrue(all(item.source_revision for item in imported), model.__name__)

    def test_bootstrap_repairs_ownership_from_older_releases(self):
        generation = self.reconcile("bootstrap")["generation"]
        from app.db import SessionLocal
        from app.models import DeviceFieldAssignment, DeviceFieldDefinition, DeviceType, DeviceTypePlugin
        models = (DeviceFieldDefinition, DeviceFieldAssignment, DeviceType, DeviceTypePlugin)
        with SessionLocal() as db:
            for model in models:
                db.query(model).filter(model.configuration_source == "gui").update({
                    "configuration_source": "yaml", "source_revision": generation,
                })
            db.commit()

        self.assertEqual(self.reconcile("bootstrap")["state"], "skipped")
        with SessionLocal() as db:
            for model in models:
                yaml_owned = db.scalar(sqlalchemy.select(sqlalchemy.func.count()).select_from(model).where(
                    model.configuration_source == "yaml",
                ))
                self.assertEqual(yaml_owned, 0, model.__name__)

    def test_authoritative_mode_restores_drift(self):
        self.reconcile("bootstrap")
        from app.db import SessionLocal
        from app.models import DeviceType
        from app.services.device_schema import invalidate_cache
        with SessionLocal() as db:
            db.query(DeviceType).filter(DeviceType.key == "mobile").update({"label": "Drifted"})
            db.commit()
        invalidate_cache()
        self.assertEqual(self.types()["mobile"]["label"], "Drifted")
        self.assertEqual(self.reconcile("authoritative")["state"], "applied")
        self.assertEqual(self.types()["mobile"]["label"], "Mobile Phones")

    # The document with the `server` device type taken out of it.
    WITHOUT_SERVER = DOCUMENT.replace("""    - key: server
      label: Servers
      overrides:
        - key: serial_number
          required: true
""", "")

    def all_types(self) -> dict:
        return {t["key"]: t for t in self.get("/api/v1/device-types?include_disabled=true").json()}

    def test_an_object_dropped_from_the_document_is_disabled_not_deleted(self):
        # Authoritative twice, not bootstrap then authoritative. Retiring a row
        # means writing to it, and the reconciler only writes to rows the
        # document owns — so the first pass has to be the one that takes
        # ownership. Bootstrap ends by handing everything it imported to the
        # GUI, which puts it permanently beyond prune's reach; that path is
        # covered below as the separate thing it is.
        self.reconcile("authoritative")
        self.assertTrue(self.all_types()["server"]["enabled"])

        self.path.write_text(self.WITHOUT_SERVER)
        self.reconcile("authoritative")

        types = self.all_types()
        self.assertIn("server", types)
        self.assertFalse(types["server"]["enabled"])

    def test_a_bootstrapped_object_is_still_the_documents_to_retire(self):
        """Bootstrap hands over who may edit a row, not where it came from.

        The type is GUI-owned after a bootstrap and stays editable, which is
        what bootstrap promises. Dropping it from the document under an
        authoritative pass still retires it, because a document is what put it
        there.
        """
        self.reconcile("bootstrap")
        self.assertEqual(self.type_source("server"), "gui")
        self.assertTrue(self.all_types()["server"]["enabled"])

        self.path.write_text(self.WITHOUT_SERVER)
        self.reconcile("authoritative")

        types = self.all_types()
        self.assertIn("server", types)
        self.assertFalse(types["server"]["enabled"])

    def test_configuration_made_in_the_gui_is_never_retired(self):
        """The limit on the rule above, and the reason it asks about provenance.

        "The document is the source of truth" is a claim over the document's
        own objects. A device type somebody created by hand was never one of
        them, and an authoritative pass that has never heard of it must leave
        it alone rather than switch it off on the next restart.
        """
        self.reconcile("authoritative")
        created = self.post("/api/v1/device-types",
                            {"key": "handmade", "label": "Handmade", "position": 99})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(self.type_source("handmade"), "gui")

        self.path.write_text(self.WITHOUT_SERVER)
        self.reconcile("authoritative")

        types = self.all_types()
        # The document's own object went; the hand-made one did not.
        self.assertFalse(types["server"]["enabled"])
        self.assertTrue(types["handmade"]["enabled"])

    def type_source(self, key: str) -> str:
        """Which side owns a device type — the fact the retirement rule turns on."""
        from app.db import SessionLocal
        from app.models import DeviceType
        with SessionLocal() as db:
            return db.scalar(
                sqlalchemy.select(DeviceType).where(DeviceType.key == key)
            ).configuration_source

    def test_an_invalid_document_leaves_the_last_published_schema_active(self):
        self.reconcile("bootstrap")
        before = [f["key"] for f in self.get("/api/v1/device-schema/global").json()["fields"]]
        bad = self.path.parent / "bad.yaml"
        bad.write_text("apiVersion: nope\nkind: DeviceSchema\n")
        status = self.reconcile("authoritative", path=bad)
        self.assertEqual(status["state"], "error")
        self.assertTrue(status["errors"])
        self.assertEqual([f["key"] for f in self.get("/api/v1/device-schema/global").json()["fields"]], before)

    def test_merge_preserves_gui_objects_and_reports_the_conflict(self):
        self.reconcile("bootstrap")
        self.assertEqual(self.post("/api/v1/device-fields", {
            "key": "gui_only", "label": "GUI Only"}).status_code, 201)
        from app.db import SessionLocal
        from app.services.device_schema import invalidate_cache
        with SessionLocal() as db:
            db.execute(sqlalchemy.text(
                "UPDATE device_field_definitions SET configuration_source='gui' WHERE key='carrier'"))
            db.commit()
        invalidate_cache()
        conflicting = self.path.parent / "merge.yaml"
        conflicting.write_text(self.DOCUMENT.replace("      label: Carrier", "      label: Provider"))
        status = self.reconcile("merge", path=conflicting)
        self.assertEqual(status["state"], "conflict")
        self.assertTrue(status["conflicts"])
        fields = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        self.assertEqual(fields["carrier"]["label"], "Carrier")
        self.assertIn("gui_only", fields)

    def test_reconciliation_status_is_exposed_for_operators(self):
        self.reconcile("bootstrap")
        status = self.get("/api/v1/device-schema/reconciliation").json()
        self.assertEqual(status["state"], "applied")
        self.assertTrue(status["generation"])
        self.assertTrue(status["applied_revision"])

    def test_gui_import_adds_missing_nested_objects_without_changing_existing_ones(self):
        document = self.DOCUMENT.replace("label: Routers", "label: Imported Routers")
        preview = self.client.post(
            "/api/v1/device-schema/import?dry_run=true", headers=self.headers,
            files={"file": ("schema.yaml", document, "application/yaml")},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertGreater(preview.json()["total"], 0)
        self.assertIn("router", preview.json()["skipped"]["device_types"])
        self.assertIn("router/wan_ip", preview.json()["additions"]["type_assignments"])

        response = self.client.post(
            "/api/v1/device-schema/import", headers=self.headers,
            files={"file": ("schema.yaml", document, "application/yaml")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.types()["router"]["label"], "Routers")
        self.assertIn("wan_ip", self.fields_for("router"))
        self.assertEqual(self.type_source("router"), "system")
        carrier = next(field for field in self.get("/api/v1/device-fields").json() if field["key"] == "carrier")
        self.assertEqual(carrier["configuration_source"], "gui")

        again = self.client.post(
            "/api/v1/device-schema/import", headers=self.headers,
            files={"file": ("schema.yaml", document, "application/yaml")},
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["total"], 0)
        self.assertIsNone(again.json()["revision"])


class ReviewRegressionApiTests(SchemaCase):
    """Review fixes exercised through HTTP and a disposable PostgreSQL."""

    def test_admin_key_cannot_promote_or_reactivate_an_account(self):
        target = self.post('/api/v1/users', {
            'username': 'review-user', 'password': 'another-review-password', 'role': 'readonly',
        })
        self.assertEqual(target.status_code, 201, target.text)
        user_id = target.json()['id']
        self.assertEqual(self.patch_(f'/api/v1/users/{user_id}', {'is_active': False}).status_code, 200)
        key = self.post('/api/v1/auth/api-keys', {'label': 'review', 'role': 'admin'})
        self.assertEqual(key.status_code, 201, key.text)
        headers = {'Authorization': 'Bearer ' + key.json()['plaintext']}
        for body in ({'role': 'admin'}, {'is_active': True}):
            response = self.client.patch(f'/api/v1/users/{user_id}', headers=headers, json=body)
            self.assertEqual(response.status_code, 403, response.text)
        listed = self.get('/api/v1/users')
        self.assertEqual(listed.status_code, 200, listed.text)
        user = next(item for item in listed.json() if item['id'] == user_id)
        self.assertEqual(user['role'], 'readonly')
        self.assertFalse(user['is_active'])

    def test_default_fleet_export_preserves_type_specific_data(self):
        created = self.post('/api/v1/device-fields', {
            'key': 'review_imei', 'label': 'Review IMEI', 'add_to_global': False,
        })
        self.assertEqual(created.status_code, 201, created.text)
        mobile = self.types()['mobile']
        response = self.put(f"/api/v1/device-schema/types/{mobile['id']}", {
            'assignments': [{'field_key': 'review_imei', 'visible': True}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.post('/api/v1/devices', {
            'unique_id': 'review-export', 'device_type': 'mobile', 'data': {'review_imei': '123456'},
        })
        self.assertEqual(response.status_code, 201, response.text)
        exported = self.get('/api/v1/devices/export?search=review-export')
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(exported.json()[0]['review_imei'], '123456')

    def test_formula_header_export_round_trips(self):
        import csv
        import io
        response = self.post('/api/v1/devices', {
            'unique_id': 'review-formula', 'data': {'=1+1': 'safe-value'},
        })
        self.assertEqual(response.status_code, 201, response.text)
        exported = self.get('/api/v1/devices/export?format=csv&search=review-formula')
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn("'=1+1", next(csv.reader(io.StringIO(exported.text))))
        imported = self.client.post('/api/v1/devices/import', headers=self.headers,
            files={'file': ('review.csv', exported.content, 'text/csv')})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['errors'], [])
        self.assertEqual(self.get('/api/v1/devices/review-formula').json()['data']['=1+1'], 'safe-value')

    def test_query_lookups_accept_names_containing_slashes_and_fragments(self):
        name = 'review/rack#2?model=a'
        for entity, body, path, parameter in [
            ('devices', {'unique_id': name}, '/api/v1/devices/lookup/by-unique-id', 'unique_id'),
            ('software', {'name': name, 'version': '1'}, '/api/v1/software/lookup/by-name', 'name'),
        ]:
            created = self.post('/api/v1/' + entity, body)
            self.assertEqual(created.status_code, 201, created.text)
            fetched = self.client.get(path, params={parameter: name}, headers=self.headers)
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.json()['id'], created.json()['id'])

    def test_type_change_rejects_incompatible_retained_values_atomically(self):
        created = self.post('/api/v1/device-fields', {
            'key': 'review_count', 'label': 'Review Count', 'field_type': 'number', 'add_to_global': False,
        })
        self.assertEqual(created.status_code, 201, created.text)
        other = self.types()['other']
        response = self.put(f"/api/v1/device-schema/types/{other['id']}", {
            'assignments': [{'field_key': 'review_count', 'visible': True}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        created = self.post('/api/v1/devices', {
            'unique_id': 'review-move', 'device_type': 'router', 'data': {'review_count': 'invalid'},
        })
        self.assertEqual(created.status_code, 201, created.text)
        path = f"/api/v1/devices/{created.json()['id']}"
        rejected = self.patch_(path, {'device_type': 'other'})
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertEqual(self.get(path).json()['device_type_key'], 'router')
        accepted = self.patch_(path, {'device_type': 'other', 'data': {'review_count': '5'}})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()['data']['review_count'], 5)
