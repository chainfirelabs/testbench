"""`list_software_tests`: every test of a version, with its devices, from the export."""
import asyncio
import json

import pytest

pytest.importorskip("mcp")

from tb_mcp import tools  # noqa: E402
from tb_mcp.client import NotFound  # noqa: E402

SOFTWARE = {"id": "sw-2", "name": "nmap", "version": "7.94"}


def export_row(n, outcome, device):
    return {
        "id": f"t{n}", "device_unique_id": device, "software_name": "nmap", "software_version": "7.94",
        "outcome": outcome, "tag": "automated", "run_at": f"2026-10-0{n}", "notes": "", "misc_data": {"ping": n},
        "test_run_name": "Nightly #1", "created_by_username": "ci",
        "device": {"unique_id": device, "device_type": "router", "make": "Cisco", "model": "ISR",
                   "serial_number": None, "misc_data": {"firmware_version": "17.3"}},
    }


ROWS = [export_row(1, "pass", "dev-a"), export_row(2, "fail", "dev-b"), export_row(3, "pass", "dev-a")]


class StubApi:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/software/nmap":
            return {"id": "sw-1", "name": "nmap", "version": "7.95"}
        if path == "/software/sw-1/versions":
            return [{"id": "sw-1", "name": "nmap", "version": "7.95"}, SOFTWARE]
        if path == "/software":
            return {"items": [], "total": 0}
        if path == "/tests/export":
            return [dict(row) for row in ROWS]
        raise NotFound("Software not found", 404)


@pytest.fixture
def api(monkeypatch):
    stub = StubApi()
    monkeypatch.setattr(tools, "api", lambda: stub)
    return stub


def run(coro):
    return asyncio.run(coro)


def test_every_test_of_the_pinned_version_comes_with_its_device(api):
    out = run(tools.list_software_tests("nmap", version="7.94"))
    path, params = api.calls[-1]
    assert path == "/tests/export"
    assert params == {"format": "json", "include_device": "true", "software_id": "sw-2", "outcome": None}
    assert out["summary"] == {"tests": 3, "devices": 2, "outcomes": {"pass": 2, "fail": 1}}
    assert out["total"] == 3 and "truncated" not in out
    newest = out["items"][0]
    assert newest["id"] == "t3" and newest["outcome"] == "pass"
    assert newest["device"] == {"unique_id": "dev-a", "device_type": "router", "make": "Cisco",
                                "model": "ISR", "firmware_version": "17.3"}
    assert newest["fields"] == {"ping": 3} and newest["test_run"] == "Nightly #1"


def test_devices_and_outcome_narrow_the_export(api):
    run(tools.list_software_tests("nmap", version="7.94", outcome="fail", devices=["dev-b"]))
    _, params = api.calls[-1]
    assert params["outcome"] == "fail"
    assert json.loads(params["include__device_unique_id"]) == ["dev-b"]


def test_a_large_version_comes_in_chunks(api):
    first = run(tools.list_software_tests("nmap", version="7.94", limit=2))
    assert [row["id"] for row in first["items"]] == ["t3", "t2"]
    assert first["truncated"] and first["next_offset"] == 2
    rest = run(tools.list_software_tests("nmap", version="7.94", limit=2, offset=first["next_offset"]))
    assert [row["id"] for row in rest["items"]] == ["t1"] and "next_offset" not in rest
    assert rest["summary"]["tests"] == 3


def test_unknown_software_or_version_is_an_answer(api):
    assert run(tools.list_software_tests("nope"))["found"] is False
    missing = run(tools.list_software_tests("nmap", version="1.0"))
    assert missing["found"] is False
