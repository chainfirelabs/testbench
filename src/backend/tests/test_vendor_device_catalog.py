"""Searching every vendor compatibility list at once."""

from test_device_schema_api import SchemaCase


class VendorDeviceCatalogTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        backup = cls.post("/api/v1/software", {"name": "Backup-Restore", "version": "1.0"}).json()
        newer = cls.post(f"/api/v1/software/{backup['id']}/versions", {
            "version": "2.0", "copy_vendor_devices": False,
        }).json()
        guard = cls.post("/api/v1/software", {"name": "NetGuard", "version": "3.1"}).json()
        cls.claims = {
            backup["id"]: [("Cisco", "ISR 4331", "supported"), ("Juniper", "SRX300", "partial")],
            newer["id"]: [("Cisco", "ISR 4331", "supported")],
            guard["id"]: [("Cisco", "Catalyst 9300", "unsupported"), ("Dell", "R640", "supported")],
        }
        for software_id, rows in cls.claims.items():
            for make, model, status in rows:
                cls.post(f"/api/v1/software/{software_id}/vendor-devices", {
                    "make": make, "model": model, "support_status": status,
                })

    @classmethod
    def search(cls, query=""):
        return cls.get(f"/api/v1/vendor-devices{f'?{query}' if query else ''}").json()

    def test_every_claim_is_listed_with_the_software_that_makes_it(self):
        page = self.search()
        self.assertEqual(page["total"], 5)
        self.assertEqual(
            {(item["software_name"], item["software_version"], item["make"]) for item in page["items"]},
            {
                ("Backup-Restore", "1.0", "Cisco"), ("Backup-Restore", "1.0", "Juniper"),
                ("Backup-Restore", "2.0", "Cisco"),
                ("NetGuard", "3.1", "Cisco"), ("NetGuard", "3.1", "Dell"),
            },
        )

    def test_a_claim_on_a_superseded_version_says_so(self):
        by_version = {
            item["software_version"]: item["software_is_latest"]
            for item in self.search("software=Backup-Restore")["items"]
        }
        self.assertEqual(by_version, {"1.0": False, "2.0": True})

    def test_search_matches_the_hardware_and_the_software(self):
        self.assertEqual(self.search("search=ISR")["total"], 2)
        self.assertEqual(self.search("search=netguard")["total"], 2)

    def test_named_filters_combine(self):
        page = self.search("make=Cisco&support_status=supported")
        self.assertEqual(page["total"], 2)
        self.assertTrue(all(item["model"] == "ISR 4331" for item in page["items"]))

    def test_the_checklist_exclusion_filter_applies(self):
        page = self.search('exclude__make=["Dell", "Juniper"]')
        self.assertEqual(page["total"], 3)

    def test_offset_paginates_by_row(self):
        first = self.search("page_size=2")
        rest = self.search("page_size=2&offset=2")
        self.assertEqual(first["total"], rest["total"])
        self.assertEqual(rest["page"], 2)
        self.assertFalse(
            {item["id"] for item in first["items"]} & {item["id"] for item in rest["items"]}
        )

    def test_global_search_finds_a_claim_no_page_could_be_started_from(self):
        results = self.get("/api/v1/search?q=Catalyst").json()
        self.assertEqual(results["vendor_devices_total"], 1)
        claim = results["vendor_devices"][0]
        self.assertEqual((claim["software_name"], claim["model"]), ("NetGuard", "Catalyst 9300"))

    def test_the_export_carries_the_software_alongside_the_hardware(self):
        response = self.get("/api/v1/vendor-devices/export?format=csv&make=Dell")
        self.assertEqual(response.status_code, 200, response.text)
        header, row = response.text.splitlines()[:2]
        self.assertEqual(header.split(",")[:3], ["software_name", "software_version", "make"])
        self.assertTrue(row.startswith("NetGuard,3.1,Dell"))


class TestedDevicePagingTests(SchemaCase):
    def test_the_window_reports_how_many_rows_there_are(self):
        """Without a total the grid reading this pages on past the data forever."""
        software = self.post("/api/v1/software", {"name": "Prober", "version": "1"}).json()
        for index in range(3):
            device = self.post("/api/v1/devices", {"unique_id": f"probe-{index}"}).json()
            self.post("/api/v1/tests", {
                "software_id": software["id"], "device_id": device["id"], "outcome": "pass",
            })
        page = self.get(f"/api/v1/software/{software['id']}/tested-devices?page=1&page_size=2")
        body = page.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 2)
        self.assertEqual(len(body["devices"]), 2)
