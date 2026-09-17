"""Software bundles against a real PostgreSQL (skipped when unavailable)."""

from test_device_schema_api import SchemaCase


class SoftwareBundleApiTests(SchemaCase):
    def test_software_template_contains_only_flat_software_fields(self):
        response = self.get("/api/v1/software/template")
        self.assertEqual(response.status_code, 200, response.text)
        header = response.text.splitlines()[0].split(",")
        self.assertIn("name", header)
        self.assertIn("version", header)
        self.assertNotIn("bundle_components", header)
        self.assertNotIn("vendor_devices", header)

    def test_components_are_scoped_to_the_suite(self):
        response = self.post("/api/v1/software", {
            "name": "Microsoft 365",
            "version": "2026.1",
            "bundle_components": [
                {"name": "Microsoft Outlook", "version": "16.2"},
                {"name": "Microsoft Word", "version": "16.4"},
            ],
        })
        self.assertEqual(response.status_code, 201, response.text)
        bundle = response.json()
        self.assertEqual(
            [(item["name"], item["version"]) for item in bundle["bundle_components"]],
            [("Microsoft Outlook", "16.2"), ("Microsoft Word", "16.4")],
        )
        outlook = self.get("/api/v1/software/lookup/by-name?name=Microsoft%20Outlook")
        self.assertEqual(outlook.status_code, 404, outlook.text)

    def test_test_import_keeps_components_under_the_suite(self):
        software = self.post("/api/v1/software", {
            "name": "Microsoft 365", "version": "2026.1",
        }).json()
        self.post("/api/v1/devices", {"unique_id": "suite-device"})
        content = (
            "device_unique_id,software_name,software_version,component_name,component_version,outcome\n"
            "suite-device,Microsoft 365,2026.1,Microsoft Outlook,16.2,pass\n"
            "suite-device,Microsoft 365,2026.1,Microsoft Word,16.4,fail\n"
        )
        imported = self.client.post(
            "/api/v1/tests/import", headers=self.headers,
            files={"file": ("tests.csv", content, "text/csv")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["created"], 2)
        self.assertEqual(imported.json()["errors"], [])

        tests = self.get(f"/api/v1/tests?software_id={software['id']}").json()["items"]
        self.assertEqual(
            {(item["software_name"], item["component_name"]) for item in tests},
            {("Microsoft 365", "Microsoft Outlook"), ("Microsoft 365", "Microsoft Word")},
        )
        tested = self.get(f"/api/v1/software/{software['id']}/tested-devices").json()["devices"]
        self.assertEqual(
            {item["component"]["name"] for item in tested},
            {"Microsoft Outlook", "Microsoft Word"},
        )
        self.assertEqual(
            self.get("/api/v1/software/lookup/by-name?name=Microsoft%20Outlook").status_code,
            404,
        )

    def test_test_template_includes_optional_component_columns(self):
        response = self.get("/api/v1/tests/template")
        self.assertEqual(response.status_code, 200, response.text)
        header = response.text.splitlines()[0].split(",")
        self.assertEqual(header[0:5], [
            "device_unique_id", "software_name", "software_version",
            "component_name", "component_version",
        ])


class VendorDeviceSchemaApiTests(SchemaCase):
    def test_custom_field_can_be_required_for_one_software(self):
        software = self.post("/api/v1/software", {
            "name": "Router Manager", "version": "1.0",
        }).json()
        created = self.post("/api/v1/entity-fields/vendor_devices", {
            "key": "license_tier", "label": "License Tier", "field_type": "select",
            "options": ["standard", "enterprise"],
        })
        self.assertEqual(created.status_code, 201, created.text)
        field_id = created.json()["id"]
        schema = self.get(
            f"/api/v1/software/{software['id']}/vendor-devices/schema"
        ).json()
        for field in schema:
            if field["id"] == field_id:
                field["required"] = True
        updated = self.put(
            f"/api/v1/software/{software['id']}/vendor-devices/schema",
            {"fields": [
                {"id": field["id"], "visible": field["visible"], "required": field["required"]}
                for field in schema
            ]},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        missing = self.post(f"/api/v1/software/{software['id']}/vendor-devices", {
            "make": "Acme", "model": "R1",
        })
        self.assertEqual(missing.status_code, 422)
        accepted = self.post(f"/api/v1/software/{software['id']}/vendor-devices", {
            "make": "Acme", "model": "R1", "misc_data": {"license_tier": "enterprise"},
        })
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["misc_data"]["license_tier"], "enterprise")
