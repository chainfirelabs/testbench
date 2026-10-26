"""AI Test Summary: a report on a whole test run, written by an AI model.

A global plugin. When Helm deploys it, every test run gets a "Summarize with
AI" action, whatever devices or device types the run covered. A run of the
action reads the run back from TestBench (tests, devices, outputs and the text
of every file), computes the exact numbers, asks the model for the narrative,
and saves the result to the run's shared files as a new markdown file, so every
version is kept.

Nothing here needs Kubernetes: one request to TestBench, one or a few to the
model, one to save. It runs in this process, a thread per run.
"""

import os
import secrets
import threading
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException

from . import ai, report

app = FastAPI(title="TestBench AI Test Summary plugin")
PLUGIN_ID = "test-summary"
ACTION_ID = "test-summary.summarize"
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.Lock()
MAX_KEPT_RUNS = 100


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _testbench_url() -> str:
    return _env("TB_TESTBENCH_URL", "http://testbench-backend:8000/api/v1").rstrip("/")


def _headers() -> dict[str, str]:
    return {"X-TestBench-Plugin": PLUGIN_ID, "X-TestBench-Plugin-Secret": _env("TB_PLUGIN_SHARED_SECRET")}


def _auth(plugin: str | None, secret: str | None):
    shared = _env("TB_PLUGIN_SHARED_SECRET")
    if plugin != PLUGIN_ID or not shared or not secrets.compare_digest(secret or "", shared):
        raise HTTPException(401, "Invalid plugin credential")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/plugin/v1/manifest")
