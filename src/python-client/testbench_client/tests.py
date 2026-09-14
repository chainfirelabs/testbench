"""Test results: recording evidence that software ran against a device.

The module is named for the domain entity, not for this package's own test
suite — those live in `python-client/tests/`.
"""

from __future__ import annotations

from datetime import date
from typing import Iterator
from urllib.parse import quote

from .models import TEST_OUTCOMES, TEST_TAGS, Device, Software, Test, to_api_date


class Tests:
    """Accessed as `tb.tests`."""

    def __init__(self, client) -> None:
        self._dm = client

    # -- writing ----------------------------------------------------------

    def create(
        self,
        *,
        device: str | Device,
        software: str | Software,
        outcome: str,
        tag: str = "adhoc",
        software_version: str | None = None,
        misc_data: dict | None = None,
        notes: str | None = None,
        run_at: date | str | None = None,
    ) -> Test:
        """Record one run.

        ```python
        tb.tests.create(device="dev-0042", software="nmap", outcome="pass",
                        tag="acceptance", misc_data={"duration_s": 12.4})
        ```

        `device` and `software` are the names you already have; the UUIDs the
        API wants are resolved and cached for you.

        Leaving `software_version` unset records the version the software is on
        now, which is almost always right — the run exercised whatever is
        current. Set it when the run exercised a build the catalogue has moved
        past.

        `misc_data` is free-form JSON: durations, throughput, log URLs, exit codes.
        It is the field that makes these results worth querying later, so put
        the numbers in it rather than in `notes`.
        """
        _check_enum("outcome", outcome, TEST_OUTCOMES)
        _check_enum("tag", tag, TEST_TAGS)
        body = {
            "device_id": self._dm.devices.resolve(device),
            "software_id": self._dm.software.resolve(software, software_version),
            "outcome": outcome,
            "tag": tag,
            "misc_data": misc_data or {},
            "notes": notes,
            "run_at": to_api_date(run_at),
            "software_version": software_version,
        }
        return Test.from_dict(self._dm.request("POST", "/tests", json=body))

    def record(self, device, software, passed: bool, **kwargs) -> Test:
        """`create()` for the common case where a framework has a boolean.

        Maps True to "pass" and False to "fail". "warn" has no boolean, so
        reach for `create()` when you want it.
        """
        return self.create(device=device, software=software,
                           outcome="pass" if passed else "fail", **kwargs)

    def update(self, test: str | Test, **fields) -> Test:
        """Amend a recorded result (outcome, tag, misc_data, notes, run_at)."""
        key = test.id if isinstance(test, Test) else str(test)
        if "run_at" in fields:
            fields["run_at"] = to_api_date(fields["run_at"])
        return Test.from_dict(self._dm.request("PATCH", f"/tests/{quote(key, safe='')}", json=fields))

    def delete(self, test: str | Test) -> None:
        key = test.id if isinstance(test, Test) else str(test)
        self._dm.request("DELETE", f"/tests/{quote(key, safe='')}")

    # -- reading ----------------------------------------------------------

    def list(
        self,
        *,
        device: str | Device | None = None,
        software: str | Software | None = None,
        software_version: str | None = None,
        outcome: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        fields: dict[str, str | int | float | bool] | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int | None = None,
    ) -> list[Test]:
        """Recorded runs, newest first, across as many pages as it takes."""
        return list(self.iter(device=device, software=software,
                              software_version=software_version, outcome=outcome,
                              tag=tag, search=search, fields=fields, sort=sort, order=order, limit=limit))

    def iter(
        self,
        *,
        device: str | Device | None = None,
        software: str | Software | None = None,
        software_version: str | None = None,
        outcome: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        fields: dict[str, str | int | float | bool] | None = None,
        sort: str = "created_at",
        order: str = "desc",
        limit: int | None = None,
    ) -> Iterator[Test]:
        if outcome is not None:
            _check_enum("outcome", outcome, TEST_OUTCOMES)
        if tag is not None:
            _check_enum("tag", tag, TEST_TAGS)
        params = {**(fields or {}),
            "device_id": self._dm.devices.resolve(device) if device is not None else None,
            "software_id": (
                self._dm.software.resolve(software, software_version)
                if software is not None else None
            ),
            "outcome": outcome,
            "tag": tag,
            "search": search,
            "sort": sort,
            "order": order,
        }
        for row in self._dm.paginate("/tests", params, limit=limit):
            yield Test.from_dict(row)

    def get(self, test_id: str) -> Test:
        return Test.from_dict(self._dm.request("GET", f"/tests/{quote(test_id, safe='')}"))

    def for_device(self, device: str | Device, *, limit: int | None = None) -> list[Test]:
        """Every recorded run against one device, newest first."""
        return self.list(device=device, limit=limit)


def _check_enum(field: str, value: str, allowed: tuple[str, ...]) -> None:
    # Caught here rather than at the API: the round trip is wasted either way,
    # and a filter with a bad value returns an empty page instead of an error,
    # which reads as "no such results exist".
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}, got '{value}'")
