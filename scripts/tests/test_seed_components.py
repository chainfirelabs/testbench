"""Component count and claim fixtures shared by the seed scripts."""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seed_components import component_additions, component_support_for, components_for


class SeedComponentsTests(unittest.TestCase):
    def test_component_count_is_not_capped_at_five(self):
        generated = components_for("Suite", "2.0", 10)
        self.assertEqual(len(generated), 10)
        self.assertEqual(len({item["name"] for item in generated}), 10)

    def test_additions_preserve_existing_components(self):
        existing = [{"id": "own-1", "name": "Custom", "version": "3"}]
        additions = component_additions(existing, "Suite", "2.0", 4)
        self.assertEqual(len(additions), 3)
        self.assertEqual(component_additions(existing + additions, "Suite", "2.0", 4), [])

    def test_claims_link_requested_count_and_show_different_statuses(self):
        components = [{"id": f"c-{i}"} for i in range(5)]
        links = component_support_for(components, 4, "supported", random.Random(7))
        self.assertEqual(len(links), 4)
        self.assertEqual(len({link["component_id"] for link in links}), 4)
        self.assertTrue(any(link["support_status"] != "supported" for link in links))
        self.assertEqual(component_support_for(components, 0, "supported", random.Random(7)), [])


if __name__ == "__main__":
    unittest.main()
