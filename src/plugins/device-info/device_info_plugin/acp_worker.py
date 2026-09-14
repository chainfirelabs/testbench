"""Run one TestBench device-information prompt through OMP's ACP v1 server."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone


PROMPT = os.environ["TB_INFO_RUN_PROMPT"]
SYSTEM_PROMPT = os.getenv("TB_AI_SYSTEM_PROMPT", "").strip()
MODEL = os.getenv("TB_AI_MODEL", "")
ACP_COMMAND = os.getenv(
    "TB_ACP_COMMAND",
    "/usr/local/bin/entrypoint.sh acp --tools browser --approval-mode yolo",
).split()
RESULT_PREFIX = "TESTBENCH_RESULT_JSON="
EVENT_PREFIX = "TESTBENCH_EVENT_JSON="

process: subprocess.Popen[str] | None = None
session_id: str | None = None
cancel_requested = threading.Event()
write_lock = threading.Lock()
next_request_id = 1
assistant_text = ""
result_emitted = False


def emit_event(event_type: str, **payload) -> None:
    event = {
        "type": event_type,
        "at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    print(EVENT_PREFIX + json.dumps(event, separators=(",", ":")), flush=True)


def send(method: str, params: dict, request_id: int | None = None) -> None:
    if not process or not process.stdin:
        return
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        message["id"] = request_id
    with write_lock:
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()


def request(method: str, params: dict) -> int:
    global next_request_id
    request_id = next_request_id
    next_request_id += 1
    send(method, params, request_id)
    return request_id


def request_cancel(reason: str) -> None:
    if cancel_requested.is_set():
        return
    cancel_requested.set()
    emit_event("run.cancelling", reason=reason)
    if session_id:
        send("session/cancel", {"sessionId": session_id})


def handle_signal(signum, _frame) -> None:
    request_cancel(signal.Signals(signum).name.lower())


def read_stderr(stream, messages: queue.Queue[tuple[str, str]]) -> None:
    for line in stream:
        messages.put(("stderr", line.rstrip("\n")))
    messages.put(("stderr_eof", ""))


def answer_permission(message: dict) -> None:
    # The ACP server is launched with only the browser tool and that tool is
    # approved at launch. Any permission request is therefore unexpected. Deny
    # it fail-closed instead of inferring authorization from human text.
    outcome = {"outcome": {"outcome": "cancelled"}}
    emit_event("permission.denied", reason="Interactive permission requests are not allowed")
    response = {"jsonrpc": "2.0", "id": message["id"], "result": outcome}
    with write_lock:
        process.stdin.write(json.dumps(response, separators=(",", ":")) + "\n")
        process.stdin.flush()


def extract_result(allow_bare: bool = False) -> None:
    global result_emitted
    if result_emitted:
        return
    start = assistant_text.rfind(RESULT_PREFIX)
    if start >= 0:
        candidate = assistant_text[start + len(RESULT_PREFIX):].lstrip()
    elif allow_bare:
        candidate = assistant_text.strip()
    else:
        return
    try:
        value, _end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return
    if not isinstance(value, dict) or not {"findings", "model", "artifacts"}.issubset(value):
        return
    print(RESULT_PREFIX + json.dumps(value, separators=(",", ":")), flush=True)
    result_emitted = True
    emit_event("result.received")


def display_content(content) -> str:
    if isinstance(content, dict) and content.get("type") == "text":
        return str(content.get("text", ""))
    return ""


def handle_update(update: dict) -> None:
    global assistant_text
    kind = update.get("sessionUpdate", "unknown")
    if kind == "agent_message_chunk":
        text = display_content(update.get("content"))
        if text:
            assistant_text += text
            emit_event("assistant.delta", text=text)
            extract_result()
        return
    if kind == "tool_call":
        emit_event(
            "tool.started",
            toolCallId=update.get("toolCallId"),
            title=update.get("title", "Tool call"),
            status=update.get("status", "pending"),
            kind=update.get("kind"),
        )
        return
    if kind == "tool_call_update":
        emit_event(
            "tool.progress",
            toolCallId=update.get("toolCallId"),
            title=update.get("title"),
            status=update.get("status"),
        )
        return
    if kind == "usage_update":
        emit_event("usage.updated", usage=update.get("usage") or {})
        return
    # ACP also publishes session metadata/configuration notifications. They are
    # useful to a full editor client but add substantial noise to TestBench's
    # worker window, so intentionally omit them from the user-facing stream.


def wait_for_response(messages, request_id: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.1, min(1.0, deadline - time.monotonic()))
        try:
            source, payload = messages.get(timeout=remaining)
        except queue.Empty:
            if process.poll() is not None:
                raise RuntimeError(f"OMP ACP exited with status {process.returncode}")
            continue
        if source == "stderr":
            print(payload, file=sys.stderr, flush=True)
            continue
        if source == "stdout_eof":
            raise RuntimeError(f"OMP ACP closed stdout with status {process.poll()}")
        message = json.loads(payload)
        if "method" in message:
            method = message["method"]
            if method == "session/update":
                handle_update((message.get("params") or {}).get("update") or {})
            elif method == "session/request_permission" and "id" in message:
                answer_permission(message)
            elif "id" in message:
                response = {
                    "jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": -32601, "message": "Unsupported by TestBench ACP client"},
                }
                with write_lock:
                    process.stdin.write(json.dumps(response, separators=(",", ":")) + "\n")
                    process.stdin.flush()
            continue
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"ACP {request_id} failed: {message['error']}")
            return message.get("result") or {}
    raise TimeoutError(f"Timed out waiting for ACP request {request_id}")


def read_stdout(stream, messages: queue.Queue[tuple[str, str]]) -> None:
    for line in stream:
        messages.put(("stdout", line))
    messages.put(("stdout_eof", ""))


def main() -> int:
    global process, session_id
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, handle_signal)

    env = os.environ.copy()
    if MODEL:
        # The OMP image entrypoint renders this role into config.yml before it
        # starts ACP, making the configured discovery/repeat model authoritative.
        env["OMP_ROLE_DEFAULT"] = MODEL
    process = subprocess.Popen(
        ACP_COMMAND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    messages: queue.Queue[tuple[str, str]] = queue.Queue()
    threading.Thread(target=read_stdout, args=(process.stdout, messages), daemon=True).start()
    threading.Thread(target=read_stderr, args=(process.stderr, messages), daemon=True).start()

    emit_event("run.started", protocol="acp", protocolVersion=1, requestedModel=MODEL)
    initialized = wait_for_response(
        messages,
        request("initialize", {"protocolVersion": 1, "clientCapabilities": {}}),
        60,
    )
    agent = initialized.get("agentInfo") or {}
    emit_event(
        "agent.initialized",
        name=agent.get("name"), version=agent.get("version"),
        protocolVersion=initialized.get("protocolVersion"),
    )
    session = wait_for_response(
        messages,
        request("session/new", {"cwd": "/tmp", "mcpServers": []}),
        60,
    )
    session_id = session["sessionId"]
    selected_model = next(
        (item.get("currentValue") for item in session.get("configOptions", []) if item.get("id") == "model"),
        MODEL,
    )
    emit_event("model.selected", requested=MODEL, resolved=selected_model)

    full_prompt = PROMPT
    if SYSTEM_PROMPT:
        full_prompt = SYSTEM_PROMPT + "\n\n" + PROMPT
    prompt_id = request("session/prompt", {
        "sessionId": session_id,
        "prompt": [{"type": "text", "text": full_prompt}],
    })
    result = wait_for_response(messages, prompt_id, float(os.getenv("TB_ACP_PROMPT_TIMEOUT_SECONDS", "1800")))
    extract_result(allow_bare=True)
    stop_reason = result.get("stopReason", "unknown")
    emit_event("run.cancelled" if stop_reason == "cancelled" else "run.completed", stopReason=stop_reason, usage=result.get("usage") or {})
    if stop_reason == "cancelled" and not result_emitted:
        return 130
    if not result_emitted:
        print("ACP prompt completed without a TESTBENCH_RESULT_JSON payload", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:
        emit_event("run.failed", error=str(exc))
        print(f"ACP worker failed: {exc}", file=sys.stderr, flush=True)
        exit_code = 1
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    raise SystemExit(exit_code)
