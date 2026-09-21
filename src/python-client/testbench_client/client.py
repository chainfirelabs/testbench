"""The client: connection, authentication, retries and error mapping.

Everything else in this package is a thin resource wrapper over `request()`.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from typing import Any, Iterator
from urllib.parse import quote

import httpx

from . import __version__
from .errors import (
    AuthenticationError,
    Conflict,
    TestBenchError,
    NotFound,
    PermissionDenied,
    ServerError,
    TransportError,
    ValidationError,
)
from .models import DeviceType, Identity

logger = logging.getLogger("testbench_client")

DEFAULT_BASE_URL = "http://localhost:8001/api/v1"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
# The API caps page_size at 1000. 200 keeps a full-fleet listing to a couple of
# requests without asking Postgres for an enormous page.
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000

ENV_URL = "TB_API_URL"
ENV_KEY = "TB_API_KEY"

_VERSIONED = re.compile(r"/api/v\d+/?$")


def normalise_base_url(url: str) -> str:
    """Accept either the host root or the full versioned prefix.

    `http://tb.example.com` and `http://tb.example.com/api/v1` both work;
    forgetting the prefix otherwise produces 404s from a server that is up,
    which is a confusing first five minutes.
    """
    url = url.strip().rstrip("/")
    if not url:
        raise ValueError("base_url must not be empty")
    return url if _VERSIONED.search(url + "/") else url + "/api/v1"


class TestBench:
    """A connection to a TestBench instance.

    ```python
    from testbench_client import TestBench

    with TestBench.from_env() as tb:
        for device in tb.devices.list(status="available"):
            print(device)
    ```

    Safe to share between threads: `httpx.Client` is thread-safe and the name
    resolution cache is locked. Prefer one client per process — each holds a
    connection pool, and reusing it is most of why this is faster than curl.
    """

    # The class name starts with "Test", so pytest tries to collect it as a
    # test class in any module that imports it. It is not one.
    __test__ = False

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        verify: bool | str = True,
        page_size: int = DEFAULT_PAGE_SIZE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                f"An API key is required. Mint one in TestBench under "
                f"Profile -> API keys, then pass api_key= or set {ENV_KEY}."
            )
        if not _looks_like_api_key(api_key):
            # A login JWT is also a valid bearer token, so this is a warning and
            # not a refusal — but it expires in hours and this library has no
            # way to refresh it, which is rarely what someone intended.
            logger.warning(
                "The configured credential does not look like a TestBench "
                "API key (tb_<prefix>_<secret>); using it as a bearer token."
            )
        self.base_url = normalise_base_url(base_url or DEFAULT_BASE_URL)
        self.page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        self._retries = max(0, retries)
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            verify=verify,
            transport=transport,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"testbench-client/{__version__}",
                "Accept": "application/json",
            },
        )
        self._identity: Identity | None = None
        self._cache: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

        from .devices import Devices
        from .software import SoftwareResource
        from .tests import Tests
        from .vendor_devices import VendorDevices

        self.devices = Devices(self)
        self.tests = Tests(self)
        self.software = SoftwareResource(self)
        self.vendor_devices = VendorDevices(self)

    def entity_fields(self) -> dict[str, list[dict]]:
        """Logical device/software/test fields this installation exposes.

        The device entry is the *global* device schema — the fields every
        device type inherits. For one type's own fields, use
        `device_schema(type_key)`.
        """
        return self.request("GET", "/entity-fields")

    def device_types(self, *, include_disabled: bool = False) -> list[DeviceType]:
        """The inventory categories this installation defines."""
        params = {"include_disabled": "true"} if include_disabled else None
        return [DeviceType.from_dict(row) for row in self.request("GET", "/device-types", params=params)]

    def device_schema(self, type_key: str | None = None) -> dict:
        """The published schema for one device type, or the global one.

        What a device of this type looks like here: which fields it carries, in
        what order, which are required, and which plugins may act on it. Field
        keys are stable and are what every other call in this library uses;
        labels are presentation only.
        """
        path = f"/device-schema/types/{quote(type_key, safe='')}" if type_key else "/device-schema/global"
        return self.request("GET", path)

    def device_schema_revision(self) -> int:
        """The published revision, for a caller that caches schemas."""
        return int(self.request("GET", "/device-schema/global").get("revision", 0))

    @classmethod
    def from_env(cls, **overrides: Any) -> "TestBench":
        """Build a client from `TB_API_URL` and `TB_API_KEY`.

        The usual shape for CI, where the key is an injected secret and nothing
        should be reading it out of a checked-in config file.
        """
        base_url = overrides.pop("base_url", None) or os.environ.get(ENV_URL) or DEFAULT_BASE_URL
        api_key = overrides.pop("api_key", None) or os.environ.get(ENV_KEY, "")
        if not api_key:
            raise ValueError(
                f"{ENV_KEY} is not set. Mint an API key in TestBench "
                f"under Profile -> API keys and export it as {ENV_KEY}."
            )
        return cls(base_url, api_key, **overrides)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TestBench":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        who = self._identity.username if self._identity else "?"
        return f"<TestBench {self.base_url} as {who}>"

    # -- identity ---------------------------------------------------------

    def whoami(self, *, refresh: bool = False) -> Identity:
        """The account and effective role this key resolves to.

        Worth calling once at the start of a run: it turns a revoked key into
        one clear failure at setup instead of a confusing one an hour into a
        suite. The result is cached; pass refresh=True to re-check.
        """
        if self._identity is None or refresh:
            self._identity = Identity.from_dict(self.request("GET", "/auth/me"))
        return self._identity

    @property
    def identity(self) -> Identity:
        return self.whoami()

    # -- requests ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
    ) -> Any:
        """Make one API call and return parsed JSON (None for 204).

        Retries idempotent requests through transport failures and 5xx; a
        response the API meant to send is never retried, because a 403 does not
        become a 200 by asking again.
        """
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        idempotent = method.upper() in ("GET", "HEAD")
        attempts = self._retries + 1 if idempotent else 1
        last: Exception | None = None

        for attempt in range(attempts):
            if attempt:
                self._backoff(attempt)
            try:
                response = self._http.request(method, path, params=clean, json=json)
            except httpx.HTTPError as exc:
                last = TransportError(
                    f"Could not reach the TestBench API at {self.base_url}: {exc}",
                    url=path,
                )
                logger.debug("%s %s failed (attempt %d/%d): %s", method, path, attempt + 1, attempts, exc)
                continue

            if response.is_success:
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()

            error = _to_error(response, path)
            if isinstance(error, ServerError) and attempt < attempts - 1:
                last = error
                continue
            raise error

        raise last if last else TestBenchError(f"{method} {path} failed")

    def _backoff(self, attempt: int) -> None:
        # Jittered, because a framework running devices in parallel would
        # otherwise line every worker up to retry on the same tick.
        delay = min(0.5 * 2 ** (attempt - 1), 5.0)
        time.sleep(delay * (0.5 + random.random() / 2))

    def paginate(
        self,
        path: str,
        params: dict | None = None,
        *,
        limit: int | None = None,
        page_size: int | None = None,
    ) -> Iterator[dict]:
        """Yield raw rows across pages, stopping at `limit` if given.

        Rows already seen are skipped by id: the API pages with OFFSET, so a row
        inserted during a long listing shifts everything after it down a slot
        and would otherwise be yielded twice.
        """
        if limit is not None and limit <= 0:
            return
        size = max(1, min(page_size or self.page_size, MAX_PAGE_SIZE))
        if limit is not None:
            size = min(size, max(1, limit))
        page = 1
        seen: set[str] = set()
        yielded = 0
        while True:
            body = self.request(
                "GET", path, params={**(params or {}), "page": page, "page_size": size}
            )
            items = body.get("items", [])
            for row in items:
                key = row.get("id")
                if key is not None and key in seen:
                    continue
                if key is not None:
                    seen.add(key)
                yield row
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if len(items) < size or yielded >= body.get("total", 0):
                return
            page += 1

    # -- name resolution --------------------------------------------------

    def cache_get(self, kind: str, key: str) -> str | None:
        with self._lock:
            return self._cache.get((kind, key))

    def cache_put(self, kind: str, key: str, value: str) -> None:
        with self._lock:
            self._cache[(kind, key)] = value

    def clear_cache(self) -> None:
        """Forget resolved names.

        Call this if software gained a new version mid-run and you want bare
        names to start meaning the new one. See `software.resolve()` for why
        that is otherwise sticky on purpose.
        """
        with self._lock:
            self._cache.clear()


def _looks_like_api_key(value: str) -> bool:
    parts = value.split("_", 2)
    # "dm" is the pre-TestBench prefix, still carried by keys minted then.
    return len(parts) == 3 and parts[0] in ("tb", "dm") and all(parts)


def _detail(response: httpx.Response) -> str:
    """The API's `detail` string, or something honest if the body is not JSON."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:300] or f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return str(body)
    detail = body.get("detail", body)
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        # 422 bodies are a list of pydantic errors; one line each.
        return "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', e)}"
            if isinstance(e, dict) else str(e)
            for e in detail
        )
    return str(detail)


def _to_error(response: httpx.Response, path: str) -> TestBenchError:
    status = response.status_code
    detail = _detail(response)
    kwargs = {"status": status, "detail": detail, "url": path}

    if status == 401:
        return AuthenticationError(
            f"The TestBench API rejected the API key: {detail}. It may have "
            f"been revoked or expired — mint a replacement under Profile -> API keys.",
            **kwargs,
        )
    if status == 403:
        return PermissionDenied(
            f"Permission denied: {detail}. A key grants what its role and its "
            f"owner's role both grant, so it is never more than its owner has — "
            f"check `tb.whoami().permissions`.",
            **kwargs,
        )
    if status == 404:
        return NotFound(detail, **kwargs)
    if status == 409:
        return Conflict(detail, **kwargs)
    if status in (400, 422):
        return ValidationError(detail, **kwargs)
    if status >= 500:
        return ServerError(
            f"The TestBench API returned an internal error ({status}): {detail}",
            **kwargs,
        )
    return TestBenchError(detail, **kwargs)
