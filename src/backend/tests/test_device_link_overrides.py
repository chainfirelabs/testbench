"""The two layers behind an address link: the field's default, the device's override.

The override exists because the value cannot carry this. A field with a scan
address role has its value read raw by `services/scan.py` and by the reboot
plugin, which hand it to `socket.create_connection` — a scheme or a port stored
inside the value takes the device offline. These tests pin the override to its
own envelope key and out of the document for that reason.
"""

from test_device_schema_api import SchemaCase


class FieldLinkDefaultTests(SchemaCase):
    def test_a_linked_field_defaults_to_plain_http_on_no_particular_port(self):
        self.make_visible("lan_ip")
        self.assertEqual(self.fields_for("router")["lan_ip"]["link_scheme"], "http")
        self.assertIsNone(self.fields_for("router")["lan_ip"]["link_port"])

    def test_a_field_can_be_created_linking_to_https_on_a_port(self):
        created = self.post("/api/v1/device-fields", {
            "key": "portal", "label": "Portal", "field_type": "text",
            "opens_web_page": True, "link_scheme": "https", "link_port": 8443,
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["link_scheme"], "https")
        self.assertEqual(created.json()["link_port"], 8443)
        published = self.fields_for("router")["portal"]
        self.assertEqual(published["link_scheme"], "https")
        self.assertEqual(published["link_port"], 8443)

    def test_a_scheme_outside_the_whitelist_is_refused(self):
        """`javascript:` is not something an administrator may configure either."""
        refused = self.post("/api/v1/device-fields", {
            "key": "bad_scheme", "label": "Bad", "field_type": "text",
            "opens_web_page": True, "link_scheme": "javascript",
        })
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_a_port_outside_the_range_is_refused(self):
        refused = self.post("/api/v1/device-fields", {
            "key": "bad_port", "label": "Bad", "field_type": "text",
            "opens_web_page": True, "link_port": 70000,
        })
        self.assertEqual(refused.status_code, 422, refused.text)


class FieldLinkDefaultUpdateTests(SchemaCase):
    """Its own database: this one changes a field the class above asserts ships plain."""

    def test_an_existing_field_can_be_pointed_at_https(self):
        self.make_visible("lan_ip")
        definitions = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        self.assertEqual(definitions["lan_ip"]["link_scheme"], "http", "precondition")

        updated = self.patch_(
            f"/api/v1/device-fields/{definitions['lan_ip']['id']}",
            {"link_scheme": "https", "link_port": 8443},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["link_scheme"], "https")
        self.assertEqual(self.fields_for("router")["lan_ip"]["link_port"], 8443)


class DeviceLinkOverrideTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # An installation turns its address fields on, and the changelog reads
        # its labels from the published schema: without this the entries below
        # would fall back to the field key, which is a different test.
        cls.make_visible("lan_ip", "wan_ip")

    @classmethod
    def a_device(cls, unique_id, **fields):
        return cls.post("/api/v1/devices", {"unique_id": unique_id, **fields}).json()

    def test_a_device_starts_with_none(self):
        self.assertEqual(self.a_device("lo-none")["link_overrides"], {})

    def test_one_device_can_override_the_scheme_and_port(self):
        device = self.a_device("lo-one", lan_ip="10.0.0.5")
        updated = self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https", "port": 8443}},
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            updated.json()["link_overrides"], {"lan_ip": {"scheme": "https", "port": 8443}},
        )

    def test_the_override_stays_out_of_the_device_document(self):
        """The whole point: the address the scanner is handed stays an address."""
        device = self.a_device("lo-document", lan_ip="10.0.0.6")
        self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https"}},
        })
        after = self.get(f"/api/v1/devices/{device['id']}").json()
        self.assertEqual(after["lan_ip"], "10.0.0.6")
        self.assertNotIn("link_overrides", after.get("misc_data") or {})

    def test_an_override_naming_only_a_port_is_kept_as_one(self):
        device = self.a_device("lo-port")
        updated = self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"port": 8080}},
        })
        self.assertEqual(updated.json()["link_overrides"], {"lan_ip": {"port": 8080}})

    def test_an_empty_override_is_dropped_rather_than_stored(self):
        """Clearing both controls removes the override instead of leaving a husk."""
        device = self.a_device("lo-empty")
        updated = self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "", "port": ""}},
        })
        self.assertEqual(updated.json()["link_overrides"], {})

    def test_overrides_are_replaced_wholesale_not_merged(self):
        device = self.a_device("lo-replace")
        self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https"}, "wan_ip": {"port": 8080}},
        })
        updated = self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https"}},
        })
        self.assertEqual(updated.json()["link_overrides"], {"lan_ip": {"scheme": "https"}})

    def test_an_update_that_does_not_mention_them_leaves_them_alone(self):
        device = self.a_device("lo-untouched", lan_ip="10.0.0.7")
        self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https"}},
        })
        updated = self.patch_(f"/api/v1/devices/{device['id']}", {"model": "R650"})
        self.assertEqual(updated.json()["link_overrides"], {"lan_ip": {"scheme": "https"}})

    def test_a_scheme_outside_the_whitelist_is_refused(self):
        device = self.a_device("lo-bad-scheme")
        refused = self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "javascript"}},
        })
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_a_port_outside_the_range_is_refused(self):
        device = self.a_device("lo-bad-port")
        refused = self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"port": 0}},
        })
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_they_can_be_set_when_the_device_is_created(self):
        device = self.post("/api/v1/devices", {
            "unique_id": "lo-created", "lan_ip": "10.0.0.10",
            "link_overrides": {"lan_ip": {"scheme": "https"}},
        })
        self.assertEqual(device.status_code, 201, device.text)
        self.assertEqual(device.json()["link_overrides"], {"lan_ip": {"scheme": "https"}})
        # And the address is still just an address.
        self.assertEqual(device.json()["lan_ip"], "10.0.0.10")

    def test_they_survive_a_raw_export_and_reimport(self):
        """Otherwise restoring a fleet quietly puts every link back on http."""
        import io as _io
        import json as _json

        device = self.a_device("lo-roundtrip", lan_ip="10.0.0.11")
        self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https", "port": 8443}},
        })
        exported = _json.loads(self.get("/api/v1/devices/export?format=json&columns=data").text)
        row = next(item for item in exported if item["unique_id"] == "lo-roundtrip")
        self.assertEqual(row["link_overrides"], {"lan_ip": {"scheme": "https", "port": 8443}})

        # Cleared, then restored from the file.
        self.patch_(f"/api/v1/devices/{device['id']}", {"link_overrides": {}})
        self.assertEqual(self.get(f"/api/v1/devices/{device['id']}").json()["link_overrides"], {})
        upload = self.client.post(
            "/api/v1/devices/import", headers=self.headers,
            files={"file": ("devices.json", _io.BytesIO(_json.dumps(exported).encode()), "application/json")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertEqual(upload.json()["errors"], [])
        restored = self.get(f"/api/v1/devices/{device['id']}").json()
        self.assertEqual(restored["link_overrides"], {"lan_ip": {"scheme": "https", "port": 8443}})

    def test_an_import_that_does_not_mention_them_leaves_them_alone(self):
        """A CSV written against the field columns is not a request to clear them."""
        import io as _io
        import json as _json

        device = self.a_device("lo-quiet-import", lan_ip="10.0.0.12")
        self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https"}},
        })
        rows = [{"unique_id": "lo-quiet-import", "model": "R650"}]
        upload = self.client.post(
            "/api/v1/devices/import", headers=self.headers,
            files={"file": ("devices.json", _io.BytesIO(_json.dumps(rows).encode()), "application/json")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        after = self.get(f"/api/v1/devices/{device['id']}").json()
        self.assertEqual(after["model"], "R650")
        self.assertEqual(after["link_overrides"], {"lan_ip": {"scheme": "https"}})

    def test_a_change_to_one_is_recorded_in_the_changelog(self):
        device = self.a_device("lo-changelog", lan_ip="10.0.0.8")
        self.patch_(f"/api/v1/devices/{device['id']}", {
            "link_overrides": {"lan_ip": {"scheme": "https", "port": 8443}},
        })
        entry = self.get(f"/api/v1/devices/{device['id']}/changelog").json()["items"][0]
        self.assertEqual(entry["action"], "device.update")
        self.assertEqual(entry["changes"], [
            {"field": "lan_ip", "label": "LAN IP link",
             "old": "default", "new": "https://…:8443"},
        ])
        # And it is not reported as the address itself moving.
        self.assertNotIn("Device updated: LAN IP,", entry["summary"])


class FieldLinkYamlTests(SchemaCase):
    """The field's default survives a DeviceSchema export/import round trip."""

    def test_it_round_trips_through_the_bootstrap_document(self):
        import yaml

        self.post("/api/v1/device-fields", {
            "key": "portal_url", "label": "Portal", "field_type": "text",
            "opens_web_page": True, "link_scheme": "https", "link_port": 8443,
        })
        config_map = yaml.safe_load(self.get("/api/v1/device-schema/export").text)
        exported = yaml.safe_load(config_map["data"]["device-schema.yaml"])
        fields = {f["key"]: f for f in exported["spec"]["fields"]}
        self.assertEqual(fields["portal_url"]["linkScheme"], "https")
        self.assertEqual(fields["portal_url"]["linkPort"], 8443)

    def test_a_field_on_the_defaults_carries_neither_key(self):
        """The document stays as small as what it is actually saying."""
        import yaml

        self.make_visible("lan_ip")
        config_map = yaml.safe_load(self.get("/api/v1/device-schema/export").text)
        exported = yaml.safe_load(config_map["data"]["device-schema.yaml"])
        fields = {f["key"]: f for f in exported["spec"]["fields"]}
        self.assertNotIn("linkScheme", fields["lan_ip"])
        self.assertNotIn("linkPort", fields["lan_ip"])
