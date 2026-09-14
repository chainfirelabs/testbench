import csv
import io
import json
import re
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

# Cells a spreadsheet would evaluate rather than display. A device named
# `=cmd|'/c calc'!A0` is a formula to Excel and Sheets, and our exports are
# meant to be opened in exactly those. See _defuse/_rearm for the round trip.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

# The most an import may carry, in bytes. Rows are parsed into memory before
# anything is written, so an unbounded upload is an unbounded allocation.
MAX_IMPORT_BYTES = 16 * 1024 * 1024

_UNSAFE_FILENAME = re.compile(r'[^A-Za-z0-9._-]+')


def _defuse(value: str) -> str:
    """Neutralise a cell a spreadsheet would run as a formula.

    Prefixing with an apostrophe is the standard defence: Excel and Sheets
    both read the rest as literal text. `_rearm` takes it back off on import,
    so an exported file still round-trips to the values it came from.
    """
    return "'" + value if value[:1] in _FORMULA_LEAD else value


def _rearm(value: str) -> str:
    """Undo `_defuse` so export -> edit -> import returns the original value."""
    return value[1:] if value[:1] == "'" and value[1:2] in _FORMULA_LEAD else value


def _defuse_header(value: str) -> str:
    # Escape leading apostrophes too, making formula headers and literal
    # apostrophe-prefixed keys distinct and reversible on import.
    return "'" + value if value.startswith("'") or value[:1] in _FORMULA_LEAD else value


def _rearm_header(value: str) -> str:
    return value[1:] if value.startswith("''") else _rearm(value)


def safe_filename(stem: str, suffix: str) -> str:
    """A download name that cannot break out of the Content-Disposition header.

    Names come from user data (software is named by whoever created it), and a
    quote or newline in a header value is a response-splitting primitive, so
    the stem is reduced to characters that need no quoting at all.
    """
    cleaned = _UNSAFE_FILENAME.sub("-", stem).strip("-.") or "export"
    return f"{cleaned[:100]}.{suffix}"


def to_csv_rows(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writerow({column: _defuse_header(column) for column in columns})
    for row in rows:
        flat = {}
        for col in columns:
            val = row.get(col)
            if isinstance(val, (dict, list)):
                val = json.dumps(val)
            flat[col] = _defuse(val) if isinstance(val, str) else val
        writer.writerow(flat)
    return buf.getvalue()


def parse_csv(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is not None:
        reader.fieldnames = [_rearm_header(key) for key in reader.fieldnames]
    rows = []
    for row in reader:
        parsed = {}
        for k, v in row.items():
            if k is None:
                continue
            if v is None or v == "":
                parsed[k] = None
            elif v[:1] in ("{", "["):
                # Only JSONB columns are written as JSON by the exporter. Bare
                # scalars stay strings: json.loads would turn a firmware version
                # like "17.9" into a float and fail validation on re-import.
                try:
                    parsed[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    parsed[k] = v
            else:
                parsed[k] = _rearm(v)
        rows.append(parsed)
    return rows


def parse_import(content: bytes, filename: str) -> list[dict[str, Any]]:
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Import is too large ({len(content)} bytes); the limit is {MAX_IMPORT_BYTES}",
        )
    if filename.lower().endswith(".csv"):
        return parse_csv(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="JSON import must be an array of objects")
    return data


def strip_nulls(row: dict[str, Any]) -> dict[str, Any]:
    """Drop blank cells so schema defaults apply.

    Import files are filled in from a template, so most cells are empty. An
    empty cell reads as None, which fails validation for a field that is not
    nullable (`misc_data` wants a dict, not None). Leaving the key out instead
    lets the default stand. Nothing is lost: the import handlers already treat
    a missing field and a null field alike.
    """
    return {k: v for k, v in row.items() if v is not None}


def template_csv(columns: list[str]) -> str:
    """A header-only CSV: exactly the columns an import accepts."""
    buf = io.StringIO()
    csv.writer(buf).writerow([_defuse_header(column) for column in columns])
    return buf.getvalue()


def download_response(content: str, filename: str, media_type: str) -> Response:
    """Send an already-rendered export as one response body.

    Deliberately not a StreamingResponse over a BytesIO: Starlette iterates a
    sync file object *by line* through the threadpool, so a pretty-printed JSON
    export turned into one threadpool hop per line — 1.4s to ship 350KB that
    took 45ms to produce. The content is fully in memory either way, so there
    is nothing to stream.
    """
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def export_response(rows: list[dict[str, Any]], columns: list[str], fmt: str, stem: str) -> Response:
    """Render an export as CSV or JSON. `fmt` is whatever the caller asked for;
    anything but "csv" means JSON, which is the documented default."""
    if fmt == "csv":
        return download_response(to_csv_rows(rows, columns), safe_filename(stem, "csv"), "text/csv")
    return download_response(
        json.dumps(rows, indent=2), safe_filename(stem, "json"), "application/json"
    )
