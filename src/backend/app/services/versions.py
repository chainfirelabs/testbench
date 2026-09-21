"""Ordering software versions.

Lives here rather than in `api/software.py` because more than one collection
has to agree about which version of a name is the current one: the software
list says so on every row, and so does a vendor-claim search that spans every
version of every software. Two implementations would eventually disagree, and
the disagreement would show up as a row marked current on one page and not on
another.
"""

import re

_VERSION_RE = re.compile(r"^[vV]?(\d+(?:\.\d+)*)(?:[-+](.*))?$")


def natural_key(value: str) -> tuple:
    return tuple(
        (1, int(token)) if token.isdigit() else (0, token.casefold())
        for token in re.findall(r"\d+|\D+", value)
    )


def version_key(version: str) -> tuple:
    """Sort dotted numeric versions naturally, with releases above prereleases.

    The catalogue permits free-form versions, so values outside that common
    grammar fall back to a case-folded natural sort. Numeric versions always
    outrank free-form and unversioned rows.
    """
    raw = (version or "").strip()
    if not raw:
        return (0,)
    match = _VERSION_RE.fullmatch(raw)
    if match:
        release = [int(part) for part in match.group(1).split(".")]
        while len(release) > 1 and release[-1] == 0:
            release.pop()
        suffix = match.group(2)
        # A final release is newer than its prerelease (2.0 > 2.0-rc1).
        return (2, tuple(release), 1 if suffix is None else 0, natural_key(suffix or ""))
    return (1, natural_key(raw))


def newest_version(rows: list):
    """Highest version, with creation time/id only breaking equal-version ties."""
    return max(rows, key=lambda row: (version_key(row.version), row.created_at, row.id)) if rows else None
