"""Listing the tests behind one Software → Tested Devices row.

That list groups a software version's tests by device and component, and tests
run against the software as a whole form a group of their own. Opening a row
asks for exactly its tests, so the list has to narrow by component, including
to "no component".
"""

from test_device_schema_api import SchemaCase


class TestComponentFilter(SchemaCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device = cls.post("/api/v1/devices", {"unique_id": "cf-dev", "make": "Cisco"}).json()["id"]
        software = cls.post("/api/v1/software", {
            "name": "cf-suite", "version": "1.0",
            "bundle_components": [{"name": "Agent", "version": "2.0"}],
        }).json()
        cls.software = software["id"]
        cls.component = software["bundle_components"][0]["id"]
        for component, outcome in ((cls.component, "pass"), (cls.component, "fail"), (None, "pass")):
            created = cls.post("/api/v1/tests", {
                "device_id": cls.device, "software_id": cls.software,
                "component_id": component, "outcome": outcome,
            })
            assert created.status_code == 201, created.text

    @classmethod
    def items(cls, component_id):
        return cls.get(
            f"/api/v1/tests?device_id={cls.device}&software_id={cls.software}&component_id={component_id}",
        ).json()["items"]

    def test_a_component_lists_only_its_own_tests(self):
        items = self.items(self.component)
        self.assertEqual(sorted(item["outcome"] for item in items), ["fail", "pass"])
        self.assertTrue(all(item["component_id"] == self.component for item in items))

    def test_none_lists_the_tests_of_the_software_as_a_whole(self):
        items = self.items("none")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["component_id"])
