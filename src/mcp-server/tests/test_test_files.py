"""The read-only test output and artifact tools, against a stub API."""
import asyncio

import pytest

pytest.importorskip("mcp")

from tb_mcp import tools  # noqa: E402
from tb_mcp.client import NotFound  # noqa: E402
from tb_mcp.format import test_brief as brief  # noqa: E402  (not a pytest test)


class StubApi:
    def __init__(self, routes):
        self.routes = routes
        self.paths = []

    async def get(self, path, params=None):
        self.paths.append(path)
        if path not in self.routes:
            raise NotFound("Test not found", 404)
        return self.routes[path]


@pytest.fixture
def stub(monkeypatch):
    def install(routes):
        api = StubApi(routes)
        monkeypatch.setattr(tools, "api", lambda: api)
        return api
    return install


def run(coro):
    return asyncio.run(coro)


def test_output_text_is_returned_with_its_name(stub):
    api = stub({"/tests/t%2F1/output": {
        "output": {"filename": "run.log", "size_bytes": 6}, "text": "hello\n", "error": None}})
    out = run(tools.get_test_output("t/1"))
    assert out == {"test_id": "t/1", "has_output": True, "filename": "run.log",
                   "size_bytes": 6, "text": "hello\n"}
    assert api.paths == ["/tests/t%2F1/output"]


def test_long_output_is_cut_from_the_requested_end(stub, monkeypatch):
    monkeypatch.setattr(tools.settings, "max_response_bytes", 4096)
    text = "a" * 3000 + "b" * 3000
    stub({"/tests/t1/output": {"output": {"filename": "x", "size_bytes": 6000}, "text": text}})
    head = run(tools.get_test_output("t1"))
    tail = run(tools.get_test_output("t1", tail=True))
    assert head["truncated"] and set(head["text"]) == {"a"}
    assert set(tail["text"]) == {"b"} and "tail=false" in tail["note"]
    assert len(head["text"]) == len(tail["text"]) == 2048


def test_missing_and_unreadable_output_are_reported_not_raised(stub):
    stub({"/tests/t1/output": {"output": None, "text": None},
          "/tests/t2/output": {"output": {"filename": "x", "size_bytes": 1}, "text": None,
                               "error": "Output file was not found"}})
    assert run(tools.get_test_output("t1")) == {"test_id": "t1", "has_output": False}
    assert run(tools.get_test_output("t2"))["error"] == "Output file was not found"
    assert run(tools.get_test_output("nope"))["found"] is False


def test_artifacts_are_listed_without_storage_details(stub):
    stub({"/tests/t1/artifacts": {"items": [
        {"id": "a1", "filename": "report.zip", "content_type": "application/zip",
         "size_bytes": 3, "created_at": "2026-10-01T12:00:00"}], "storage_enabled": True}})
    out = run(tools.list_test_artifacts("t1"))
    assert out["items"] == [{"filename": "report.zip", "content_type": "application/zip",
                             "size_bytes": 3, "uploaded": "2026-10-01"}]
    assert out["total"] == 1
    assert run(tools.list_test_artifacts("nope"))["found"] is False


def test_test_rows_carry_their_id_and_attachments():
    row = brief({"id": "t1", "device_unique_id": "dev-1", "outcome": "pass",
                      "artifact_count": 2, "output_filename": "run.log"})
    assert row["id"] == "t1" and row["artifacts"] == 2 and row["output"] == "run.log"
    assert "artifacts" not in brief({"id": "t2", "artifact_count": 0})
