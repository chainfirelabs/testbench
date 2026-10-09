"""Exports follow the table filters, or its explicit selection."""

from test_device_schema_api import SchemaCase


class ExportScopeTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.devices = {}
        for unique_id, make in (("export-a", "Cisco"), ("export-b", "Dell")):
            response = cls.post("/api/v1/devices", {"unique_id": unique_id, "make": make})
            assert response.status_code == 201, response.text
            cls.devices[unique_id] = response.json()["id"]
        cls.software = {}
        for name in ("Export Alpha", "Export Beta"):
            response = cls.post("/api/v1/software", {"name": name, "version": "1.0"})
            assert response.status_code == 201, response.text
            cls.software[name] = response.json()["id"]
        cls.tests = {}
        for unique_id, name in (("export-a", "Export Alpha"), ("export-b", "Export Beta")):
            response = cls.post("/api/v1/tests", {
                "device_id": cls.devices[unique_id], "software_id": cls.software[name],
                "outcome": "pass",
            })
            assert response.status_code == 201, response.text
            cls.tests[unique_id] = response.json()["id"]
        cls.claims = {}
        for name, make in (("Export Alpha", "Cisco"), ("Export Beta", "Dell")):
            response = cls.post(f"/api/v1/software/{cls.software[name]}/vendor-devices", {
                "make": make, "model": "Export Model",
            })
            assert response.status_code == 201, response.text
            cls.claims[name] = response.json()["id"]

    def exported(self, path, filters=None, ids=None):
        body = {"filters": filters or {}, "ids": ids}
        response = self.post(f"/api/v1{path}/export?format=json", body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_filtered_exports(self):
        cases = (
            ("/devices", {"include__make": '["Cisco"]'}, "unique_id", "export-a"),
            ("/tests", {"include__device_unique_id": '["export-a"]'}, "device_unique_id", "export-a"),
            ("/software", {"include__name": '["Export Alpha"]'}, "name", "Export Alpha"),
            ("/vendor-devices", {"include__make": '["Cisco"]'}, "make", "Cisco"),
            (f"/software/{self.software['Export Alpha']}/vendor-devices",
             {"include__make": '["Cisco"]'}, "make", "Cisco"),
        )
        for path, filters, field, expected in cases:
            with self.subTest(path):
                self.assertEqual([row[field] for row in self.exported(path, filters)], [expected])

    def test_selected_exports_override_filters_and_keep_scope(self):
        cases = (
            ("/devices", self.devices["export-b"], "unique_id", "export-b", {"include__make": '["Cisco"]'}),
            ("/tests", self.tests["export-b"], "device_unique_id", "export-b",
             {"include__device_unique_id": '["export-a"]'}),
            ("/software", self.software["Export Beta"], "name", "Export Beta",
             {"include__name": '["Export Alpha"]'}),
            ("/vendor-devices", self.claims["Export Beta"], "make", "Dell",
             {"include__make": '["Cisco"]'}),
        )
        for path, selected_id, field, expected, filters in cases:
            with self.subTest(path):
                self.assertEqual([row[field] for row in self.exported(path, filters, [selected_id])], [expected])
        scoped = f"/software/{self.software['Export Alpha']}/vendor-devices"
        self.assertEqual(self.exported(scoped, ids=[self.claims["Export Beta"]]), [])
        self.assertEqual([row["make"] for row in self.exported(scoped, ids=[self.claims["Export Alpha"]])],
                         ["Cisco"])

    def test_empty_selection_exports_no_rows(self):
        self.assertEqual(self.exported("/devices", ids=[]), [])

    def test_audit_export_uses_list_filters_and_selection(self):
        audit = self.get('/api/v1/audit_logs?page_size=100').json()['items']
        action = audit[0]['action']
        filters = {"include__action": f'["{action}"]'}
        expected = {row['id'] for row in audit if row['action'] == action}
        self.assertEqual({row['id'] for row in self.exported('/audit_logs', filters)}, expected)
        selected = audit[-1]['id']
        self.assertEqual([row['id'] for row in self.exported(
            '/audit_logs', {"include__action": '[]'}, [selected])], [selected])

    def test_get_exports_still_honor_filters(self):
        for path, query, field, expected in (
            ("/tests", "include__device_unique_id=%5B%22export-a%22%5D", "device_unique_id", "export-a"),
            ("/software", "include__name=%5B%22Export+Alpha%22%5D", "name", "Export Alpha"),
        ):
            with self.subTest(path):
                response = self.get(f"/api/v1{path}/export?format=json&{query}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([row[field] for row in response.json()], [expected])
