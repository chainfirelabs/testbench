"""Dashboard widget aggregates against a disposable PostgreSQL database."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.api.dashboard import WIDGET_TYPES
from app.db import SessionLocal
from app.models import Device, DeviceType, Software, Test
from test_device_schema_api import SchemaCase


class DashboardDataTests(SchemaCase):
    def test_every_widget_has_data_and_rankings_are_bounded(self):
        empty = self.post("/api/v1/dashboard/data", {"widgets": [
            {"id": kind.replace("_", "-"), "type": kind, "title": kind, "description": ""}
            for kind in WIDGET_TYPES
        ]})
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["widgets"]["fleet-summary"]["total"], 0)
        self.assertEqual(empty.json()["widgets"]["devices-by-make"]["items"], [])

        with SessionLocal() as db:
            router = db.scalar(select(DeviceType).where(DeviceType.key == "router"))
            first = Device(unique_id="lab-router-1", device_type_id=router.id)
            first.make = "Acme"
            first.status = "checked_out"
            first.checkout_due = date.today() - timedelta(days=1)
            first.last_scanned_at = datetime.now(timezone.utc)
            first.online_status = True
            first.firmware_version = "1.0"
            second = Device(unique_id="lab-unknown-1")
            second.make = ""
            software = Software()
            software.name = "Test OS"
            software.version = "2.0"
            older_software = Software()
            older_software.name = "Legacy App"
            older_software.version = "1.0"
            db.add_all((first, second, software, older_software))
            db.flush()
            for device, outcome in ((first, "pass"), (first, "fail"), (second, "warn")):
                record = Test(software_id=software.id, device_id=device.id)
                record.outcome = outcome
                record.run_at = date.today()
                db.add(record)
            old_record = Test(software_id=older_software.id, device_id=first.id)
            old_record.outcome = "pass"
            old_record.run_at = date.today() - timedelta(days=90)
            db.add(old_record)
            db.commit()
            first_id, second_id, software_id = first.id, second.id, software.id

        widgets = [{
            "id": kind.replace("_", "-"), "type": kind, "title": kind,
            "description": "", "body": "Scheduled maintenance" if kind == "announcement" else "",
            "device_type_key": "router" if kind == "firmware_coverage" else None,
        } for kind in WIDGET_TYPES]
        response = self.post("/api/v1/dashboard/data", {"widgets": widgets})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["widgets"]
        self.assertEqual(len(data), len(WIDGET_TYPES))
        self.assertEqual(data["fleet-summary"]["total"], 2)
        self.assertEqual(data["fleet-summary"]["online"], 1)
        self.assertEqual(data["fleet-summary"]["never_scanned"], 1)
        self.assertEqual(data["devices-by-make"]["total"], 2)
        self.assertEqual({item["label"] for item in data["devices-by-make"]["items"]}, {"Acme", "Unknown"})
        self.assertEqual(data["checkouts-due"]["overdue"], 1)
        self.assertEqual(data["most-tested-software"]["items"][0]["count"], 3)
        self.assertEqual(data["most-tested-software"]["items"][0]["id"], software_id)
        self.assertEqual(data["most-tested-devices"]["items"][0]["id"], first_id)
        self.assertEqual(data["most-tested-devices"]["items"][0]["count"], 3)
        self.assertEqual(data["recent-problem-tests"]["items"][0]["outcome"] in ("fail", "warn"), True)
        self.assertEqual(data["untested-devices"]["total"], 0)
        self.assertEqual(data["scan-freshness"]["never_scanned"], 1)
        self.assertEqual(data["software-outcomes"]["items"][0]["total"], 3)
        self.assertEqual(data["firmware-coverage"]["total"], 1)
        self.assertEqual(data["announcement"]["body"], "Scheduled maintenance")
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.get("/api/v1/devices?scan_state=never_scanned").json()["total"], 1)
        self.assertEqual(self.get("/api/v1/devices?scan_state=online").json()["total"], 1)
        self.assertEqual(self.get("/api/v1/devices?make_group=Unknown").json()["total"], 1)
        self.assertEqual(self.get("/api/v1/devices?status=available").json()["total"], 1)

        recent = self.post("/api/v1/dashboard/data", {"widgets": [
            {**widgets[7], "days": 30},
        ]})
        self.assertEqual(recent.status_code, 200, recent.text)
        self.assertEqual(len(recent.json()["widgets"]["most-tested-software"]["items"]), 1)
