"""A device's own changelog, projected from the audit log."""

from test_device_schema_api import SchemaCase


class DeviceChangelogTests(SchemaCase):
    @classmethod
    def a_device(cls, unique_id):
        return cls.post("/api/v1/devices", {
            "unique_id": unique_id, "make": "Dell", "model": "R640",
        }).json()

    def test_it_reports_what_changed_with_the_schemas_own_labels(self):
        device = self.a_device("cl-labels")
        self.patch_(f"/api/v1/devices/{device['id']}", {"model": "R650"})
        page = self.get(f"/api/v1/devices/{device['id']}/changelog").json()
        self.assertEqual(page["total"], 2)
        # Newest first.
        update, create = page["items"]
        self.assertEqual(create["action"], "device.create")
        self.assertEqual(update["action"], "device.update")
        self.assertEqual(update["summary"], "Device updated: Model")
        self.assertEqual(update["changes"], [
            {"field": "model", "label": "Model", "old": "R640", "new": "R650"},
        ])

    def test_the_related_counts_include_it(self):
        device = self.a_device("cl-counts")
        counts = self.get(f"/api/v1/devices/{device['id']}/related-counts").json()
        self.assertEqual(counts["changelog"], 1)

    def test_it_is_scoped_to_one_device(self):
        first = self.a_device("cl-one")
        second = self.a_device("cl-two")
        self.patch_(f"/api/v1/devices/{second['id']}", {"model": "R750"})
        page = self.get(f"/api/v1/devices/{first['id']}/changelog").json()
        self.assertEqual({item["action"] for item in page["items"]}, {"device.create"})

    def test_a_non_admin_sees_the_projection_but_not_the_raw_entry(self):
        device = self.a_device("cl-readonly")
        self.post("/api/v1/users", {
            "username": "cl-reader", "password": "password123", "role": "readonly",
        })
        token = self.client.post("/api/v1/auth/login", json={
            "username": "cl-reader", "password": "password123",
        }).json()["access_token"]
        header = {"Authorization": f"Bearer {token}"}

        # The audit log itself stays closed to them...
        self.assertEqual(self.client.get("/api/v1/audit_logs", headers=header).status_code, 403)
        # ...while the device's own history, which is the point of the tab, is not.
        response = self.client.get(
            f"/api/v1/devices/{device['id']}/changelog", headers=header,
        )
        self.assertEqual(response.status_code, 200, response.text)
        entry = response.json()["items"][0]
        self.assertEqual(entry["action"], "device.create")
        self.assertIsNone(entry["detail"])
        self.assertIsNone(entry["ip_address"])

    def test_a_sensitive_value_is_masked_even_when_the_writer_did_not(self):
        from app.services.changelog import entry_changes

        # The plugin result callbacks diff the raw document, so the masking has
        # to happen on the way out as well as on the way in.
        changes = entry_changes(
            {"diff": {"password": {"old": "hunter2", "new": "hunter3"},
                      "model": {"old": "R640", "new": "R650"}}},
            {"password": "Password", "model": "Model"},
            {"password"},
        )
        self.assertEqual(changes, [
            {"field": "model", "label": "Model", "old": "R640", "new": "R650"},
            {"field": "password", "label": "Password", "old": "***", "new": "***"},
        ])
