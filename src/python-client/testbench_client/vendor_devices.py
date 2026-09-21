"""Vendor devices: the hardware vendors claim their software supports.

Read-only. These rows are a vendor's published compatibility list, imported
from a datasheet or a matrix; nothing a test run does should be writing them.

The distinction this module exists to protect is the same one the rest of the
system protects, and it is easy to lose:

* a **vendor device** is a CLAIM — the vendor says their software works on that
  hardware. It is not evidence, and the fleet need not own one.
* a **test** is EVIDENCE — that software was actually run against a device here,
  with an outcome. Absence of one means untested, not unsupported.

So `tb.vendor_devices.for_device(...)` answers "who claims to support this
box?" and `tb.tests.for_device(...)` answers "what has it actually run?".
Neither answers the other, and code that treats them as interchangeable is the
bug this docstring is trying to prevent.
"""

from __future__ import annotations

from typing import Iterator

from .models import VENDOR_SUPPORT_STATUSES, Device, Software, VendorDevice


class VendorDevices:
    """Accessed as `tb.vendor_devices`.

    Searches every software version's compatibility list at once. To read one
    software's list on its own, pass `software=`.
    """

    def __init__(self, client) -> None:
        self._dm = client

    def list(
        self,
        *,
        search: str | None = None,
        software: str | Software | None = None,
        make: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        hardware_version: str | None = None,
        architecture: str | None = None,
        support_status: str | None = None,
        sort: str = "make",
        order: str = "asc",
        limit: int | None = None,
    ) -> list[VendorDevice]:
        """Every claim matching the filters, across as many pages as it takes.

        `search` matches make, model, firmware, hardware version, architecture,
        source, notes and the software's own name and version at once; the
        named filters are narrower and combine with AND.

        `software` takes a name and covers **every version of it** — a claim
        made by 1.0 and one made by 2.0 are separate rows, and both come back.
        Use `for_software()` to read one version's list on its own.

        `support_status` is the vendor's word: supported, partial, unsupported
        or planned. `unsupported` rows are returned like any other, because
        "the vendor says no" is an answer.
        """
        return list(self.iter(
            search=search, software=software, make=make, model=model,
            firmware_version=firmware_version, hardware_version=hardware_version,
            architecture=architecture, support_status=support_status,
            sort=sort, order=order, limit=limit,
        ))

    def iter(
        self,
        *,
        search: str | None = None,
        software: str | Software | None = None,
        make: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        hardware_version: str | None = None,
        architecture: str | None = None,
        support_status: str | None = None,
        sort: str = "make",
        order: str = "asc",
        limit: int | None = None,
    ) -> Iterator[VendorDevice]:
        """Like `list()`, but yields as pages arrive rather than buffering."""
        if support_status is not None:
            _check_support_status(support_status)
        params = {
            "search": search,
            # The API takes a software *name* here, which is what covers every
            # version of it; a Software record is one version, so its name is
            # what is sent rather than its id.
            "software": software.name if isinstance(software, Software) else software,
            "make": make, "model": model,
            "firmware_version": firmware_version,
            "hardware_version": hardware_version,
            "architecture": architecture,
            "support_status": support_status,
            "sort": sort, "order": order,
        }
        for row in self._dm.paginate("/vendor-devices", params, limit=limit):
            yield VendorDevice.from_dict(row)

    def for_software(
        self,
        name: str | Software,
        version: str | None = None,
        *,
        search: str | None = None,
        support_status: str | None = None,
        limit: int | None = None,
    ) -> list[VendorDevice]:
        """One software version's own compatibility list.

        A bare name gives the current version's list, the same way every other
        name in this library resolves. Note that versions do not inherit from
        one another: a new version starts as a copy and the two diverge, so
        this is genuinely the list that version publishes — not its ancestor's.

        Resolved to an id and asked of the catalogue rather than of the
        software's own sub-resource: the catalogue takes the same filters as
        `list()` and its rows already name the software they belong to, so a
        record is the same shape whichever way it arrived.
        """
        if support_status is not None:
            _check_support_status(support_status)
        software_id = self._dm.software.resolve(name, version)
        params = {
            "software_id": software_id,
            "search": search,
            "support_status": support_status,
        }
        return [
            VendorDevice.from_dict(row)
            for row in self._dm.paginate("/vendor-devices", params, limit=limit)
        ]

    def for_device(
        self,
        identifier: str | Device,
        *,
        supported_only: bool = False,
        limit: int | None = None,
    ) -> list[VendorDevice]:
        """Claims describing a device in the fleet, by its own make and model.

        A convenience over `list()`, not a new question: it reads the device's
        make and model and searches the catalogue for them. Claims are matched
        on the hardware a vendor published, so a claim naming only a make will
        not be found by a search that also pins the model — this errs towards
        the narrow answer, which is the one that is actually about this device.

        Returns nothing for a device with neither a make nor a model; there is
        nothing to match on, and every claim in the catalogue is not an answer.
        """
        device = identifier if isinstance(identifier, Device) else self._dm.devices.get(identifier)
        if not device.make and not device.model:
            return []
        rows = self.list(make=device.make or None, model=device.model or None, limit=limit)
        return [row for row in rows if row.is_supported] if supported_only else rows


def _check_support_status(status: str) -> None:
    if status not in VENDOR_SUPPORT_STATUSES:
        raise ValueError(
            f"support_status must be one of {', '.join(VENDOR_SUPPORT_STATUSES)}; got {status!r}"
        )
