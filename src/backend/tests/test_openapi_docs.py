"""The web app's Docs page reads the API's OpenAPI document under /api."""

from test_device_schema_api import SchemaCase


class OpenApiDocsTests(SchemaCase):
    def test_admins_get_the_document(self):
        response = self.get("/api/v1/openapi.json")
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()
        self.assertIn("/api/v1/tests/export", document["paths"])
        self.assertIn("components", document)
        self.assertNotIn("/api/v1/openapi.json", document["paths"])

    def test_anonymous_requests_are_refused(self):
        self.assertEqual(self.client.get("/api/v1/openapi.json").status_code, 401)

    def test_everyone_else_is_refused(self):
        self.post("/api/v1/users", {"username": "reader", "password": "password123", "role": "tester"})
        token = self.client.post("/api/v1/auth/login", json={
            "username": "reader", "password": "password123",
        }).json()["access_token"]
        response = self.client.get("/api/v1/openapi.json", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 403, response.text)
