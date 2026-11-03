"""The test-run tools and `read_artifact`, against a stub API."""
import asyncio

import pytest

pytest.importorskip("mcp")

from tb_mcp import tools  # noqa: E402
from tb_mcp.client import NotFound  # noqa: E402

RUN = {"id": "r" * 36, "name": "Nightly", "external_id": "pipeline-77", "url": "https://ci.example/77",
       "started_at": "2026-10-30T02:00:00", "finished_at": "2026-10-30T02:41:09", "notes": "full notes",
       "test_count": 3, "pass_count": 2, "fail_count": 1, "warn_count": 0, "artifact_count": 1,
       "created_at": "2026-10-30T02:00:00", "created_by_username": "ci"}
OLDER = {**RUN, "id": "o" * 36, "started_at": "2026-10-29T02:00:00", "fail_count": 0, "pass_count": 3}
OTHER = {**RUN, "id": "x" * 36, "name": "Nightly arm64", "external_id": None}


def make_test(n, outcome="pass", **extra):
    return {"id": f"t{n}", "device_unique_id": f"dev-{n}", "software_name": "nmap",
            "software_version": "7.94", "outcome": outcome, "test_run_id": RUN["id"],
            "test_run_name": "Nightly", "output_filename": "stdout.log", **extra}


class StubApi:
    """Routes by path; a route may be a function of the request's params."""

    def __init__(self, routes, files=None):
        self.routes = routes
        self.files = files or {}
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, {k: v for k, v in (params or {}).items() if v is not None}))
        if path not in self.routes:
            raise NotFound("Not found", 404)
        route = self.routes[path]
        return route(self.calls[-1][1]) if callable(route) else route

    async def read_bytes(self, path, *, keep, tail, max_bytes):
        data = self.files[path]
        stopped = len(data) > max_bytes
        data = data[:max_bytes]
        return (data[-keep:] if tail else data[:keep]), len(data), stopped


@pytest.fixture
def stub(monkeypatch):
    def install(routes, files=None):
        api = StubApi(routes, files)
        monkeypatch.setattr(tools, "api", lambda: api)
        return api
    return install


def run(coro):
    return asyncio.run(coro)


def runs_page(params, runs=(RUN, OLDER, OTHER)):
    term = (params.get("search") or "").lower()
    items = [r for r in runs if term in r["name"].lower() or term in (r["external_id"] or "").lower()]
    return {"items": items[: params.get("page_size", 50)], "total": len(items)}


def test_finding_the_latest_run_newest_first(stub):
    api = stub({"/test-runs": runs_page})
    out = run(tools.find_test_runs(search="nightly", limit=1))
    assert out["total"] == 3 and out["returned"] == 1
    row = out["items"][0]
    assert row["id"] == RUN["id"] and row["started"] == "2026-10-30 02:00"
    assert row["outcomes"] == {"pass": 2, "fail": 1} and row["tests"] == 3 and row["files"] == 1
    assert row["notes"] == "full notes"
    assert api.calls[0][1] == {"search": "nightly", "sort": "started_at", "order": "desc", "page_size": 1}


def test_software_means_every_version_unless_one_is_pinned(stub):
    software = {"id": "s1", "name": "nmap", "version": "7.95", "version_count": 2, "is_latest": True}
    api = stub({"/test-runs": runs_page, "/software/nmap": software,
                "/software/s1/versions": [{"id": "s0", "name": "nmap", "version": "7.94"}, software]})
    out = run(tools.find_test_runs(software="nmap"))
    assert out["software"] == "nmap (any version)"
    assert api.calls[-1][1]["software_name"] == "nmap" and "software_id" not in api.calls[-1][1]
    run(tools.find_test_runs(software="nmap", software_version="7.94"))
    assert api.calls[-1][1]["software_id"] == "s0"


def test_failing_and_date_filters_scan_the_runs(stub):
    stub({"/test-runs": runs_page})
    failing = run(tools.find_test_runs(failing=True))
    assert {r["id"] for r in failing["items"]} == {RUN["id"], OTHER["id"]}
    dated = run(tools.find_test_runs(until="2026-10-29"))
    assert [r["id"] for r in dated["items"]] == [OLDER["id"]]


