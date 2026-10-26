import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from test_summary_plugin import server

HEADERS = {"X-TestBench-Plugin": "test-summary", "X-TestBench-Plugin-Secret": "shared"}
REPORT = {
    "run": {"id": "run-1", "name": "Nightly"},
    "test_fields": [], "device_fields": [],
    "devices": {"d1": {"unique_id": "rtr-1", "device_type": "router"}},
    "tests": [{"id": "t1", "device_id": "d1", "outcome": "fail", "notes": "password=hunter2 broke",
               "output": None, "artifacts": []}],
    "shared_files": [],
}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("TB_PLUGIN_SHARED_SECRET", "shared")
    monkeypatch.setenv("TB_TESTBENCH_URL", "http://backend/api/v1")
    for name in ("TB_TEST_SUMMARY_AI_URL", "TB_TEST_SUMMARY_AI_MODEL", "TB_TEST_SUMMARY_PROMPT",
                 "TB_TEST_SUMMARY_AI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    server.RUNS.clear()


class Fakes:
    """A TestBench backend and an AI provider, as one httpx transport."""

    def __init__(self, save_status=201):
        self.requests: list[httpx.Request] = []
        self.save_status = save_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/report"):
            return httpx.Response(200, json=REPORT)
        if request.url.path.endswith("/summaries"):
            if self.save_status == 503:
                return httpx.Response(503, json={"detail": "Object storage is disabled"})
            return httpx.Response(201, json={"stored": True, "artifact_id": "a1",
                                             "filename": "ai-summary-2026-10-23T12-00-00Z.md"})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "## Summary\nrtr-1 failed."}}]})
        return httpx.Response(404)

    def install(self):
        original = httpx.Client
        transport = httpx.MockTransport(self)
        return patch.object(httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transport}))


def start(entity=None, actor=None):
    entity = entity or {"type": "test_run", "id": "run-1", "name": "Nightly",
                        "_ai_configuration": {"url": "https://ai.example/v1", "model": "gpt-test",
                                              "api_key": "gui-key", "provider_type": "openai"}}
    server.RUNS["r1"] = {"run_id": "r1", "state": "starting", "output": ""}
    server._job("r1", entity, actor or {"id": "user-1"})
    return server.RUNS["r1"]


def test_manifest_is_a_global_test_run_action_and_reflects_helm_ai(monkeypatch):
    client = TestClient(server.app)
    assert client.get("/plugin/v1/manifest").status_code == 401
    body = client.get("/plugin/v1/manifest", headers=HEADERS).json()
    assert body["global"] is True
    assert body["actions"][0]["entity"] == "test_runs"
    assert body["ai_configuration"]["locked"] is False
    monkeypatch.setenv("TB_TEST_SUMMARY_AI_URL", "https://ai.example/v1")
    monkeypatch.setenv("TB_TEST_SUMMARY_AI_MODEL", "helm-model")
    locked = client.get("/plugin/v1/manifest", headers=HEADERS).json()["ai_configuration"]
    assert locked == {"locked": True, "provider_type": "openai-compatible", "url": "https://ai.example/v1",
                      "model": "helm-model", "repeat_model": "helm-model"}


def test_invoke_needs_one_test_run_and_starts_a_job():
    client = TestClient(server.app)
    bad = client.post("/plugin/v1/actions/test-summary.summarize/invoke", headers=HEADERS,
                      json={"entities": [{"type": "device", "id": "d1"}]})
    assert bad.status_code == 422
    with patch.object(server.threading, "Thread") as thread:
        started = client.post("/plugin/v1/actions/test-summary.summarize/invoke", headers=HEADERS,
                              json={"entities": [{"type": "test_run", "id": "run-1"}], "actor": {"id": "u"}})
    assert started.status_code == 202
    assert started.json()["entity_ids"] == ["run-1"]
    thread.return_value.start.assert_called_once()


def test_concurrent_runs_are_limited(monkeypatch):
    monkeypatch.setenv("TB_TEST_SUMMARY_MAX_CONCURRENT_RUNS", "1")
    server.RUNS["busy"] = {"run_id": "busy", "state": "running", "output": ""}
    client = TestClient(server.app)
    response = client.post("/plugin/v1/actions/test-summary.summarize/invoke", headers=HEADERS,
                           json={"entities": [{"type": "test_run", "id": "run-1"}]})
    assert response.status_code == 429


def test_a_run_reads_asks_and_saves_a_report():
    fakes = Fakes()
    with fakes.install():
        run = start()
    assert run["state"] == "completed", run.get("error")
    assert run["result"]["file"]["filename"].startswith("ai-summary-")
    assert "rtr-1 failed." in run["result"]["markdown"]
    assert "| fail | 1 | 100.0% |" in run["result"]["markdown"]
    report, chat, save = fakes.requests
    assert report.headers["X-TestBench-Plugin-Secret"] == "shared"
    assert chat.headers["Authorization"] == "Bearer gui-key"
    sent = json.loads(chat.content)
    assert sent["model"] == "gpt-test"
    assert "hunter2" not in sent["messages"][1]["content"]
    saved = json.loads(save.content)
    assert saved["actor_id"] == "user-1" and saved["run_id"] == "r1" and saved["model"] == "gpt-test"
    assert "Saved ai-summary-" in run["output"]


def test_helm_managed_ai_uses_the_mounted_key_and_the_helm_prompt(monkeypatch):
    monkeypatch.setenv("TB_TEST_SUMMARY_AI_API_KEY", "helm-key")
    monkeypatch.setenv("TB_TEST_SUMMARY_PROMPT", "Three bullets for managers.")
    fakes = Fakes()
    with fakes.install():
        run = start({"type": "test_run", "id": "run-1", "_ai_configuration": {
            "locked": True, "url": "https://ai.example/v1", "model": "helm-model", "api_key": None}})
    assert run["state"] == "completed", run.get("error")
    chat = fakes.requests[1]
    assert chat.headers["Authorization"] == "Bearer helm-key"
    assert json.loads(chat.content)["messages"][0]["content"].startswith("Three bullets for managers.")


def test_without_object_storage_the_report_is_shown_but_not_kept():
    fakes = Fakes(save_status=503)
    with fakes.install():
        run = start()
    assert run["state"] == "completed"
    assert run["result"]["file"] is None
    assert "Not saved: object storage is disabled" in run["output"]


def test_an_ai_failure_fails_the_run_with_the_providers_reason():
    def refuse(request):
        if request.url.path.endswith("/report"):
            return httpx.Response(200, json=REPORT)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    original = httpx.Client
    transport = httpx.MockTransport(refuse)
    with patch.object(httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transport})):
        run = start()
    assert run["state"] == "failed"
    assert "refused the request (401): bad key" in run["error"]


def test_no_ai_configuration_fails_clearly():
    fakes = Fakes()
    with fakes.install():
        run = start({"type": "test_run", "id": "run-1"})
    assert run["state"] == "failed"
    assert "No AI endpoint and model are configured" in run["error"]


def test_a_cancelled_run_saves_nothing():
    fakes = Fakes()
    client = TestClient(server.app)

    def cancel_then_answer(request):
        if request.url.path.endswith("/chat/completions"):
            client.delete("/plugin/v1/runs/r1", headers=HEADERS)
        return fakes(request)

    original = httpx.Client
    transport = httpx.MockTransport(cancel_then_answer)
    with patch.object(httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transport})):
        run = start()
    assert run["state"] == "cancelled"
    assert not any(request.url.path.endswith("/summaries") for request in fakes.requests)
    assert "Nothing was saved" in run["output"]
