"""The fleet view's online-state poll, over devices that were never scanned.

`online_status` lives in the device document, and its SQL expression is a cast
of the JSON key. A device that has never been scanned has no key, so the
column came back NULL rather than the attribute's Python default of False, and
`DeviceScanState` refused it — the whole poll failed with a 500, and the online
dots never moved during a scan.
"""

from test_device_schema_api import SchemaCase


class ScanResultsTests(SchemaCase):
    def test_a_never_scanned_device_reads_as_offline(self):
        self.post("/api/v1/devices", {"unique_id": "never-scanned"})
        response = self.get("/api/v1/devices/scan/results")
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()[0]
        self.assertIs(state["online_status"], False)
        self.assertIsNone(state["last_scanned_at"])
