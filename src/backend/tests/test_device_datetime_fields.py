"""Date-typed device fields the catalog does not define.

`DeviceCreate` and `DeviceUpdate` declare `last_seen_online`,
`last_scanned_at` and `checkout_due` as date types, so Pydantic parses them
into Python date objects. The device document is JSONB, and a field the catalog
does not account for is kept verbatim — so before this was fixed, sending one
of those for an unconfigured field put a `datetime` into JSONB and the write
failed at the flush, as a 500 with no field named.

Only the single-device create and update reached it, because those are the
paths with a model to parse the value; bulk and import take rows as plain
dicts. The application's own seed script creates devices one at a time, so this
was reachable from the first thing a new installation runs.
"""

from test_device_schema_api import SchemaCase


class UnconfiguredDateFieldTests(SchemaCase):
    def test_a_datetime_for_an_unconfigured_field_is_stored(self):
        created = self.post("/api/v1/devices", {
            "unique_id": "dt-1", "make": "HPE",
            "last_seen_online": "2026-09-19T10:00:00+00:00",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["make"], "HPE")

    def test_it_reads_back_as_the_timestamp_that_went_in(self):
        self.post("/api/v1/devices", {
            "unique_id": "dt-2", "last_scanned_at": "2026-09-19T10:00:00+00:00",
        })
        device = self.get("/api/v1/devices/dt-2").json()
        # Stored as text in the document, so it survives the round trip rather
        # than arriving as something JSON cannot describe.
        self.assertIn("2026-09-19", str(device["last_scanned_at"]))

    def test_it_survives_an_update_as_well_as_a_create(self):
        self.post("/api/v1/devices", {"unique_id": "dt-3"})
        updated = self.patch_("/api/v1/devices/dt-3", {
            "last_seen_online": "2026-09-19T10:00:00+00:00",
        })
        self.assertEqual(updated.status_code, 200, updated.text)

    def test_a_date_field_on_a_real_checkout_is_accepted(self):
        """`checkout_due` is the third of these, and is only legal on a
        checked-out device — the refusal below is that rule, not this bug."""
        created = self.post("/api/v1/devices", {
            "unique_id": "dt-4", "status": "checked_out",
            "checkout_purpose": "lab", "checkout_due": "2026-09-25",
        })
        self.assertEqual(created.status_code, 201, created.text)

    def test_a_return_date_without_a_checkout_is_still_refused(self):
        refused = self.post("/api/v1/devices", {
            "unique_id": "dt-5", "checkout_due": "2026-09-25",
        })
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("checked-out", refused.text)

    def test_a_bulk_write_carrying_one_also_lands(self):
        """Bulk never hit this — `BulkPayload.upserts` is `list[dict]`, so no
        model parses a row and the value arrives as the string it was sent as.
        Pinned anyway: the two paths should not diverge on what they accept,
        and this one only worked by not going through the model."""
        response = self.post("/api/v1/devices/bulk", {
            "upserts": [
                {"unique_id": "dt-6", "last_seen_online": "2026-09-19T10:00:00+00:00"},
                {"unique_id": "dt-7", "last_seen_online": "2026-09-18T09:00:00+00:00"},
            ],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["errors"], [])
        self.assertEqual(response.json()["created"], 2)