def manifest(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    ai_url, ai_model = _env("TB_TEST_SUMMARY_AI_URL"), _env("TB_TEST_SUMMARY_AI_MODEL")
    return {
        "id": PLUGIN_ID, "label": "AI Test Summary", "version": "1.0.0", "protocol_version": 1,
        # Not enabled per device type: a test run can span several.
        "global": True,
        "ai_configuration": {
            "locked": bool(ai_url or ai_model), "provider_type": "openai-compatible",
            "url": ai_url, "model": ai_model, "repeat_model": ai_model,
        },
        "actions": [{
            "id": ACTION_ID, "entity": "test_runs", "scope": "row",
            "label": "Summarize with AI",
            "title": "Write a report on this run with AI: devices, results, outputs and files",
            "icon": "sparkles", "risk": "read_only",
        }],
    }


@app.get("/plugin/v1/health")
def health():
    return {"status": "ok"}


@app.post("/plugin/v1/actions/{action_id}/invoke", status_code=202)
def invoke(action_id: str, body: dict, x_testbench_plugin: str | None = Header(None),
           x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    if action_id != ACTION_ID:
        raise HTTPException(404, "Unknown action")
    entities = body.get("entities") or []
    if len(entities) != 1 or entities[0].get("type") != "test_run" or not entities[0].get("id"):
        raise HTTPException(422, "AI Test Summary needs exactly one test run")
    entity = entities[0]
    run_id = secrets.token_hex(8)
    run = {
        "run_id": run_id, "action_id": action_id, "entity_ids": [str(entity["id"])],
        "state": "starting", "output": "", "started_at": _now(),
    }
    limit = _int_env("TB_TEST_SUMMARY_MAX_CONCURRENT_RUNS", 3)
    with RUNS_LOCK:
        active = sum(item.get("state") in {"starting", "running"} for item in RUNS.values())
        if active >= limit:
            raise HTTPException(429, f"AI Test Summary is already writing {limit} reports; try again shortly")
        RUNS[run_id] = run
        _forget_old_runs()
    threading.Thread(target=_job, args=(run_id, entity, body.get("actor") or {}), daemon=True).start()
    return dict(run)


@app.get("/plugin/v1/runs/{run_id}")
def status(run_id: str, x_testbench_plugin: str | None = Header(None),
           x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    with RUNS_LOCK:
        if run_id not in RUNS:
            raise HTTPException(404, "Run not found")
        return dict(RUNS[run_id])


@app.get("/plugin/v1/runs")
def list_runs(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    with RUNS_LOCK:
        return {"runs": [dict(run) for run in RUNS.values() if run.get("state") in {"starting", "running"}]}


@app.delete("/plugin/v1/runs/{run_id}")
def cancel(run_id: str, x_testbench_plugin: str | None = Header(None),
           x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            raise HTTPException(404, "Run not found")
        if run.get("state") in {"starting", "running"}:
            # The job checks this between steps and stops before saving; a
            # model request already in flight is left to finish and discarded.
            run.update(state="cancelled", finished_at=_now())
            run["output"] += "Stopped. Nothing was saved.\n"
        return dict(run)


def _forget_old_runs() -> None:
    finished = [key for key, run in RUNS.items() if run.get("state") not in {"starting", "running"}]
    for key in finished[:max(0, len(RUNS) - MAX_KEPT_RUNS)]:
        RUNS.pop(key, None)


class _Cancelled(Exception):
    pass


def _update(run_id: str, *, log: str | None = None, **fields) -> None:
    with RUNS_LOCK:
        run = RUNS[run_id]
        if run.get("state") == "cancelled":
            raise _Cancelled()
        run.update(fields)
        if log:
            run["output"] += log + "\n"


def _job(run_id: str, entity: dict, actor: dict) -> None:
    try:
        _update(run_id, state="running", log=f"Reading test run “{entity.get('name') or entity['id']}”…")
        timeout = float(_int_env("TB_TEST_SUMMARY_REQUEST_TIMEOUT_SECONDS", 300))
        with httpx.Client(timeout=60) as client:
            response = client.get(f"{_testbench_url()}/plugin-host/test-summary/test-runs/{entity['id']}/report",
                                  headers=_headers())
            response.raise_for_status()
            data = response.json()
        stats = report.compute_stats(data)
        files = stats["files"]
        _update(run_id, log=(
            f"Read {stats['tests']} tests on {stats['devices']} devices; {files['shared_files']} shared files, "
            f"{files['test_outputs']} test outputs and {files['test_artifacts']} test artifacts "
            f"({files['read_as_text']} read as text)."
        ))
        configuration = entity.get("_ai_configuration") or {}
        model = configuration.get("model")
        notes: list[str] = []
        if stats["tests"] == 0 and not data.get("shared_files"):
            narrative = "This run has no tests and no shared files, so there is nothing to summarise yet."
        else:
            narrative = _write(run_id, data, stats, configuration, timeout, notes)
        markdown = report.render(data, stats, narrative, model=model,
                                 generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                                 notes=notes)
        _update(run_id, log="Saving the report to the run's shared files…")
        saved = _save(run_id, entity["id"], actor.get("id"), markdown, model)
        result = {"markdown": markdown, "model": model, "file": saved}
        if saved:
            _update(run_id, log=f"Saved {saved['filename']}.")
        _update(run_id, state="completed", finished_at=_now(), result=result)
    except _Cancelled:
        return
    except Exception as exc:  # noqa: BLE001 — the run reports what went wrong.
        message = _message(exc)
        try:
            _update(run_id, state="failed", finished_at=_now(), error=message, log=f"Failed: {message}")
        except _Cancelled:
            return


def _write(run_id: str, data: dict, stats: dict, configuration: dict, timeout: float, notes: list[str]) -> str:
    """The model's narrative: one request, or one per part and a final one."""
    planned = report.plan(data, stats, _int_env("TB_TEST_SUMMARY_MAX_INPUT_CHARS", 100_000),
                          _int_env("TB_TEST_SUMMARY_MAX_AI_CALLS", 12))
    prompt = report.system_prompt(_env("TB_TEST_SUMMARY_PROMPT"))
    key = _env("TB_TEST_SUMMARY_AI_API_KEY")
    model = configuration.get("model") or "the model"

    def ask(system: str, content: dict, step: str) -> str:
        _update(run_id, log=f"Asking {model} {step}…")
        return ai.chat(configuration, [
            {"role": "system", "content": system},
            {"role": "user", "content": "Test run data (JSON):\n" + report.json.dumps(content, default=str, ensure_ascii=False)},
        ], helm_api_key=key, timeout=timeout)

    if planned["mode"] == "single":
        return ask(prompt, planned["context"], "to write the report")
    parts = planned["parts"]
    _update(run_id, log=f"The run is too large for one request; reading it in {len(parts)} parts.")
    findings = []
    for index, part in enumerate(parts, 1):
        noted = ask(f"{report.CHUNK_PROMPT}\n\n{report.GUARD}", {"device": part["device"], "tests": part["tests"]},
                    f"about part {index} of {len(parts)} ({part['device']})")
        findings.append({"device": part["device"], "notes": noted})
    if planned["omitted_tests"]:
        notes.append(f"{planned['omitted_tests']} tests were counted in the tables but not read by the model "
                     "(passing tests first), to stay within the request limit")
    return ask(prompt, {**planned["context"], "findings_by_part": findings}, "to write the report from those parts")


def _save(run_id: str, test_run_id: str, actor_id: str | None, markdown: str, model: str | None) -> dict | None:
    _update(run_id)  # stop here if the run was cancelled
    with httpx.Client(timeout=60) as client:
        response = client.post(
            f"{_testbench_url()}/plugin-host/test-summary/test-runs/{test_run_id}/summaries",
            headers=_headers(), json={"run_id": run_id, "actor_id": actor_id, "markdown": markdown, "model": model},
        )
    if response.status_code == 503:
        # No object storage: the report is still shown, just not kept.
        _update(run_id, log="Not saved: object storage is disabled, so the run cannot keep files.")
        return None
    response.raise_for_status()
    return response.json()


def _message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail")
        except ValueError:
            detail = None
        return f"TestBench answered {exc.response.status_code}: {detail or exc.response.reason_phrase}"
    if isinstance(exc, httpx.HTTPError):
        return f"Could not reach TestBench: {exc}"
    return str(exc) or exc.__class__.__name__