def test_a_run_comes_with_its_files_and_test_cases(stub):
    tests = [make_test(1), make_test(2, "fail", artifact_count=2), make_test(3)]
    api = stub({
        "/test-runs": runs_page,
        f"/test-runs/{RUN['id']}": RUN,
        f"/test-runs/{RUN['id']}/artifacts": {"items": [
            {"id": "f1", "test_run_id": RUN["id"], "filename": "junit.xml", "content_type": "application/xml",
             "size_bytes": 10, "created_at": "2026-10-30T02:41:00", "object_key": "secret/key"}]},
        "/tests": lambda p: {"items": [t for t in tests if p.get("outcome") in (None, t["outcome"])],
                             "total": len([t for t in tests if p.get("outcome") in (None, t["outcome"])])},
    })
    out = run(tools.get_test_run("Nightly"))
    assert out["run"]["id"] == RUN["id"] and out["run"]["url"] == "https://ci.example/77"
    assert out["note_on_run"].startswith("2 runs are named 'Nightly'")
    assert out["shared_files"] == [{"id": "f1", "filename": "junit.xml", "content_type": "application/xml",
                                    "size_bytes": 10, "uploaded": "2026-10-30"}]
    assert [t["id"] for t in out["items"]] == ["t1", "t2", "t3"]
    assert "test_run" not in out["items"][0] and out["items"][1]["artifacts"] == 2
    assert out["items"][0]["output"] == "stdout.log"
    assert ("/tests", {"test_run_id": RUN["id"], "page": 1, "page_size": 50}) in api.calls

    failed = run(tools.get_test_run(RUN["id"], outcome="fail"))
    assert [t["id"] for t in failed["items"]] == ["t2"] and failed["total"] == 1


def test_a_run_is_found_by_its_external_id_and_partial_names_are_not_guessed(stub):
    stub({"/test-runs": runs_page, f"/test-runs/{RUN['id']}/artifacts": {"items": []},
          "/tests": {"items": [], "total": 0}})
    assert run(tools.get_test_run("PIPELINE-77"))["run"]["id"] == RUN["id"]
    miss = run(tools.get_test_run("Night"))
    assert miss["found"] is False and miss["did_you_mean"] == ["Nightly", "Nightly arm64"]


def test_a_large_run_is_read_in_chunks_from_any_offset(stub):
    tests = [make_test(n) for n in range(7)]

    def page(p):
        start = (p["page"] - 1) * p["page_size"]
        return {"items": tests[start:start + p["page_size"]], "total": len(tests)}

    stub({f"/test-runs/{RUN['id']}": RUN, f"/test-runs/{RUN['id']}/artifacts": {"items": []}, "/tests": page})
    first = run(tools.get_test_run(RUN["id"], limit=3))
    assert [t["id"] for t in first["items"]] == ["t0", "t1", "t2"] and first["next_offset"] == 3
    # An offset off the page grid, as a trimmed response leaves it.
    middle = run(tools.get_test_run(RUN["id"], offset=4, limit=3))
    assert [t["id"] for t in middle["items"]] == ["t4", "t5", "t6"] and "next_offset" not in middle


def test_one_test_case_in_full_with_its_and_its_runs_files(stub):
    stub({
        "/tests/t2": make_test(2, "fail", notes="x" * 400, misc_data={"ping": 3}, artifact_count=1,
                              test_run_artifact_count=1),
        "/tests/t2/artifacts": {
            "items": [{"id": "a1", "filename": "screen.png", "content_type": "image/png", "size_bytes": 5}],
            "test_run": {"id": RUN["id"], "name": "Nightly"},
            "test_run_items": [{"id": "f1", "filename": "junit.xml", "content_type": "application/xml",
                                "size_bytes": 10}]},
    })
    out = run(tools.get_test("t2"))
    assert out["notes"] == "x" * 400 and out["fields"] == {"ping": 3}
    assert out["test_run"] == "Nightly" and out["test_run_id"] == RUN["id"]
    assert [f["id"] for f in out["files"]] == ["a1"] and [f["id"] for f in out["run_files"]] == ["f1"]
    assert run(tools.get_test("nope"))["found"] is False


def test_a_tests_listing_includes_its_runs_shared_files(stub):
    stub({"/tests/t1/artifacts": {"items": [], "test_run": {"id": RUN["id"], "name": "Nightly"},
                                  "test_run_items": [{"id": "f1", "filename": "junit.xml"}]}})
    out = run(tools.list_test_artifacts("t1"))
    assert out["items"] == [] and out["run_files"] == [{"id": "f1", "filename": "junit.xml"}]
    assert out["test_run"] == {"id": RUN["id"], "name": "Nightly"}


