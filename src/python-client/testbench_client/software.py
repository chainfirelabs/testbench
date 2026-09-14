"""Software: lookup and version resolution.

Read-only on purpose. Recording a result needs software that already exists,
and a test framework inventing rows in the catalogue is how a catalogue fills
up with typos. Create software in the web app; if you do need it here, it is
`POST /software` and a sibling of the methods below.
"""

from __future__ import annotations

from typing import Iterator
from urllib.parse import quote

from .errors import NotFound, UnknownSoftware
from .models import Software


class SoftwareResource:
    """Accessed as `tb.software`.

    Named `SoftwareResource` rather than `Software` so it does not collide with
    the record class of that name; you never write the class name yourself.
    """

    def __init__(self, client) -> None:
        self._dm = client

    def list(
        self,
        *,
        search: str | None = None,
        latest_only: bool = False,
        fields: dict[str, str | int | float | bool] | None = None,
        limit: int | None = None,
    ) -> list[Software]:
        """Software in the catalogue. `latest_only` collapses version groups to
        the current version of each name."""
        return list(self.iter(search=search, latest_only=latest_only, fields=fields, limit=limit))

    def iter(
        self,
        *,
        search: str | None = None,
        latest_only: bool = False,
        fields: dict[str, str | int | float | bool] | None = None,
        limit: int | None = None,
    ) -> Iterator[Software]:
        params = {**(fields or {}), "search": search, "latest_only": latest_only or None}
        for row in self._dm.paginate("/software", params, limit=limit):
            yield Software.from_dict(row)

    def get(self, name: str | Software, version: str | None = None) -> Software:
        """One piece of software by name (or UUID).

        A bare name gives the CURRENT version — the highest numeric version.
        Pass `version` for a specific one.
        """
        if isinstance(name, Software) and name.id and version is None:
            return name
        key = name.name if isinstance(name, Software) else str(name)
        try:
            if "/" in key or key in {".", ".."}:
                body = self._dm.request("GET", "/software/lookup/by-name", params={"name": key})
            else:
                body = self._dm.request("GET", f"/software/{quote(key, safe='')}")
            current = Software.from_dict(body)
        except NotFound as exc:
            raise self._unknown(key, version, exc) from exc

        if version is None or current.version == version:
            return current
        for candidate in self.versions(current.id):
            if candidate.version == version:
                return candidate
        known = ", ".join(v.version or "(unversioned)" for v in self.versions(current.id))
        raise UnknownSoftware(
            f"{current.name} has no version '{version}'. Known versions: {known}.",
            suggestions=[current.name],
        )

    def versions(self, name: str | Software) -> list[Software]:
        """Every version sharing this name, newest first."""
        key = name.id if isinstance(name, Software) else str(name)
        if "/" in key or key in {".", ".."}:
            key = self.get(key).id
        rows = self._dm.request("GET", f"/software/{quote(key, safe='')}/versions")
        return [Software.from_dict(r) for r in rows]

    def resolve(self, name: str | Software, version: str | None = None) -> str:
        """The software's UUID, which `POST /tests` needs.

        Cached for the life of the client. For a bare name that means the run
        keeps recording against whichever version was current when the first
        result was written, even if someone adds a new version halfway through
        — which is what you want from a test run: one build, one set of
        evidence. Call `tb.clear_cache()` if you want the opposite.
        """
        if isinstance(name, Software) and name.id and version is None:
            return name.id
        key = name.name if isinstance(name, Software) else str(name)
        cache_key = f"{key}@{version}" if version else key
        cached = self._dm.cache_get("software", cache_key)
        if cached:
            return cached
        software = self.get(key, version)
        self._dm.cache_put("software", cache_key, software.id)
        return software.id

    def _unknown(self, key: str, version: str | None, exc: NotFound) -> UnknownSoftware:
        suggestions: list[str] = []
        try:
            rows = self._dm.request("GET", "/software", params={"search": key, "page_size": 5})
            suggestions = sorted({r.get("name", "") for r in rows.get("items", [])})
        except Exception:  # noqa: BLE001 — the suggestion is a nicety, not the error
            pass
        named = f"'{key}' version '{version}'" if version else f"'{key}'"
        message = f"No software {named}."
        if suggestions:
            message += f" Did you mean: {', '.join(suggestions)}?"
        return UnknownSoftware(message, suggestions=suggestions, status=exc.status,
                               detail=exc.detail, url=exc.url)
