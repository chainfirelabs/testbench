from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase

from app.api.software import _newest_version, _version_key


class SoftwareVersionOrderingTests(TestCase):
    def test_numeric_versions_are_not_ordered_by_creation_time(self):
        older_high = self._row("6.0.0", day=1)
        newer_low = self._row("2.5.3", day=2)

        self.assertIs(_newest_version([older_high, newer_low]), older_high)

    def test_numeric_components_sort_naturally(self):
        self.assertGreater(_version_key("10.0.0"), _version_key("9.9.9"))
        self.assertGreater(_version_key("1.10"), _version_key("1.9"))
        self.assertEqual(_version_key("1.0"), _version_key("1.0.0"))

    def test_release_sorts_above_its_prerelease(self):
        self.assertGreater(_version_key("2.0.0"), _version_key("2.0.0-rc1"))

    @staticmethod
    def _row(version: str, day: int):
        return SimpleNamespace(
            version=version,
            created_at=datetime(2026, 1, day, tzinfo=timezone.utc),
            id=str(day),
        )
