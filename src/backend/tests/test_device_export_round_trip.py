"""A device export must restore the device it came from.

There used to be two CSV exports on the devices page, because the field-column
one silently dropped `link_overrides` — a per-device setting with no field, and
so no column — and restoring a fleet from it put every custom link back on
http without a word. The second export existed to work around that, carried the
whole document as a JSON blob per row, and was unusable in a spreadsheet, so
choosing between them was a trap for anyone who did not already know.

The field-column export carries the overrides now. These tests are what keeps
the second one from being needed again: they take a device through export and
import and compare what comes back with what went in, value by value.
"""

import io as _io

from test_device_schema_api import SchemaCase


class DeviceExportRoundTripTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.make_visible("lan_ip")
        for spec in (
            {"key": "port_count", "label": "Port Count", "field_type": "number"},
            {"key": "specs", "label": "Specs", "field_type": "json"},
            {"key": "managed", "label": "Managed", "field_type": "boolean"},
            {"key": "rack_slot", "label": "Rack Slot", "field_type": "text"},
        ):
            assert cls.post("/api/v1/device-fields", spec).status_code == 201

    def a_device(self, unique_id, **extra):
        created = self.post("/api/v1/devices", {
            "unique_id": unique_id, "make": "Cisco", "lan_ip": "10.0.0.5",
            "port_count": 48, "managed": True, "rack_slot": "B12",
            "specs": {"psu": 2, "tags": ["core", "edge"]},
            **extra,
        })
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()

    def snapshot(self, unique_id):
        device = self.get(f"/api/v1/devices/{unique_id}").json()
        return device["data"], device["link_overrides"]

    def round_trip(self, unique_id, fmt="csv"):
        """Export the fleet, delete the device, import the file back."""
        exported = self.client.get(
            f"/api/v1/devices/export?format={fmt}&columns=all", headers=self.headers,
        )
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual(self.client.delete(
            f"/api/v1/devices/{unique_id}", headers=self.headers,
        ).status_code, 204)
        result = self.client.post(
            "/api/v1/devices/import", headers=self.headers,
            files={"file": (f"devices.{fmt}", _io.BytesIO(exported.content),
                            "text/csv" if fmt == "csv" else "application/json")},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["errors"], [])

    def test_a_link_override_survives_a_csv_round_trip(self):
        """The regression that made a second export necessary."""
        self.a_device("rt-links")
        self.patch_("/api/v1/devices/rt-links", {
            "link_overrides": {"lan_ip": {"scheme": "https", "port": 8443}},
        })
        before = self.snapshot("rt-links")
        self.round_trip("rt-links")
        self.assertEqual(self.snapshot("rt-links"), before)

    def test_the_whole_document_survives_with_its_types(self):
        """A number stays a number and a boolean stays a boolean: CSV makes
        every cell text, and the import is what puts the types back."""
        self.a_device("rt-types")
        before_data, _ = self.snapshot("rt-types")
        self.assertEqual(before_data["port_count"], 48)
        self.assertIs(before_data["managed"], True)

        self.round_trip("rt-types")
        after_data, _ = self.snapshot("rt-types")
        self.assertEqual(after_data, before_data)
        self.assertEqual(after_data["port_count"], 48)
        self.assertIs(after_data["managed"], True)
        self.assertEqual(after_data["specs"], {"psu": 2, "tags": ["core", "edge"]})

    def test_the_export_carries_a_link_overrides_column(self):
        self.a_device("rt-column")
        exported = self.get("/api/v1/devices/export?format=csv&columns=all")
        self.assertIn("link_overrides", exported.text.splitlines()[0])

    def test_a_device_overriding_nothing_leaves_the_cell_empty(self):
        """An empty JSON object in every row is noise in a spreadsheet, and a
        blank cell re-imports as "says nothing" rather than "clear them"."""
        import csv

        self.a_device("rt-plain")
        exported = self.get("/api/v1/devices/export?format=csv&columns=all")
        rows = {row["unique_id"]: row for row in csv.DictReader(_io.StringIO(exported.text))}
        self.assertEqual(rows["rt-plain"]["link_overrides"], "")

    def test_a_json_round_trip_keeps_them_too(self):
        self.a_device("rt-json")
        self.patch_("/api/v1/devices/rt-json", {
            "link_overrides": {"lan_ip": {"port": 9000}},
        })
        before = self.snapshot("rt-json")
        self.round_trip("rt-json", fmt="json")
        self.assertEqual(self.snapshot("rt-json"), before)


class LinkOverridesKeyIsReservedTests(SchemaCase):
    """`link_overrides` travels beside the document, so it is not a field name.

    It is part of the request envelope — stripped out of the document before
    anything is stored — so a field given that key would show a column, accept
    typing, and keep nothing. The other envelope keys have always been refused;
    this one was not, because it was added to the envelope without being added
    here.
    """

    def test_a_field_cannot_be_named_link_overrides(self):
        refused = self.post("/api/v1/device-fields", {
            "key": "link_overrides", "label": "Link Overrides", "field_type": "text",
        })
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_the_other_envelope_keys_are_refused_the_same_way(self):
        """Pinned together so the two lists cannot drift apart again."""
        for key in ("unique_id", "misc_data", "device_type", "created_at"):
            with self.subTest(key):
                refused = self.post("/api/v1/device-fields", {
                    "key": key, "label": "X", "field_type": "text",
                })
                self.assertEqual(refused.status_code, 422, refused.text)

    def test_an_ordinary_key_is_still_accepted(self):
        created = self.post("/api/v1/device-fields", {
            "key": "rack_note", "label": "Rack Note", "field_type": "text",
        })
        self.assertEqual(created.status_code, 201, created.text)
