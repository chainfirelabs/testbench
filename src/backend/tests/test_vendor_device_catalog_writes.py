"""Creating, importing and templating vendor claims from the cross-software page.

A vendor device belongs to one software version. The software-scoped endpoints
get that from the URL; these get it from the row, which is the only difference
between them — so what is checked here is mostly that the version a row names
is the version it lands on, and that naming one that does not exist is an error
rather than a claim quietly filed against whichever version happens to be
current.
"""

import io
import json

from test_device_schema_api import SchemaCase


class VendorDeviceCatalogWriteTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        backup = cls.post("/api/v1/software", {"name": "Backup-Restore", "version": "1.0"}).json()
        cls.post(f"/api/v1/software/{backup['id']}/versions", {
            "version": "2.0", "copy_vendor_devices": False,
        })
        cls.post("/api/v1/software", {"name": "NetGuard", "version": "3.1"})

    @classmethod
    def claims_for(cls, name):
        return cls.get(f"/api/v1/vendor-devices?software={name}").json()["items"]

    @classmethod
    def upload(cls, rows):
        payload = json.dumps(rows).encode()
        return cls.client.post(
            "/api/v1/vendor-devices/import", headers=cls.headers,
            files={"file": ("claims.json", io.BytesIO(payload), "application/json")},
        )

    # ---------------------------------------------------------------- create

    def test_a_claim_names_the_version_it_belongs_to(self):
        created = self.post("/api/v1/vendor-devices", {
            "software_name": "Backup-Restore", "software_version": "2.0",
            "make": "Cisco", "model": "ISR 4331",
        })
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual(body["software_name"], "Backup-Restore")
        self.assertEqual(body["software_version"], "2.0")
        # And it is on that version's list when read back, rather than on
        # whichever version the name alone would have resolved to.
        landed = [
            item for item in self.claims_for("Backup-Restore") if item["id"] == body["id"]
        ]
        self.assertEqual([item["software_version"] for item in landed], ["2.0"])

    def test_a_claim_can_be_added_to_a_superseded_version(self):
        """The version that was tested is often not the one that shipped today."""
        created = self.post("/api/v1/vendor-devices", {
            "software_name": "Backup-Restore", "software_version": "1.0",
            "make": "Juniper", "model": "SRX300",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["software_version"], "1.0")
        self.assertFalse(created.json()["software_is_latest"])

    def test_the_software_name_is_required(self):
        refused = self.post("/api/v1/vendor-devices", {"make": "Cisco", "model": "ISR 4331"})
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_an_unknown_software_is_refused(self):
        refused = self.post("/api/v1/vendor-devices", {
            "software_name": "Nonesuch", "software_version": "1.0", "make": "Cisco",
        })
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("Nonesuch", refused.text)

    def test_a_version_the_software_does_not_have_is_refused(self):
        """Not filed against the current version instead: nobody made that claim."""
        refused = self.post("/api/v1/vendor-devices", {
            "software_name": "Backup-Restore", "software_version": "9.9", "make": "Cisco",
        })
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("9.9", refused.text)
        # The message says what it does have, so the fix is obvious.
        self.assertIn("1.0", refused.text)

    def test_a_duplicate_claim_on_the_same_version_conflicts(self):
        body = {
            "software_name": "NetGuard", "software_version": "3.1",
            "make": "Dell", "model": "R640",
        }
        self.assertEqual(self.post("/api/v1/vendor-devices", body).status_code, 201)
        again = self.post("/api/v1/vendor-devices", body)
        self.assertEqual(again.status_code, 409, again.text)

    def test_the_same_device_may_be_claimed_by_two_versions(self):
        """Two versions making the same claim are two claims, not a duplicate."""
        for version in ("1.0", "2.0"):
            created = self.post("/api/v1/vendor-devices", {
                "software_name": "Backup-Restore", "software_version": version,
                "make": "Arista", "model": "7050X",
            })
            self.assertEqual(created.status_code, 201, created.text)

    # ---------------------------------------------------------------- import

    def test_one_file_may_load_claims_for_several_versions(self):
        response = self.upload([
            {"software_name": "Backup-Restore", "software_version": "1.0",
             "make": "Cisco", "model": "C9200"},
            {"software_name": "Backup-Restore", "software_version": "2.0",
             "make": "Cisco", "model": "C9200"},
            {"software_name": "NetGuard", "software_version": "3.1",
             "make": "HPE", "model": "DL380"},
        ])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["created"], 3)

    def test_reimporting_the_same_file_updates_rather_than_duplicates(self):
        rows = [{"software_name": "NetGuard", "software_version": "3.1",
                 "make": "Lenovo", "model": "SR650", "support_status": "partial"}]
        self.assertEqual(self.upload(rows).json()["created"], 1)
        again = self.upload(rows).json()
        self.assertEqual((again["created"], again["updated"]), (0, 1))

    def test_a_row_naming_an_unknown_version_fails_alone(self):
        """The rest of the file still applies — one bad row is not a bad file."""
        response = self.upload([
            {"software_name": "NetGuard", "software_version": "3.1",
             "make": "Supermicro", "model": "X11"},
            {"software_name": "NetGuard", "software_version": "0.1",
             "make": "Broken", "model": "Row"},
        ])
        result = response.json()
        self.assertEqual(result["created"], 1)
        self.assertEqual([error["row"] for error in result["errors"]], [1])
        self.assertIn("0.1", result["errors"][0]["error"])

    def test_an_exported_catalogue_file_can_be_imported_back(self):
        """The template's columns are the export's columns for this reason."""
        self.post("/api/v1/vendor-devices", {
            "software_name": "NetGuard", "software_version": "3.1",
            "make": "Fortinet", "model": "FG-60F", "support_status": "unsupported",
        })
        exported = json.loads(self.get("/api/v1/vendor-devices/export?format=json").text)
        self.assertTrue(exported)
        self.assertIn("software_name", exported[0])
        self.assertIn("software_version", exported[0])

        result = self.upload(exported).json()
        self.assertEqual(result["errors"], [])
        # Every row already existed, so nothing was created.
        self.assertEqual(result["created"], 0)

    # -------------------------------------------------------------- template

    def test_the_template_leads_with_the_two_software_columns(self):
        response = self.get("/api/v1/vendor-devices/template")
        self.assertEqual(response.status_code, 200, response.text)
        header = response.text.splitlines()[0].strip().split(",")
        self.assertEqual(header[:2], ["software_name", "software_version"])
        self.assertIn("make", header)
        self.assertIn("support_status", header)
        self.assertNotIn("misc_data", header)

    def test_the_template_is_blank_below_its_header(self):
        rows = [line for line in self.get("/api/v1/vendor-devices/template").text.splitlines() if line.strip()]
        self.assertEqual(len(rows), 1, rows)


