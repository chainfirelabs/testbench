"""Devices: listing, lookup, and the checkout workflow."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from typing import Iterator
from urllib.parse import quote

from .errors import DeviceUnavailable, NotFound, UnknownDevice
from .models import DEVICE_STATUSES, Device, to_api_date

logger = logging.getLogger("testbench_client")


class Devices:
    """Accessed as `tb.devices`.

    Devices are addressed by their `unique_id` ("dev-0042") everywhere in this
    library. The API accepts a UUID in the same position, so either works, but
    nothing here requires you to know one.
    """

    def __init__(self, client) -> None:
        self._dm = client

    # -- reading ----------------------------------------------------------

    def list(
        self,
        *,
        search: str | None = None,
        device_type: str | None = None,
        status: str | None = None,
        location: str | None = None,
        overdue: bool | None = None,
        fields: dict[str, str | int | float | bool] | None = None,
        sort: str = "unique_id",
        order: str = "asc",
        limit: int | None = None,
    ) -> list[Device]:
        """Every device matching the filters, across as many pages as it takes.

        With no filters this is the whole fleet. `limit` caps the rows fetched,
        not just the rows returned — it stops paging early.

        `overdue=True` narrows to devices past their return date; False to
        everything else. `device_type` takes a stable type key — "router",
        "mobile", or "uncategorized" for devices with no type — never a label,
        which an administrator may rename at any time.

        `fields` filters on any field this installation defines, by its stable
        key: `fields={"serial_number": "SN-1"}`.
        """
        return list(self.iter(search=search, device_type=device_type, status=status, location=location,
                              overdue=overdue, fields=fields, sort=sort, order=order, limit=limit))

    def iter(
        self,
        *,
        search: str | None = None,
        device_type: str | None = None,
        status: str | None = None,
        location: str | None = None,
        overdue: bool | None = None,
        fields: dict[str, str | int | float | bool] | None = None,
        sort: str = "unique_id",
        order: str = "asc",
        limit: int | None = None,
    ) -> Iterator[Device]:
        """Like `list()`, but yields as pages arrive rather than buffering."""
        if status is not None:
            _check_status(status)
        params = {**(fields or {}), "search": search, "device_type": device_type, "status": status, "location": location,
                  "overdue": overdue, "sort": sort, "order": order}
        for row in self._dm.paginate("/devices", params, limit=limit):
            yield Device.from_dict(row)

    def overdue(self, *, min_days: int = 1, limit: int | None = None) -> list[Device]:
        """Devices past their return date, longest overdue first.

        The dedicated endpoint rather than `list(overdue=True)`: it sorts by how
        late each one is, which is the order you want when the list is a queue
        of people to go and ask.
        """
        return [
            Device.from_dict(row)
            for row in self._dm.paginate("/devices/overdue", {"min_days": min_days}, limit=limit)
        ]

    def get(self, identifier: str | Device) -> Device:
        """One device by unique_id (or UUID), with its test history attached.

        This is the detail record: `device.tests` and `device.verified_tests`
        are populated, which `list()` does not do.
        """
        key = _key(identifier)
        try:
            if "/" in key or key in {".", ".."}:
                return Device.from_dict(self._dm.request(
                    "GET", "/devices/lookup/by-unique-id", params={"unique_id": key},
                ))
            return Device.from_dict(self._dm.request("GET", f"/devices/{quote(key, safe='')}"))
        except NotFound as exc:
            raise self._unknown(key, exc) from exc

    def exists(self, identifier: str | Device) -> bool:
        try:
            self.get(identifier)
            return True
        except UnknownDevice:
            return False

    def actions(self, identifier: str | Device) -> list[dict]:
        """Plugin actions for one device, each saying whether it can run.

        Not every installed action applies to every device: a plugin is
        available only where the device type's policy allows it and the fields
        it needs are both configured and filled in. Each entry carries
        `available` and, when it is false, `unavailable_reason`.
        """
        key = _key(identifier)
        try:
            if "/" in key or key in {".", ".."}:
                key = self.get(key).id
            return self._dm.request("GET", f"/devices/{quote(key, safe='')}/actions").get("actions", [])
        except NotFound as exc:
            raise self._unknown(key, exc) from exc

    def schema(self, identifier: str | Device) -> dict:
        """The published schema for this device's type."""
        device = self.get(identifier)
        return self._dm.device_schema(device.device_type_key)

    def resolve(self, identifier: str | Device) -> str:
        """The device's UUID, which `POST /tests` needs and people do not have.

        Cached for the life of the client: a device's id never changes, and a
        suite recording a result per test case would otherwise re-fetch the
        same row hundreds of times.
        """
        if isinstance(identifier, Device) and identifier.id:
            return identifier.id
        key = str(identifier)
        cached = self._dm.cache_get("device", key)
        if cached:
            return cached
        device = self.get(key)
        self._dm.cache_put("device", key, device.id)
        self._dm.cache_put("device", device.unique_id, device.id)
        return device.id

    # -- status -----------------------------------------------------------

    def set_status(self, identifier: str | Device, status: str, **fields) -> Device:
        """Set a device's status, and optionally other fields in the same call.

        Crossing into `checked_out` stamps the key owner and the day; crossing
        out of it clears them, along with the purpose and the return date. That
        happens server-side — this does not, and cannot, set `checked_out_by`
        itself.

        Note that entering `checked_out` needs `checkout_purpose` and
        `checkout_due` passed as fields, or the API returns 422. `check_out()`
        is the method that gets that right.
        """
        _check_status(status)
        key = _key(identifier)
        try:
            if "/" in key or key in {".", ".."}:
                key = self.get(key).id
            body = self._dm.request("PATCH", f"/devices/{quote(key, safe='')}", json={"status": status, **fields})
        except NotFound as exc:
            raise self._unknown(key, exc) from exc
        return Device.from_dict(body)

    def check_out(
        self,
        identifier: str | Device,
        *,
        purpose: str,
        due: date | str,
        force: bool = False,
    ) -> Device:
        """Take the device, refusing if it is not free.

        `purpose` and `due` are required, because the API requires them: a
        checkout has to say why the device is out and when it is coming back.
        Past `due` the device shows as overdue and whoever holds it is
        reminded daily — nothing takes it back automatically.

        The API does not enforce exclusivity: `PATCH status=checked_out` on a
        device someone else already holds is a no-op that returns 200, leaving
        their name on it. So this checks first, and checks again afterwards —
        if another run claimed it in between, the second check catches it and
        raises rather than letting a suite proceed on hardware it does not own.

        That is a guard, not a lock. Two clients calling this microseconds apart
        can still both believe they won; the loser finds out on the re-read.
        For genuinely contended hardware, serialise at a level that has a lock
        to offer (a CI job queue, a resource plugin).

        Already holding it is not an error — this is safe to call twice.
        """
        if not purpose or not str(purpose).strip():
            raise ValueError("check_out() needs a purpose")
        if due is None:
            raise ValueError("check_out() needs a return date")
        details = {"checkout_purpose": purpose, "checkout_due": to_api_date(due)}

        me = self._dm.identity.username
        device = self.get(identifier)

        if device.is_checked_out:
            if device.checked_out_by_username == me:
                # Already ours, so no status change and no guard to satisfy —
                # but restate the reason and the deadline rather than leaving a
                # stale pair behind. Calling this again with a later date is
                # how a checkout gets extended.
                body = self._dm.request(
                    "PATCH", f"/devices/{quote(device.id, safe='')}", json=details
                )
                return Device.from_dict(body)
            if not force:
                raise DeviceUnavailable(
                    f"{device.unique_id} is checked out by "
                    f"{device.checked_out_by_username or 'someone else'}.",
                    device=device,
                )
        elif not device.is_available and not force:
            raise DeviceUnavailable(
                f"{device.unique_id} is '{device.status}', not available. "
                f"Pass force=True to take it anyway.",
                device=device,
            )

        if force and device.is_checked_out and device.checked_out_by_username != me:
            # The stamp only moves when the status *changes*, so a takeover has
            # to leave checked_out before re-entering it.
            logger.warning("Taking %s from %s", device.unique_id,
                           device.checked_out_by_username)
            self.set_status(device.unique_id, "available")

        updated = self.set_status(device.unique_id, "checked_out", **details)
        if updated.checked_out_by_username != me:
            raise DeviceUnavailable(
                f"{updated.unique_id} was claimed by "
                f"{updated.checked_out_by_username or 'someone else'} while we were "
                f"checking it out.",
                device=updated,
            )
        return updated

    def check_in(self, identifier: str | Device, *, only_if_mine: bool = True) -> Device:
        """Return the device to `available`.

        Idempotent: a device that is not checked out is left exactly as it is,
        including one someone deliberately marked `broken` — an automated
        release should not quietly undo that.
        """
        device = self.get(identifier)
        if not device.is_checked_out:
            return device
        me = self._dm.identity.username
        if only_if_mine and device.checked_out_by_username != me:
            raise DeviceUnavailable(
                f"{device.unique_id} is checked out by "
                f"{device.checked_out_by_username or 'someone else'}, not by {me}. "
                f"Pass only_if_mine=False to return it anyway.",
                device=device,
            )
        return self.set_status(device.unique_id, "available")

    @contextmanager
    def reserved(
        self,
        identifier: str | Device,
        *,
        purpose: str,
        due: date | str,
        force: bool = False,
    ):
        """Hold a device for the duration of a block, and always give it back.

        ```python
        with tb.devices.reserved("dev-0042", purpose="Nightly suite",
                                 due=date.today() + timedelta(days=1)) as device:
            run_the_suite_against(device.wan_ip)
        ```

        The purpose and return date are the ones every checkout needs. For a
        block that gives the device back at the end, a date a day or two out is
        the honest answer: it is what the fleet should show if the block never
        reaches its release.

        The release runs even if the block raises, which is the entire point:
        a crashed run that strands hardware as `checked_out` costs someone an
        afternoon of wondering who has it.

        If the block leaves the device in some other state — `broken`, say,
        because that is what the run discovered — the release leaves it there.
        """
        device = self.check_out(identifier, purpose=purpose, due=due, force=force)
        try:
            yield device
        finally:
            try:
                self.check_in(device.unique_id)
            except Exception as exc:  # noqa: BLE001
                # Never let cleanup replace the real exception from the block.
                logger.error("Could not check %s back in: %s", device.unique_id, exc)

    # -- errors -----------------------------------------------------------

    def _unknown(self, key: str, exc: NotFound) -> UnknownDevice:
        """A 404 plus near misses, so a typo is obvious from a CI log alone."""
        suggestions: list[str] = []
        try:
            rows = self._dm.request(
                "GET", "/devices", params={"search": key, "page_size": 5}
            )
            suggestions = [r.get("unique_id", "") for r in rows.get("items", [])]
        except Exception:  # noqa: BLE001 — the suggestion is a nicety, not the error
            pass
        message = f"No device '{key}'."
        if suggestions:
            message += f" Did you mean: {', '.join(suggestions)}?"
        return UnknownDevice(message, suggestions=suggestions, status=exc.status,
                             detail=exc.detail, url=exc.url)


def _key(identifier: str | Device) -> str:
    if isinstance(identifier, Device):
        return identifier.unique_id or identifier.id
    return str(identifier)


def _check_status(status: str) -> None:
    # Checked here rather than left to the API: `?status=braken` on a *filter*
    # is not rejected, it returns an empty page, which reads as "there are no
    # broken devices" — a wrong answer is worse than an error.
    if status not in DEVICE_STATUSES:
        raise ValueError(f"status must be one of {', '.join(DEVICE_STATUSES)}, got '{status}'")
