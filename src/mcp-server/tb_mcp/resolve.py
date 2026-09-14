"""Name resolution (MCP_SERVER.md §6).

People say "nmap" and "dev-0042", never
`01a03937-0ba1-71cd-9013-12c86abc0375`. Every tool takes human identifiers and
this module turns them into records. A model shown a UUID-shaped field will
cheerfully invent one, so UUIDs stay inside the server.

Three rules, all of which exist because the failure they prevent is silent:

* no match  -> near misses, not an error string
* many matches -> the candidates, and stop; never guess
* version ambiguity -> the current version, and *say so in the response*
"""

from __future__ import annotations

import difflib
from urllib.parse import quote

from .client import ApiClient, NotFound
from .format import no_match

SUGGESTION_LIMIT = 5
# How many names a miss may pull to fuzzy-match against. Only ever paid on the
# miss path, and both lists are small in this system.
SUGGESTION_POOL = 1000


async def _suggest(client: ApiClient, path: str, key: str, term: str, params: dict) -> list[str]:
    """Near misses for a name that did not resolve.

    Two passes. The API's `search` is a substring match, which catches a partial
    name ("forti" -> "fortigate-audit") but not a typo — "nmapp" contains "nmap"
    and so matches nothing. When substring search comes back empty, fall back to
    an edit-distance match over the names, which is what catches the typo.
    """
    page = await client.get(path, params={**params, "search": term, "page_size": SUGGESTION_LIMIT})
    hits = [row[key] for row in page.get("items", [])]
    if hits:
        return hits
    page = await client.get(path, params={**params, "page_size": SUGGESTION_POOL})
    names = [row[key] for row in page.get("items", [])]
    return difflib.get_close_matches(term, names, n=SUGGESTION_LIMIT, cutoff=0.6)


class Unresolved(Exception):
    """Carries a ready-to-return "no match" payload (see `format.no_match`)."""

    def __init__(self, payload: dict) -> None:
        super().__init__(payload.get("message", "not found"))
        self.payload = payload


async def resolve_device(client: ApiClient, identifier: str) -> dict:
    """A device by `unique_id` or UUID.

    Returns the full `DeviceDetail` (which carries the device's test history) —
    the API has no lighter single-device read, and the rows never leave this
    process except through a projection.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        raise Unresolved(no_match("device", identifier, []))
    try:
        if "/" in identifier or identifier in {".", ".."}:
            return await client.get("/devices/lookup/by-unique-id", params={"unique_id": identifier})
        return await client.get(f"/devices/{quote(identifier, safe='')}")
    except NotFound:
        hits = await _suggest(client, "/devices", "unique_id", identifier, {"sort": "unique_id"})
        raise Unresolved(no_match("device", identifier, hits)) from None


async def resolve_software(client: ApiClient, identifier: str, version: str | None = None) -> dict:
    """Software by name (case-insensitive) or UUID, optionally pinned to a version.

    A bare name addresses the *current* version, matching both the API's own
    resolution and the web UI's `/software/:name` route. `version` selects a
    specific one from the name's version group.
    """
    identifier = (identifier or "").strip()
    if not identifier:
        raise Unresolved(no_match("software", identifier, []))
    try:
        if "/" in identifier or identifier in {".", ".."}:
            software = await client.get("/software/lookup/by-name", params={"name": identifier})
        else:
            software = await client.get(f"/software/{quote(identifier, safe='')}")
    except NotFound:
        hits = await _suggest(client, "/software", "name", identifier, {"latest_only": True})
        raise Unresolved(no_match("software", identifier, hits)) from None

    if version is None:
        return software

    versions = await client.get(f"/software/{quote(software['id'], safe='')}/versions")
    wanted = version.strip()
    for candidate in versions:
        if (candidate.get("version") or "").lower() == wanted.lower():
            return candidate
    available = [v.get("version") or "(unversioned)" for v in versions]
    raise Unresolved(
        no_match(f"version of '{software['name']}'", wanted, available)
    )


def describe_software(software: dict) -> str:
    """What the server resolved to, echoed back so the model can self-correct.

    Silently picking a version and not mentioning it is how a model ends up
    reporting evidence from the wrong build.
    """
    name = software.get("name", "?")
    version = software.get("version") or "unversioned"
    count = software.get("version_count") or 1
    if count > 1:
        which = "current version" if software.get("is_latest") else "not the current version"
        return f"{name} ({version}, {which} — {count} versions exist)"
    return f"{name} ({version})"


def describe_device(device: dict) -> str:
    bits = [b for b in (device.get("make"), device.get("model")) if b]
    if device.get("location"):
        bits.append(device["location"])
    return f"{device.get('unique_id', '?')}" + (f" ({', '.join(bits)})" if bits else "")


async def fetch_pages(
    client: ApiClient, path: str, params: dict, max_rows: int, page_size: int = 200
) -> list[dict]:
    """Walk a paginated list endpoint up to `max_rows`.

    Only used where the API cannot express a filter (`find_tests` date ranges —
    see §9). Rows pulled here are filtered and projected in this process; they
    never reach the model's context.
    """
    rows: list[dict] = []
    page = 1
    while len(rows) < max_rows:
        body = await client.get(path, params={**params, "page": page, "page_size": page_size})
        items = body.get("items", [])
        rows.extend(items)
        if len(items) < page_size or len(rows) >= (body.get("total") or 0):
            break
        page += 1
    return rows[:max_rows]