class VendorDeviceCatalogDeleteTests(SchemaCase):
    """Removing claims from the catalogue, where a selection spans versions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        backup = cls.post("/api/v1/software", {"name": "Backup-Restore", "version": "1.0"}).json()
        cls.post(f"/api/v1/software/{backup['id']}/versions", {
            "version": "2.0", "copy_vendor_devices": False,
        })
        cls.post("/api/v1/software", {"name": "NetGuard", "version": "3.1"})

    def a_claim(self, name, version, make, model):
        created = self.post("/api/v1/vendor-devices", {
            "software_name": name, "software_version": version,
            "make": make, "model": model,
        })
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()

    def test_a_selection_spanning_software_is_deleted_in_one_call(self):
        first = self.a_claim("Backup-Restore", "1.0", "Cisco", "ISR 4331")
        second = self.a_claim("NetGuard", "3.1", "Dell", "R640")
        response = self.post("/api/v1/vendor-devices/delete", {"ids": [first["id"], second["id"]]})
        self.assertEqual(response.status_code, 204, response.text)
        remaining = {item["id"] for item in self.get("/api/v1/vendor-devices").json()["items"]}
        self.assertNotIn(first["id"], remaining)
        self.assertNotIn(second["id"], remaining)

    def test_deleting_one_version_s_claim_leaves_the_other_s(self):
        """Two versions claiming the same device are two claims, not one."""
        old = self.a_claim("Backup-Restore", "1.0", "Arista", "7050X")
        new = self.a_claim("Backup-Restore", "2.0", "Arista", "7050X")
        self.assertEqual(
            self.post("/api/v1/vendor-devices/delete", {"ids": [old["id"]]}).status_code, 204,
        )
        remaining = {item["id"] for item in self.get("/api/v1/vendor-devices").json()["items"]}
        self.assertNotIn(old["id"], remaining)
        self.assertIn(new["id"], remaining)

    def test_an_unknown_id_is_skipped_rather_than_failing_the_call(self):
        """A row someone else already removed should not strand the rest."""
        claim = self.a_claim("NetGuard", "3.1", "HPE", "DL380")
        response = self.post("/api/v1/vendor-devices/delete", {
            "ids": [claim["id"], "00000000-0000-0000-0000-000000000000"],
        })
        self.assertEqual(response.status_code, 204, response.text)
        remaining = {item["id"] for item in self.get("/api/v1/vendor-devices").json()["items"]}
        self.assertNotIn(claim["id"], remaining)

    def test_it_is_audited_per_row_with_the_software_each_belonged_to(self):
        claim = self.a_claim("NetGuard", "3.1", "Fortinet", "FG-60F")
        software_id = claim["software_id"]
        self.post("/api/v1/vendor-devices/delete", {"ids": [claim["id"]]})
        entries = [
            item for item in self.get("/api/v1/audit_logs?page_size=200").json()["items"]
            if item["action"] == "software.vendor_device.delete"
            and item["entity_id"] == claim["id"]
        ]
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["detail"]["software_id"], software_id)
        self.assertTrue(entries[0]["detail"]["from_catalog"])

    def test_a_row_can_still_be_edited_through_its_own_software(self):
        """What the grid's inline edit does: PATCH against the row's software."""
        claim = self.a_claim("NetGuard", "3.1", "Lenovo", "SR650")
        updated = self.patch_(
            f"/api/v1/software/{claim['software_id']}/vendor-devices/{claim['id']}",
            {"support_status": "partial"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["support_status"], "partial")


class VendorDeviceCatalogCustomFieldTests(SchemaCase):
    """A catalogue file has to carry the installation's own vendor fields.

    Its own class because it adds a field to the catalog, which every other
    class here asserts the default shape of.

    The point is not that the export is tidier with the column in it. The
    catalogue export is also what the catalogue import reads, and an import
    writes the whole row — so a column the export leaves out comes back as "no
    value" and clears what somebody typed. These tests exist because the first
    round-trip test passed against a catalog with no custom fields at all, and
    so proved nothing about the case that actually breaks.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        created = cls.post("/api/v1/entity-fields/vendor_devices", {
            "key": "certified_by", "label": "Certified By", "field_type": "text",
        })
        assert created.status_code == 201, created.text
        cls.post("/api/v1/software", {"name": "Certifier", "version": "1.0"})

    @classmethod
    def a_claim(cls, model, certified_by):
        return cls.post("/api/v1/vendor-devices", {
            "software_name": "Certifier", "software_version": "1.0",
            "make": "Cisco", "model": model,
            # Custom values travel in misc_data, which is what the dialog packs
            # them into before sending.
            "misc_data": {"certified_by": certified_by},
        })

    def test_the_template_offers_the_custom_field_as_a_column(self):
        header = self.get("/api/v1/vendor-devices/template").text.splitlines()[0].strip().split(",")
        self.assertEqual(header[:2], ["software_name", "software_version"])
        self.assertIn("certified_by", header)

    def test_the_export_carries_the_custom_field_as_a_column(self):
        created = self.a_claim("ISR 4331", "Lab A")
        self.assertEqual(created.status_code, 201, created.text)
        exported = json.loads(self.get("/api/v1/vendor-devices/export?format=json").text)
        row = next(item for item in exported if item["model"] == "ISR 4331")
        self.assertEqual(row["certified_by"], "Lab A")

    def test_the_template_and_the_export_agree_on_their_columns(self):
        """They are read back by the same import, so they are the same shape."""
        self.a_claim("C9200", "Lab B")
        template = self.get("/api/v1/vendor-devices/template").text.splitlines()[0].strip().split(",")
        exported = json.loads(self.get("/api/v1/vendor-devices/export?format=json").text)
        # The export may carry read-only columns the template omits; every
        # column the template offers has to be one the export writes.
        self.assertEqual(set(template) - set(exported[0]), set())

    def test_a_round_trip_keeps_the_custom_value(self):
        """The regression: this cleared `certified_by` on every row it touched."""
        self.a_claim("SRX300", "Lab C")
        exported = json.loads(self.get("/api/v1/vendor-devices/export?format=json").text)

        response = self.client.post(
            "/api/v1/vendor-devices/import", headers=self.headers,
            files={"file": ("claims.json", io.BytesIO(json.dumps(exported).encode()), "application/json")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["errors"], [])

        after = {
            item["model"]: (item.get("misc_data") or {}).get("certified_by")
            for item in self.get("/api/v1/vendor-devices?software=Certifier").json()["items"]
        }
        self.assertEqual(after.get("SRX300"), "Lab C")
        # Every row the file touched, not just the one this test made.
        self.assertTrue(all(value for value in after.values()), after)

    def test_a_filled_in_template_column_lands_in_the_custom_field(self):
        rows = [{"software_name": "Certifier", "software_version": "1.0",
                 "make": "Dell", "model": "R640", "certified_by": "Lab D"}]
        response = self.client.post(
            "/api/v1/vendor-devices/import", headers=self.headers,
            files={"file": ("claims.json", io.BytesIO(json.dumps(rows).encode()), "application/json")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["errors"], [])
        row = next(
            item for item in self.get("/api/v1/vendor-devices?software=Certifier").json()["items"]
            if item["model"] == "R640"
        )
        self.assertEqual(row["misc_data"]["certified_by"], "Lab D")
