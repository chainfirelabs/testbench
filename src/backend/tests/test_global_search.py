"""Indexed JSON prefilter preserves search results for escaped values."""

from test_device_schema_api import SchemaCase


class GlobalSearchTests(SchemaCase):
    def test_device_search_matches_plain_and_quoted_text(self):
        created = self.post("/api/v1/devices", {
            "unique_id": "search-quoted", "make": 'Acme "Desk"',
        })
        self.assertEqual(created.status_code, 201, created.text)
        plain = self.get("/api/v1/search?q=Acme").json()
        quoted = self.get('/api/v1/search?q=%22Desk%22').json()
        self.assertIn("search-quoted", {row["unique_id"] for row in plain["devices"]})
        self.assertIn("search-quoted", {row["unique_id"] for row in quoted["devices"]})
