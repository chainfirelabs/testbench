"""Exceptions raised by this library.

The REST API returns good, human-readable `detail` strings — the same ones the
web app puts in front of a person — and they frequently say exactly what to do
next ("Device with unique_id 'dev-0042' already exists"). Those are passed
through as the exception message rather than replaced with a generic one, and
kept on `.detail` for code that wants to match on them. `.status` carries the
HTTP status, or None for the failures that never got that far.
"""

from __future__ import annotations


class TestBenchError(Exception):
    """Base for everything this library raises.

    Catching this catches every failure the client can produce, including the
    ones that never reached the API.
    """

    # Every subclass name starts with "Test" too; keep pytest from collecting
    # them as test classes.
    __test__ = False

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = message if detail is None else detail
        self.url = url


class TransportError(TestBenchError):
    """The request never reached the API, or no response came back.

    Not named ConnectionError: that is a builtin, and shadowing it in a library
    people will `from ... import *` is a trap.
    """


class AuthenticationError(TestBenchError):
    """401 — the API key is missing, malformed, expired or revoked.

    Never retried. A key does not become valid again on its own; something has
    to mint a new one.
    """


class PermissionDenied(TestBenchError):
    """403 — authenticated, but the key's role is too low for this call.

    A key's role is the lower of the role it was minted with and its owner's
    current role, so this can appear without the key itself changing.
    """


class NotFound(TestBenchError):
    """404 — no such device, test or software."""


class Conflict(TestBenchError):
    """409 — the request collided with the current state."""


class ValidationError(TestBenchError):
    """400 or 422 — the request body or query was rejected."""


class ServerError(TestBenchError):
    """5xx — the API failed internally."""


class UnknownDevice(NotFound):
    """No device matched the identifier given.

    `suggestions` holds near misses from a search on the same string, which is
    usually enough to spot a typo from a CI log alone.
    """

    def __init__(self, message: str, *, suggestions: list[str] | None = None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.suggestions = suggestions or []


class UnknownSoftware(NotFound):
    """No software matched the name (or the name matched, but not that version)."""

    def __init__(self, message: str, *, suggestions: list[str] | None = None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.suggestions = suggestions or []


class DeviceUnavailable(Conflict):
    """The device cannot be checked out: someone else holds it, or it is not
    available (broken, missing, in inventory).

    Raised by this library rather than returned by the API — `PATCH /devices`
    accepts a redundant status change silently — so it carries no HTTP status.
    `device` is the record as it was when the attempt was refused.
    """

    def __init__(self, message: str, *, device=None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.device = device
