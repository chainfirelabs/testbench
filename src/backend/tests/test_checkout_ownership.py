"""A checkout belongs to whoever holds it.

Checking out a device somebody else held used to return 200 and do half the
job: their name stayed on it while the purpose and return date became the
newcomer's. Now that is refused with a 409 naming the holder, and so is
checking it in, which would otherwise be the way round the refusal. An admin
can do either: check it in, or take the checkout over — which makes it
entirely theirs. Both tell the previous holder.
"""

from datetime import date, timedelta

from test_device_schema_api import SchemaCase


def due(days=3):
    return (date.today() + timedelta(days=days)).isoformat()


class CheckoutOwnershipTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name in ("alice", "bob"):
            cls.post("/api/v1/users", {"username": name, "password": "password123", "role": "tester"})
        cls.alice = cls.sign_in("alice")
        cls.bob = cls.sign_in("bob")

    @classmethod
    def sign_in(cls, username, password="password123"):
        token = cls.client.post("/api/v1/auth/login", json={
            "username": username, "password": password,
        }).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def as_user(self, headers, path, body, **params):
        return self.client.patch(path, headers=headers, json=body, params=params)

    def checked_out_by_alice(self, unique_id):
        """A device alice holds, with values that must survive whatever follows."""
        created = self.post("/api/v1/devices", {
            "unique_id": unique_id, "make": "Acme", "model": "R100",
            "location": "Lab A", "serial_number": "SN-1",
        })
        self.assertEqual(created.status_code, 201, created.text)
        taken = self.as_user(self.alice, f"/api/v1/devices/{unique_id}", {
            "data": {"status": "checked_out", "checkout_purpose": "alice's soak", "checkout_due": due()},
        })
        self.assertEqual(taken.status_code, 200, taken.text)
        self.assertEqual(taken.json()["checked_out_by_username"], "alice")
        return f"/api/v1/devices/{unique_id}"

    def assert_still_alices(self, path):
        device = self.get(path).json()
        self.assertEqual(device["checked_out_by_username"], "alice")
        self.assertEqual(device["data"]["checkout_purpose"], "alice's soak")
        self.assertEqual(device["data"]["make"], "Acme")

    def test_checking_out_a_device_someone_else_holds_is_refused(self):
        path = self.checked_out_by_alice("own-1")
        refused = self.as_user(self.bob, path, {
            "data": {"status": "checked_out", "checkout_purpose": "bob's run", "checkout_due": due(2)},
        })
        self.assertEqual(refused.status_code, 409, refused.text)
        detail = refused.json()["detail"]
        self.assertEqual(detail["code"], "checked_out_by_other")
        self.assertEqual(detail["holder"], "alice")
        self.assertEqual(detail["checkout_purpose"], "alice's soak")
        self.assertFalse(detail["can_take_over"])
        self.assertIn("checked out by alice", detail["message"])
        self.assert_still_alices(path)

    def test_changing_only_the_purpose_or_date_is_refused_too(self):
        path = self.checked_out_by_alice("own-2")
        for change in ({"checkout_purpose": "bob's run"}, {"checkout_due": due(1)}):
            refused = self.as_user(self.bob, path, {"data": change})
            self.assertEqual(refused.status_code, 409, refused.text)
        self.assert_still_alices(path)

    def test_other_fields_of_someone_elses_checkout_stay_editable(self):
        """An edit dialog re-sends the checkout as it stands; that is no claim."""
        path = self.checked_out_by_alice("own-3")
        edited = self.as_user(self.bob, path, {"data": {
            "status": "checked_out", "checkout_purpose": "alice's soak", "checkout_due": due(),
            "location": "Lab B",
        }})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["data"]["location"], "Lab B")
        self.assertEqual(edited.json()["checked_out_by_username"], "alice")

    def test_the_holder_can_still_extend_their_own_checkout(self):
        path = self.checked_out_by_alice("own-4")
        extended = self.as_user(self.alice, path, {"data": {"checkout_due": due(5), "checkout_purpose": "longer"}})
        self.assertEqual(extended.status_code, 200, extended.text)
        self.assertEqual(extended.json()["data"]["checkout_purpose"], "longer")

    def test_checking_in_someone_elses_device_is_refused(self):
        """Or the refusal above would be one click from meaningless: in, then out."""
        path = self.checked_out_by_alice("own-5")
        for status in ("available", "broken"):
            refused = self.as_user(self.bob, path, {"data": {"status": status}})
            self.assertEqual(refused.status_code, 409, refused.text)
            detail = refused.json()["detail"]
            self.assertEqual(detail["code"], "checked_out_by_other")
            self.assertFalse(detail["can_take_over"])
            self.assertIn("only they or an admin", detail["message"].lower())
        self.assert_still_alices(path)

    def test_the_holder_can_check_their_own_device_in(self):
        path = self.checked_out_by_alice("own-10")
        returned = self.as_user(self.alice, path, {"data": {"status": "available"}})
        self.assertEqual(returned.status_code, 200, returned.text)
        self.assertIsNone(returned.json()["checked_out_by_username"])

    def test_an_admin_can_check_someone_elses_device_in_and_the_holder_is_told(self):
        path = self.checked_out_by_alice("own-11")
        returned = self.patch_(path, {"data": {"status": "available"}})
        self.assertEqual(returned.status_code, 200, returned.text)
        device = returned.json()
        self.assertIsNone(device["checked_out_by_username"])
        self.assertEqual(device["data"]["make"], "Acme")

        notices = self.client.get("/api/v1/notifications", headers=self.alice).json()["items"]
        released = [n for n in notices if n["kind"] == "checkout_released"]
        self.assertEqual(len(released), 1)
        self.assertIn("admin checked in own-11", released[0]["title"])

        entries = self.get(f"/api/v1/audit_logs?entity_id={device['id']}").json()
        entries = entries["items"] if isinstance(entries, dict) else entries
        self.assertTrue(any((e.get("detail") or {}).get("released_from") == "alice" for e in entries))

    def test_an_import_cannot_check_in_someone_elses_device_either(self):
        path = self.checked_out_by_alice("own-12")
        csv = "unique_id,status\nown-12,available\n"
        imported = self.client.post(
            "/api/v1/devices/import", headers=self.bob,
            files={"file": ("devices.csv", csv, "text/csv")},
        )
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["updated"], 0)
        self.assertIn("checked out by alice", result["errors"][0]["error"])
        self.assert_still_alices(path)

    def test_only_an_admin_can_take_over(self):
        path = self.checked_out_by_alice("own-6")
        refused = self.as_user(self.bob, path, {
            "data": {"status": "checked_out", "checkout_purpose": "bob's run", "checkout_due": due()},
        }, take_over="true")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assert_still_alices(path)

    def test_an_admin_is_refused_too_until_they_ask_to_take_over(self):
        path = self.checked_out_by_alice("own-7")
        refused = self.patch_(path, {
            "data": {"status": "checked_out", "checkout_purpose": "admin's fix", "checkout_due": due()},
        })
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertTrue(refused.json()["detail"]["can_take_over"])
        self.assert_still_alices(path)

    def test_a_take_over_makes_the_checkout_the_admins_and_tells_the_holder(self):
        path = self.checked_out_by_alice("own-8")
        taken = self.client.patch(path, headers=self.headers, params={"take_over": "true"}, json={
            "data": {"status": "checked_out", "checkout_purpose": "admin's fix", "checkout_due": due(1)},
        })
        self.assertEqual(taken.status_code, 200, taken.text)
        device = taken.json()
        self.assertEqual(device["checked_out_by_username"], "admin")
        self.assertEqual(device["data"]["checkout_purpose"], "admin's fix")
        self.assertEqual(device["data"]["make"], "Acme")

        notices = self.client.get("/api/v1/notifications", headers=self.alice).json()["items"]
        taken_over = [n for n in notices if n["kind"] == "checkout_taken_over"]
        self.assertEqual(len(taken_over), 1)
        self.assertIn("admin took over own-8", taken_over[0]["title"])

        entries = self.get(f"/api/v1/audit_logs?entity_id={device['id']}").json()
        entries = entries["items"] if isinstance(entries, dict) else entries
        self.assertTrue(any((e.get("detail") or {}).get("taken_over_from") == "alice" for e in entries))

    def test_a_take_over_still_needs_a_valid_return_date(self):
        path = self.checked_out_by_alice("own-9")
        refused = self.client.patch(path, headers=self.headers, params={"take_over": "true"}, json={
            "data": {"status": "checked_out", "checkout_purpose": "admin's fix", "checkout_due": due(30)},
        })
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assert_still_alices(path)
