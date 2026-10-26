"""Everything recorded in one test run, gathered for the AI Test Summary plugin.

The plugin runs in its own container and cannot reach object storage, so the
backend reads the run on its behalf: every test with all of its fields, each
tested device once (with no sensitive values, as in a tests export), each test's
output, and the text of every artifact — the test's own and the run's shared
files. Binary files are listed by name, type and size only.

Text is bounded per file and across the whole report, and the interesting tests
are read first — failures, then warnings, then passes — so a large run loses
the text of its passing tests before anything that explains a failure.
"""

from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Test, TestArtifact, TestOutput, TestRun, TestRunArtifact
from ..services.entity_fields import TEST_FILE_FIELDS, get_entity_fields, project_fields
from ..services.object_storage import OutputError
from . import test_artifact_storage as storage

# Reports the plugin wrote earlier are shared files too; summarising them again
# would make every new report a summary of the last one.
SUMMARY_FILE_PREFIX = "ai-summary-"
FILE_CHARS = 20_000
TOTAL_CHARS = 1_500_000
TEXT_TYPES = {
    "application/json", "application/xml", "application/x-yaml", "application/yaml",
    "application/x-ndjson", "application/csv", "application/javascript", "application/x-sh",
}
TEXT_SUFFIXES = {
    ".txt", ".log", ".json", ".xml", ".yaml", ".yml", ".csv", ".tsv", ".md", ".html", ".htm",
    ".ini", ".conf", ".cfg", ".out", ".tap", ".junit", ".ndjson", ".sh", ".py", ".toml",
}
OUTCOME_ORDER = {"fail": 0, "warn": 1, "pass": 2}


def is_summary_file(filename: str) -> bool:
    return filename.startswith(SUMMARY_FILE_PREFIX)


def is_text(filename: str, content_type: str | None) -> bool:
    kind = (content_type or "").split(";")[0].strip().lower()
    return (kind.startswith("text/") or kind in TEXT_TYPES
            or PurePosixPath(filename.lower()).suffix in TEXT_SUFFIXES)


class _Budget:
    """What is left of the report's text allowance."""

    def __init__(self, total: int):
        self.left = total
        self.omitted = 0

    def take(self, wanted: int) -> int:
        granted = max(0, min(wanted, self.left))
        self.left -= granted
        return granted


def _read_text(bucket: str, key: str, limit: int) -> tuple[str, bool]:
    """At most `limit` characters of an object, and whether there was more."""
    body = storage.download_artifact(bucket, key)["Body"]
    try:
        # Never read more than one byte past what is wanted: files can be large.
        data = body.read(limit + 1)
    finally:
        body.close()
    text = data[:limit].decode("utf-8", errors="replace")
    return text, len(data) > limit


def _file(filename: str, content_type: str | None, size: int, bucket: str, key: str,
          budget: _Budget) -> dict:
    entry = {"filename": filename, "content_type": content_type, "size_bytes": size}
    if not is_text(filename, content_type):
        entry["note"] = "binary file; contents not read"
        return entry
    if not settings.object_storage_enabled:
        entry["note"] = "object storage is disabled; contents not read"
        return entry
    allowance = budget.take(min(FILE_CHARS, size))
    if allowance <= 0:
        budget.omitted += 1
        entry["note"] = "report size limit reached; contents not read"
        return entry
    try:
        text, truncated = _read_text(bucket, key, allowance)
    except OutputError as exc:
        budget.left += allowance
        entry["note"] = f"could not be read: {exc}"
        return entry
    budget.left += allowance - len(text)
    entry["text"] = text
    entry["truncated"] = truncated
    return entry


def build_report(db: Session, run: TestRun) -> dict:
    """The run, its tests and devices, and the text of its outputs and files."""
    from ..api.devices import _union_fields
    from ..api.tests import _device_export_columns, _device_projector, _test_dict

    budget = _Budget(TOTAL_CHARS)
    shared = [
        _file(item.filename, item.content_type, item.size_bytes, item.bucket, item.object_key, budget)
        for item in db.scalars(select(TestRunArtifact).where(TestRunArtifact.test_run_id == run.id)
                               .order_by(TestRunArtifact.created_at, TestRunArtifact.id))
        if not is_summary_file(item.filename)
    ]

    tests = list(db.scalars(select(Test).where(Test.test_run_id == run.id)
                            .order_by(Test.created_at, Test.id)))
    tests.sort(key=lambda item: OUTCOME_ORDER.get(item.outcome, 3))
    ids = [item.id for item in tests]
    outputs = {item.test_id: item for item in db.scalars(
        select(TestOutput).where(TestOutput.test_id.in_(ids)))} if ids else {}
    artifacts: dict[str, list[TestArtifact]] = {}
    for item in db.scalars(select(TestArtifact).where(TestArtifact.test_id.in_(ids))
                           .order_by(TestArtifact.created_at, TestArtifact.id)) if ids else []:
        artifacts.setdefault(item.test_id, []).append(item)

    fields = [field for field in get_entity_fields(db, "tests") if field.key not in TEST_FILE_FIELDS]
    pairs = _device_export_columns(db)
    add_device = _device_projector(db, "json", pairs, [])
    devices: dict[str, dict] = {}
    rows = []
    for item in tests:
        full = _test_dict(item)
        row = project_fields(full, fields, "misc_data")
        row.pop("misc_data", None)
        # Values recorded outside the configured fields are results too.
        extra = {key: value for key, value in (full.get("misc_data") or {}).items() if key not in row}
        row = {"id": item.id, "device_id": item.device_id, **row}
        if extra:
            row["other_values"] = extra
        if item.device_id not in devices and item.device is not None:
            devices[item.device_id] = add_device({}, item.device)["device"]
        output = outputs.get(item.id)
        row["output"] = _file(output.filename, "text/plain", output.size_bytes, output.bucket,
                              output.object_key, budget) if output else None
        row["artifacts"] = [
            _file(file.filename, file.content_type, file.size_bytes, file.bucket, file.object_key, budget)
            for file in artifacts.get(item.id, [])
        ]
        rows.append(row)

    return {
        "run": {
            "id": run.id, "name": run.name, "external_id": run.external_id, "url": run.url,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "notes": run.notes, "created_by_username": run.created_by_username,
        },
        "test_fields": [{"key": field.key, "label": field.label} for field in fields],
        "device_fields": [
            {"key": field.key, "label": field.label}
            for field in _union_fields(db) if not field.sensitive
        ],
        "devices": devices,
        "tests": rows,
        "shared_files": shared,
        "limits": {"file_chars": FILE_CHARS, "total_chars": TOTAL_CHARS,
                   "files_not_read_for_size": budget.omitted},
    }