ARTIFACTS = {
    "/tests/t1/artifacts": {
        "items": [{"id": "a1", "filename": "result.json", "content_type": "application/json", "size_bytes": 11},
                  {"id": "a2", "filename": "capture.pcap", "content_type": "application/vnd.tcpdump.pcap",
                   "size_bytes": 9},
                  {"id": "a3", "filename": "blob", "content_type": "application/octet-stream", "size_bytes": 4}],
        "test_run": {"id": "run1", "name": "Nightly"},
        "test_run_items": [{"id": "f1", "filename": "junit.xml", "content_type": "application/xml",
                            "size_bytes": 8}]},
    "/test-runs/run1/artifacts": {"items": [{"id": "f1", "filename": "junit.xml",
                                             "content_type": "application/xml", "size_bytes": 8}]},
}
FILES = {
    "/tests/t1/artifacts/a1/download": b'{"ok": true}',
    "/tests/t1/artifacts/a3/download": b"\x89\x00\x01\x02",
    "/test-runs/run1/artifacts/f1/download": b"<junit/>",
}


def test_reading_a_tests_file_and_its_runs_file(stub):
    api = stub(ARTIFACTS, FILES)
    out = run(tools.read_artifact("a1", test_id="t1"))
    assert out["text"] == '{"ok": true}' and out["filename"] == "result.json" and "truncated" not in out
    # A run's shared file, reached from one of its tests.
    assert run(tools.read_artifact("f1", test_id="t1"))["text"] == "<junit/>"
    assert run(tools.read_artifact("f1", run_id="run1"))["text"] == "<junit/>"
    assert "/test-runs/run1/artifacts/f1/download" not in [path for path, _ in api.calls]


def test_binary_files_are_described_not_read(stub):
    stub(ARTIFACTS, FILES)
    pcap = run(tools.read_artifact("a2", test_id="t1"))
    assert pcap["readable"] is False and "text" not in pcap
    sniffed = run(tools.read_artifact("a3", test_id="t1"))
    assert sniffed["readable"] is False and "text" not in sniffed


def test_a_wrong_artifact_or_owner_is_reported_with_the_choices(stub):
    stub(ARTIFACTS, FILES)
    miss = run(tools.read_artifact("zzz", test_id="t1"))
    assert miss["found"] is False and {f["id"] for f in miss["files"]} == {"a1", "a2", "a3", "f1"}
    assert run(tools.read_artifact("a1", test_id="nope"))["found"] is False
    with pytest.raises(tools.ToolError):
        run(tools.read_artifact("a1"))
    with pytest.raises(tools.ToolError):
        run(tools.read_artifact("a1", test_id="t1", run_id="run1"))


def test_a_long_file_is_cut_from_the_requested_end(stub, monkeypatch):
    monkeypatch.setattr(tools.settings, "max_response_bytes", 4096)
    text = ("é" * 1500 + "b" * 3000).encode()
    routes = {"/test-runs/run1/artifacts": {"items": [
        {"id": "f1", "filename": "log.txt", "content_type": "text/plain", "size_bytes": len(text)}]}}
    stub(routes, {"/test-runs/run1/artifacts/f1/download": text})
    head = run(tools.read_artifact("f1", run_id="run1"))
    tail = run(tools.read_artifact("f1", run_id="run1", tail=True))
    assert head["truncated"] and set(head["text"]) == {"é"} and "tail=true" in head["note"]
    assert tail["truncated"] and set(tail["text"]) == {"b"} and len(tail["text"]) == 2048


def test_decoding_tolerates_a_character_cut_at_either_end():
    assert tools._decode("aé".encode()[:-1], cut_start=False, cut_end=True) == "a"
    assert tools._decode("éa".encode()[1:], cut_start=True, cut_end=False) == "a"
    # Uncut, a trailing high byte is not half a character: this is Latin-1.
    assert tools._decode("café".encode("latin-1"), cut_start=False, cut_end=False) == "café"
    assert tools._decode(b"\x00\x01", cut_start=False, cut_end=False) is None


def test_the_client_streams_a_download_keeping_only_one_end():
    import httpx

    from tb_mcp.client import ApiClient

    body = bytes(range(256)) * 40  # 10 KiB, sent in 1 KiB chunks

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for start in range(0, len(body), 1024):
                yield body[start:start + 1024]

    def handler(request):
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, json={"detail": "Artifact not found"})
        return httpx.Response(200, stream=Chunks())

    async def go():
        client = ApiClient()
        client._client = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(handler))
        try:
            head = await client.read_bytes("/f", keep=1500, tail=False, max_bytes=1 << 20)
            tail = await client.read_bytes("/f", keep=1500, tail=True, max_bytes=1 << 20)
            capped = await client.read_bytes("/f", keep=1500, tail=True, max_bytes=4096)
            with pytest.raises(NotFound):
                await client.read_bytes("/missing", keep=10, tail=False, max_bytes=100)
        finally:
            await client.aclose()
        return head, tail, capped

    head, tail, capped = asyncio.run(go())
    assert head == (body[:1500], 2048, False)
    assert tail == (body[-1500:], len(body), False)
    assert capped == (body[4096 - 1500:4096], 4096, True)
