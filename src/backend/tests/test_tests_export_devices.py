"""A software version's tests exported with their devices' details."""

import csv
import io
import json

from test_device_schema_api import SchemaCase


class TestExportWithDevicesTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_visible("password")
        cls.devices = {}
        for unique_id, make, serial in (("dev-a", "Cisco", "SN-A"), ("dev-b", "Dell", "SN-B")):
            # Firmware is no field of this installation, so it is kept as misc data.
            response = cls.post("/api/v1/devices", {
                "unique_id": unique_id, "make": make, "serial_number": serial,
                "data": {"password": "hunter2", "firmware_version": f"fw-{unique_id}"},
            })
            assert response.status_code == 201, response.text
            cls.devices[unique_id] = response.json()["id"]
        response = cls.post("/api/v1/software", {"name": "Exported", "version": "1.0"})
        assert response.status_code == 201, response.text
        cls.versions = {"1.0": response.json()["id"]}
        for version in ("2.0", "3.0"):
            response = cls.post(f"/api/v1/software/{cls.versions['1.0']}/versions", {"version": version})
            assert response.status_code == 201, response.text
            cls.versions[version] = response.json()["id"]
        for unique_id, version in (("dev-a", "1.0"), ("dev-b", "1.0"), ("dev-a", "2.0"), ("dev-b", "3.0")):
            response = cls.post("/api/v1/tests", {
                "device_id": cls.devices[unique_id], "software_id": cls.versions[version], "outcome": "pass",
            })
            assert response.status_code == 201, response.text

    def export(self, format, filters, include_device=True):
        query = "&include_device=true" if include_device else ""
        response = self.post(f"/api/v1/tests/export?format={format}{query}", {"filters": filters, "ids": None})
        self.assertEqual(response.status_code, 200, response.text)
        return response.text

    def test_json_rows_carry_a_device_object_for_the_version_only(self):
        rows = json.loads(self.export("json", {"software_id": self.versions["1.0"]}))
        self.assertEqual(sorted(row["device_unique_id"] for row in rows), ["dev-a", "dev-b"])
        self.assertTrue(all(row["id"] for row in rows))
        devices = {row["device"]["unique_id"]: row["device"] for row in rows}
        self.assertEqual(devices["dev-a"]["make"], "Cisco")
        self.assertEqual(devices["dev-b"]["serial_number"], "SN-B")
        self.assertNotIn("password", devices["dev-a"])
        self.assertEqual(devices["dev-a"]["misc_data"], {"firmware_version": "fw-dev-a"})
        self.assertNotIn("hunter2", json.dumps(rows))

    def test_csv_rows_carry_device_columns_without_secrets(self):
        text = self.export("csv", {"software_id": self.versions["1.0"]})
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 2)
        self.assertIn("device_type", rows[0])
        self.assertNotIn("device_password", rows[0])
        self.assertNotIn("hunter2", text)
        by_device = {row["device_unique_id"]: row for row in rows}
        self.assertEqual(by_device["dev-a"]["device_make"], "Cisco")
        self.assertEqual(by_device["dev-a"]["device_serial_number"], "SN-A")
        # Lifted into a column of its own, like the device export does.
        self.assertEqual(by_device["dev-b"]["device_firmware_version"], "fw-dev-b")
        self.assertNotIn("device_misc_data", rows[0])

    def test_selected_devices_narrow_the_export(self):
        rows = json.loads(self.export("json", {
            "software_id": self.versions["1.0"], "include__device_unique_id": '["dev-b"]'}))
        self.assertEqual([row["device_unique_id"] for row in rows], ["dev-b"])

    def test_export_without_devices_is_unchanged(self):
        rows = json.loads(self.export("json", {"software_id": self.versions["2.0"]}, include_device=False))
        self.assertEqual([row["device_unique_id"] for row in rows], ["dev-a"])
        self.assertNotIn("device", rows[0])
        self.assertNotIn("device_serial_number", rows[0])
        self.assertNotIn("id", rows[0])

    def test_an_export_with_devices_re_imports_as_the_same_tests(self):
        text = self.export("csv", {"software_id": self.versions["3.0"]})
        response = self.client.post("/api/v1/tests/import", headers=self.headers,
                                    files={"file": ("tests.csv", text.encode(), "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["errors"], [])
        tests = self.get(f"/api/v1/tests?software_id={self.versions['3.0']}").json()["items"]
        self.assertEqual(len(tests), 1)
        self.assertEqual(response.json()["updated"], 1)
        for test in tests:
            self.assertEqual(test["misc_data"], {})
