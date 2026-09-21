"""Streamed exports, which must produce exactly what the buffered ones did.

Streaming was tried once before and reverted — Starlette iterates a sync file
object by line, so a pretty-printed export became one threadpool hop per line.
The chunked writers here are the response to that, and these tests pin the two
things that matter: the bytes are unchanged, and the chunking is invisible.
"""

import json
import unittest

from app.services.io import (
    STREAM_CHUNK_ROWS,
    _stream_csv,
    _stream_json,
    to_csv_rows,
)

COLUMNS = ["make", "model", "notes", "misc_data"]

CASES = {
    "empty": [],
    "one row": [{"make": "Cisco", "model": "ISR", "notes": None, "misc_data": {}}],
    "containers and nulls": [
        {"make": "Cisco", "model": None, "notes": "line", "misc_data": {"a": [1, 2]}},
        {"make": None, "model": "R640", "notes": "", "misc_data": {}},
    ],
    # A spreadsheet would run these; the writer defuses them either way.
    "formula cells": [{"make": "=cmd|'/c calc'!A0", "model": "+1", "notes": "@x", "misc_data": {}}],
    "exactly one chunk": [
        {"make": f"m{i}", "model": f"d{i}", "notes": None, "misc_data": {}}
        for i in range(STREAM_CHUNK_ROWS)
    ],
    "spans chunks": [
        {"make": f"m{i}", "model": f"d{i}", "notes": None, "misc_data": {"i": i}}
        for i in range(STREAM_CHUNK_ROWS * 2 + 7)
    ],
}


class StreamedExportsMatchBufferedTests(unittest.TestCase):
    def test_csv_is_byte_identical(self):
        for name, rows in CASES.items():
            with self.subTest(name):
                self.assertEqual("".join(_stream_csv(rows, COLUMNS)), to_csv_rows(rows, COLUMNS))

    def test_json_is_byte_identical(self):
        """Including the indentation: a file should not change shape because
        the endpoint behind it started streaming."""
        for name, rows in CASES.items():
            with self.subTest(name):
                self.assertEqual("".join(_stream_json(rows)), json.dumps(rows, indent=2))

    def test_an_empty_json_export_is_an_empty_array(self):
        """Not an opening bracket with nothing after it."""
        self.assertEqual("".join(_stream_json([])), "[]")
        self.assertEqual(json.loads("".join(_stream_json([]))), [])

    def test_json_stays_parseable_across_chunk_boundaries(self):
        rows = CASES["spans chunks"]
        self.assertEqual(json.loads("".join(_stream_json(rows))), rows)

    def test_it_yields_more_than_one_chunk_for_a_large_export(self):
        """Otherwise these tests would pass over a writer that never streams."""
        rows = CASES["spans chunks"]
        self.assertGreater(len(list(_stream_json(rows))), 1)
        self.assertGreater(len(list(_stream_csv(rows, COLUMNS))), 1)

    def test_rows_are_consumed_lazily(self):
        """The caller may hand over a generator, which is the point: nothing
        should force the whole row set into a list first."""
        produced = []

        def rows():
            for i in range(STREAM_CHUNK_ROWS + 5):
                produced.append(i)
                yield {"make": f"m{i}", "model": "", "notes": None, "misc_data": {}}

        stream = _stream_csv(rows(), COLUMNS)
        next(stream)
        # The first chunk is out while the tail of the input is still unread.
        self.assertLess(len(produced), STREAM_CHUNK_ROWS + 5)
        list(stream)
        self.assertEqual(len(produced), STREAM_CHUNK_ROWS + 5)
