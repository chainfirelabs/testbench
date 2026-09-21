"""The per-field `opens_web_page` switch behind the device address links."""

from test_device_schema_api import SchemaCase


class FieldWebLinkTests(SchemaCase):
    def test_scan_address_fields_are_seeded_with_it_on(self):
        """So an existing installation keeps its address links without opting in."""
        self.make_visible("wan_ip", "lan_ip")
        fields = self.fields_for("router")
        self.assertTrue(fields["wan_ip"]["opens_web_page"])
        self.assertTrue(fields["lan_ip"]["opens_web_page"])

    def test_an_ordinary_field_is_seeded_with_it_off(self):
        self.make_visible("location")
        self.assertFalse(self.fields_for("router")["location"]["opens_web_page"])

    def test_any_field_can_be_opted_in(self):
        """It is not tied to the scan roles: a vendor page is not an address."""
        created = self.post("/api/v1/device-fields", {
            "key": "vendor_page", "label": "Vendor Page", "field_type": "text",
            "opens_web_page": True,
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue(created.json()["opens_web_page"])
        self.assertTrue(self.fields_for("router")["vendor_page"]["opens_web_page"])

    def test_it_defaults_off_for_a_field_created_without_it(self):
        created = self.post("/api/v1/device-fields", {
            "key": "rack_note", "label": "Rack Note", "field_type": "text",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["opens_web_page"])


class FieldWebLinkOptOutTests(SchemaCase):
    """Its own class, and so its own database: this one mutates a seeded field,
    and the tests above assert on what that field ships as."""

    def test_an_address_field_can_be_opted_out(self):
        self.make_visible("lan_ip")
        definitions = {f["key"]: f for f in self.get("/api/v1/device-fields").json()}
        self.assertTrue(definitions["lan_ip"]["opens_web_page"], "precondition: it starts on")

        updated = self.patch_(
            f"/api/v1/device-fields/{definitions['lan_ip']['id']}",
            {"opens_web_page": False},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertFalse(updated.json()["opens_web_page"])
        # And the published schema the UI renders from agrees.
        self.assertFalse(self.fields_for("router")["lan_ip"]["opens_web_page"])


class FieldWebLinkYamlTests(SchemaCase):
    """The flag survives a DeviceSchema export/import round trip."""

    def test_it_round_trips_through_the_bootstrap_document(self):
        import yaml

        self.post("/api/v1/device-fields", {
            "key": "portal_url", "label": "Portal", "field_type": "text",
            "opens_web_page": True,
        })
        # The export is a ConfigMap wrapping the DeviceSchema document.
        config_map = yaml.safe_load(self.get("/api/v1/device-schema/export").text)
        exported = yaml.safe_load(config_map["data"]["device-schema.yaml"])
        fields = {f["key"]: f for f in exported["spec"]["fields"]}
        self.assertTrue(fields["portal_url"]["opensWebPage"])
        # An ordinary field does not carry the key at all rather than carrying
        # a false — the document stays as small as what it is saying.
        self.assertNotIn("opensWebPage", fields.get("location", {}))
