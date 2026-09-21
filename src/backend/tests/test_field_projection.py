"""`?fields=` on the list endpoints: a row narrowed to what a caller needs.

The New Test dialog reads four values per device and was downloading the whole
document to get them. What matters here is that narrowing stays *dynamic* — a
field this installation invented is projectable — and that it cannot be used
to read something a full row would not have handed over anyway.
"""

from test_device_schema_api import SchemaCase


class DeviceFieldProjectionTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_visible("lan_ip")
        cls.post("/api/v1/devices", {
            "unique_id": "proj-1", "make": "Cisco", "model": "ISR 4331",
            "status": "available", "lan_ip": "10.0.0.5",
        })

    @classmethod
    def projected(cls, fields):
        return cls.get(f"/api/v1/devices?fields={fields}").json()

    def test_a_row_carries_only_what_was_asked_for(self):
        page = self.projected("unique_id,make")
        self.assertEqual(page["items"][0], {"unique_id": "proj-1", "make": "Cisco"})

    def test_the_page_envelope_is_unchanged(self):
        """Callers page through a projection the same way they page a full row."""
        page = self.projected("unique_id")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["page"], 1)
        self.assertIn("page_size", page)

    def test_without_the_parameter_the_whole_row_still_comes_back(self):
        item = self.get("/api/v1/devices").json()["items"][0]
        self.assertEqual(item["unique_id"], "proj-1")
        self.assertIn("misc_data", item)
        self.assertIn("created_at", item)

    def test_structural_and_document_fields_mix_freely(self):
        """`id` is a column, `make` is a key in the JSON document; a caller
        should not have to know which is which."""
        item = self.projected("id,unique_id,make,lan_ip")["items"][0]
        self.assertEqual(item["unique_id"], "proj-1")
        self.assertEqual(item["lan_ip"], "10.0.0.5")
        self.assertTrue(item["id"])

    def test_an_installation_defined_field_is_projectable(self):
        """The whole point: no list of known keys to fall out of date."""
        created = self.post("/api/v1/device-fields", {
            "key": "rack_slot", "label": "Rack Slot", "field_type": "text",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.patch_("/api/v1/devices/proj-1", {"rack_slot": "B12"})
        item = self.projected("unique_id,rack_slot")["items"][0]
        self.assertEqual(item, {"unique_id": "proj-1", "rack_slot": "B12"})

    def test_a_field_this_device_does_not_have_comes_back_null(self):
        """Not an error: a fleet is heterogeneous and a picker asking one set
        of fields of every device should not fail on the ones that lack them."""
        item = self.projected("unique_id,nonexistent_field")["items"][0]
        self.assertEqual(item, {"unique_id": "proj-1", "nonexistent_field": None})

    def test_an_empty_fields_parameter_is_refused(self):
        self.assertEqual(self.get("/api/v1/devices?fields=").status_code, 422)
        self.assertEqual(self.get("/api/v1/devices?fields=,,").status_code, 422)

    def test_filters_still_apply_to_a_projection(self):
        self.assertEqual(self.projected("unique_id&make=Cisco")["total"], 1)
        self.assertEqual(self.projected("unique_id&make=Juniper")["total"], 0)


class DeviceProjectionSecretsTests(SchemaCase):
    """Its own database: it marks a field sensitive, which the tests above
    assert the ordinary shape of."""

    def test_a_sensitive_field_cannot_be_projected(self):
        """A projection reads the document by key, which is the one path that
        would hand back a value an ordinary list response never carries."""
        created = self.post("/api/v1/device-fields", {
            "key": "api_secret", "label": "API Secret", "field_type": "text",
            "sensitive": True,
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.post("/api/v1/devices", {"unique_id": "sec-1", "api_secret": "hunter2"})

        refused = self.get("/api/v1/devices?fields=unique_id,api_secret")
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("api_secret", refused.text)
        # And the secret is not in the body of the refusal either.
        self.assertNotIn("hunter2", refused.text)


class SoftwareFieldProjectionTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("/api/v1/software", {"name": "Backup-Restore", "version": "1.0"})

    @classmethod
    def projected(cls, fields):
        return cls.get(f"/api/v1/software?fields={fields}").json()

    def test_a_row_carries_only_what_was_asked_for(self):
        item = self.projected("id,name,version")["items"][0]
        self.assertEqual(sorted(item), ["id", "name", "version"])
        self.assertEqual(item["name"], "Backup-Restore")

    def test_a_derived_field_still_resolves(self):
        """`version_count` and `is_latest` are worked out from a row's siblings
        rather than read off it, so asking for one has to run that pass."""
        item = self.projected("name,version_count,is_latest")["items"][0]
        self.assertEqual(item["version_count"], 1)
        self.assertTrue(item["is_latest"])

    def test_without_the_parameter_the_whole_row_still_comes_back(self):
        item = self.get("/api/v1/software").json()["items"][0]
        self.assertIn("bundle_components", item)
        self.assertIn("vendor_device_count", item)
