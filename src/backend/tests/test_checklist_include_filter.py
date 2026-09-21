"""Checklist filters that name what to keep rather than what to drop.

The filter sends the shorter side of the selection. Unticking a few values
sends those few as exclusions; clearing the column and ticking one sends that
one as an inclusion. The reason is the query string: a column with five hundred
distinct values cannot put four hundred and ninety-nine of them in a URL — the
proxy answers 414 and the grid shows nothing, which looks exactly like a filter
that matched nothing.

Including is not the inverse of excluding. Excluding hides what it names and
lets anything else through, a value this list has never seen included;
including shows only what it names.
"""

from test_device_schema_api import SchemaCase


class SoftwareIncludeFilterTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name in ("backup-restore", "netguard", "iperf3", "wireshark"):
            cls.post("/api/v1/software", {"name": name, "version": "1.0"})

    @classmethod
    def listed(cls, query):
        page = cls.get(f"/api/v1/software?latest_only=true&{query}").json()
        return sorted(item["name"] for item in page["items"])

    def test_including_one_name_returns_only_it(self):
        self.assertEqual(self.listed('include__name=["backup-restore"]'), ["backup-restore"])

    def test_including_several_returns_those(self):
        self.assertEqual(
            self.listed('include__name=["backup-restore","iperf3"]'),
            ["backup-restore", "iperf3"],
        )

    def test_including_nothing_returns_nothing(self):
        """Not everything: a checklist with no box ticked is showing no rows,
        and the filter has to say the same."""
        self.assertEqual(self.listed("include__name=[]"), [])

    def test_excluding_still_works_the_way_it_did(self):
        self.assertEqual(
            self.listed('exclude__name=["netguard","iperf3","wireshark"]'),
            ["backup-restore"],
        )

    def test_the_two_forms_agree_on_the_same_selection(self):
        """Same intent from either end, when the column's values are all known."""
        self.assertEqual(
            self.listed('include__name=["backup-restore"]'),
            self.listed('exclude__name=["netguard","iperf3","wireshark"]'),
        )


class DeviceIncludeFilterTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("/api/v1/devices", {"unique_id": "d-cisco", "make": "Cisco"})
        cls.post("/api/v1/devices", {"unique_id": "d-dell", "make": "Dell"})
        cls.post("/api/v1/devices", {"unique_id": "d-blank"})

    @classmethod
    def listed(cls, query):
        page = cls.get(f"/api/v1/devices?{query}").json()
        return sorted(item["unique_id"] for item in page["items"])

    def test_including_a_value_returns_only_its_rows(self):
        self.assertEqual(self.listed('include__make=["Cisco"]'), ["d-cisco"])

    def test_the_blank_entry_selects_the_rows_with_no_value(self):
        self.assertEqual(self.listed("include__make=[null]"), ["d-blank"])
        self.assertEqual(self.listed('include__make=[""]'), ["d-blank"])

    def test_a_value_and_the_blank_together(self):
        self.assertEqual(
            self.listed('include__make=["Cisco",null]'), ["d-blank", "d-cisco"],
        )

    def test_including_differs_from_excluding_for_unseen_values(self):
        """The distinction that matters: excluding Dell leaves the blank row
        showing, because excluding only hides what it names."""
        self.assertEqual(self.listed('exclude__make=["Dell"]'), ["d-blank", "d-cisco"])
        self.assertEqual(self.listed('include__make=["Cisco"]'), ["d-cisco"])
