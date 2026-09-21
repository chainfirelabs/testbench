"""Roles, permissions and the guards they replaced, against a real PostgreSQL."""

from test_device_schema_api import SchemaCase


class RoleApiTests(SchemaCase):
    """The role table itself: what ships, what can be changed, what cannot."""

    def test_the_three_builtin_roles_reproduce_the_old_ladder(self):
        roles = {role["slug"]: role for role in self.get("/api/v1/roles").json()}
        # A subset check, not equality: the class shares one database and
        # another test may already have defined a role of its own.
        self.assertLessEqual({"readonly", "tester", "admin"}, set(roles))
        for slug in ("readonly", "tester", "admin"):
            self.assertTrue(roles[slug]["is_builtin"])
        self.assertEqual(roles["readonly"]["permissions"], [])
        self.assertEqual(
            set(roles["tester"]["permissions"]),
            {"devices.edit", "software.edit", "tests.edit", "views.save"},
        )
        # Admin held every guard before, so it holds every permission now.
        catalog = {item["key"] for item in self.get("/api/v1/roles/permissions").json()}
        self.assertEqual(set(roles["admin"]["permissions"]), catalog)

    def test_a_builtin_role_cannot_be_deleted(self):
        response = self.client.delete("/api/v1/roles/tester", headers=self.headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("built-in", response.json()["detail"])

    def test_the_last_role_that_manages_users_keeps_that_permission(self):
        response = self.patch_("/api/v1/roles/admin", {"permissions": ["devices.edit"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("only role", response.json()["detail"])
        # And the refusal left the role alone.
        admin = self.get("/api/v1/roles/admin").json()
        self.assertIn("users.manage", admin["permissions"])

    def test_a_role_still_held_by_someone_cannot_be_deleted(self):
        created = self.post("/api/v1/roles", {
            "slug": "field-tech", "name": "Field Tech", "permissions": ["devices.edit"],
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.post("/api/v1/users", {
            "username": "holder", "password": "password123", "role": "field-tech",
        })
        blocked = self.client.delete(
            f"/api/v1/roles/{created.json()['id']}", headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("Move them", blocked.json()["detail"])

    def test_an_unknown_permission_is_refused_rather_than_stored(self):
        response = self.post("/api/v1/roles", {
            "slug": "odd", "name": "Odd", "permissions": ["devices.edit", "devices.launch"],
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_a_user_cannot_be_given_a_role_that_does_not_exist(self):
        response = self.post("/api/v1/users", {
            "username": "nobody", "password": "password123", "role": "wizard",
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("No role", response.json()["detail"])


class PermissionEnforcementTests(SchemaCase):
    """What a role grants is what the API actually enforces."""

    @classmethod
    def sign_in(cls, username, password="password123"):
        token = cls.client.post("/api/v1/auth/login", json={
            "username": username, "password": password,
        }).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    def account(cls, username, role, permissions=None):
        """An account holding a role defined for this test."""
        if permissions is not None:
            cls.post("/api/v1/roles", {
                "slug": role, "name": role.title(), "permissions": permissions,
            })
        cls.post("/api/v1/users", {
            "username": username, "password": "password123", "role": role,
        })
        return cls.sign_in(username)

    def test_a_custom_role_gets_exactly_what_it_was_given(self):
        auditor = self.account("ada", "auditor", ["audit.view"])
        self.assertEqual(
            self.client.get("/api/v1/auth/me", headers=auditor).json()["permissions"],
            ["audit.view"],
        )
        # The one thing it was given.
        self.assertEqual(self.client.get("/api/v1/audit_logs", headers=auditor).status_code, 200)
        # And nothing else — including the write access the old ladder would
        # have had to grant to get anywhere near the audit log.
        refused = self.client.post(
            "/api/v1/devices", headers=auditor, json={"unique_id": "audit-1"},
        )
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("edit devices", refused.json()["detail"])
        self.assertEqual(self.client.get("/api/v1/users", headers=auditor).status_code, 403)

    def test_permissions_are_separable(self):
        """Recording a test no longer implies editing the inventory."""
        runner = self.account("rita", "test-runner", ["tests.edit"])
        self.post("/api/v1/devices", {"unique_id": "runner-device"})
        self.assertEqual(
            self.client.post(
                "/api/v1/devices", headers=runner, json={"unique_id": "runner-2"},
            ).status_code,
            403,
        )
        software = self.post("/api/v1/software", {"name": "Runner", "version": "1"}).json()
        device = self.get("/api/v1/devices?search=runner-device").json()["items"][0]
        recorded = self.client.post("/api/v1/tests", headers=runner, json={
            "software_id": software["id"], "device_id": device["id"], "outcome": "pass",
        })
        self.assertEqual(recorded.status_code, 201, recorded.text)

    def test_editing_a_role_applies_without_a_new_login(self):
        helper = self.account("hal", "helper", [])
        self.assertEqual(
            self.client.post(
                "/api/v1/devices", headers=helper, json={"unique_id": "hal-1"},
            ).status_code,
            403,
        )
        self.patch_("/api/v1/roles/helper", {"permissions": ["devices.edit"]})
        # Same token: the permission set is resolved per request, not baked in.
        self.assertEqual(
            self.client.post(
                "/api/v1/devices", headers=helper, json={"unique_id": "hal-1"},
            ).status_code,
            201,
        )

    def test_an_api_key_cannot_carry_more_than_its_owner(self):
        tester = self.account("terry", "tester")
        offered = self.client.get("/api/v1/auth/api-key-roles", headers=tester).json()
        self.assertNotIn("admin", offered)
        self.assertIn("tester", offered)
        refused = self.client.post("/api/v1/auth/api-keys", headers=tester, json={
            "label": "too much", "role": "admin",
        })
        self.assertEqual(refused.status_code, 403, refused.text)

    def test_an_api_key_is_capped_against_its_owners_current_role(self):
        admin_key = self.post("/api/v1/auth/api-keys", {
            "label": "full", "role": "admin",
        }).json()["plaintext"]
        header = {"Authorization": f"Bearer {admin_key}"}
        self.assertEqual(
            self.client.post(
                "/api/v1/devices", headers=header, json={"unique_id": "key-1"},
            ).status_code,
            201,
        )
        # Weaken what the *role* grants and the key already issued weakens with
        # it, without anybody touching the key.
        self.patch_("/api/v1/roles/admin", {"permissions": [
            "users.manage", "audit.view", "schema.manage", "plugins.manage",
            "settings.manage", "software.edit", "tests.edit", "views.save",
        ]})
        try:
            self.assertEqual(
                self.client.post(
                    "/api/v1/devices", headers=header, json={"unique_id": "key-2"},
                ).status_code,
                403,
            )
        finally:
            self.patch_("/api/v1/roles/admin", {"permissions": [
                item["key"] for item in self.get("/api/v1/roles/permissions").json()
            ]})
