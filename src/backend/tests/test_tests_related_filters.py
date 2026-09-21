"""Filtering tests by the rows they point at.

A test's device, software and author are rows of their own, so a filter on any
of them has to join to reach it. All three used to be skipped in the filter
loop — which did not disable them, it ignored them: the grid narrowed to one
device and the server answered with every test there was, which reads as a
filter that does nothing rather than one that is unsupported.
"""

from test_device_schema_api import SchemaCase


class TestRelatedColumnFilterTests(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        devices = {}
        for unique_id in ("dev-a", "dev-b"):
            devices[unique_id] = cls.post(
                "/api/v1/devices", {"unique_id": unique_id, "make": "Cisco"},
            ).json()["id"]
        software = {}
        for name in ("alpha", "beta"):
            software[name] = cls.post(
                "/api/v1/software", {"name": name, "version": "1.0"},
            ).json()["id"]
        for device, suite, outcome in (
            ("dev-a", "alpha", "pass"),
            ("dev-a", "beta", "fail"),
            ("dev-b", "alpha", "pass"),
            ("dev-b", "beta", "pass"),
        ):
            created = cls.post("/api/v1/tests", {
                "device_id": devices[device], "software_id": software[suite],
                "outcome": outcome,
            })
            assert created.status_code == 201, created.text

    @classmethod
    def count(cls, query=""):
        return cls.get(f"/api/v1/tests?page_size=50{f'&{query}' if query else ''}").json()["total"]

    def test_the_fixture_is_what_it_says(self):
        self.assertEqual(self.count(), 4)

    def test_filtering_by_device_narrows_the_list(self):
        """The report: this returned every test there was."""
        self.assertEqual(self.count('include__device_unique_id=["dev-a"]'), 2)

    def test_filtering_by_software_narrows_the_list(self):
        self.assertEqual(self.count('include__software_name=["alpha"]'), 2)

    def test_filtering_by_author_narrows_the_list(self):
        self.assertEqual(self.count('include__created_by_username=["admin"]'), 4)
        self.assertEqual(self.count('include__created_by_username=["nobody"]'), 0)

    def test_excluding_a_device_leaves_the_others(self):
        self.assertEqual(self.count('exclude__device_unique_id=["dev-a"]'), 2)

    def test_clearing_a_related_column_shows_nothing(self):
        """A checklist with no box ticked is showing no rows, whichever column
        it is on — this is the shape the grid sends when you clear one."""
        for field in ("device_unique_id", "software_name", "created_by_username"):
            with self.subTest(field):
                self.assertEqual(self.count(f"include__{field}=[]"), 0)

    def test_a_related_filter_and_a_search_compose(self):
        """Both reach for the same joins; making them twice is a different
        query, and usually an empty one."""
        self.assertEqual(self.count('include__device_unique_id=["dev-a"]&search=alpha'), 1)
        self.assertEqual(self.count('include__software_name=["alpha"]&search=dev-b'), 1)

    def test_two_related_filters_compose(self):
        self.assertEqual(
            self.count('include__device_unique_id=["dev-a"]&include__software_name=["alpha"]'), 1,
        )

    def test_a_related_filter_composes_with_a_plain_one(self):
        self.assertEqual(
            self.count('include__device_unique_id=["dev-a"]&include__outcome=["pass"]'), 1,
        )
