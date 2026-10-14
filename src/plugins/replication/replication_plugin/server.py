"""One optional controller for periodic or manually triggered peer sync."""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException

PLUGIN_ID = "replication"
logger = logging.getLogger(__name__)
MODE = os.getenv("TB_REPLICATION_MODE", "push")
INTERVAL_MINUTES = int(os.getenv("TB_REPLICATION_INTERVAL_MINUTES", "60"))
LOCAL_URL = os.getenv("TB_TESTBENCH_URL", "").rstrip("/")
PEER_URL = os.getenv("TB_REPLICATION_PEER_URL", "").rstrip("/")
TOKEN = os.getenv("TB_REPLICATION_TOKEN", "")
PLUGIN_SECRET = os.getenv("TB_PLUGIN_SHARED_SECRET", "")
ENTITIES = ("devices", "software", "vendor_claims", "tests")
STATE_LOCK = threading.Lock()
RUNNING = False
LAST_RUN: dict | None = None
CURRENT_RUN: dict | None = None
STOP = threading.Event()


def _time(item: dict) -> datetime:
    return datetime.fromisoformat(item["changed_at"].replace("Z", "+00:00"))


def _rank(item: dict) -> tuple:
    # Equal timestamps converge on one payload on both peers, even when each
    # instance independently changed a record at the same clock resolution.
    digest = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return (_time(item), digest)


def _latest_map(items: list[dict]) -> dict[str, dict]:
    winners: dict[str, dict] = {}
    for item in items:
        prior = winners.get(item["id"])
        if prior is None or _rank(item) > _rank(prior):
            winners[item["id"]] = item
    return winners


def select_winners(local: list[dict], peer: list[dict], mode: str) -> list[dict]:
    winners = _latest_map(local)
    if mode == "push_pull":
        for item in peer:
            prior = winners.get(item["id"])
            if prior is None or _rank(item) > _rank(prior):
                winners[item["id"]] = item
    return list(winners.values())


def _different(winner: dict, current: dict | None) -> bool:
    return current is None or winner != current


def _auth(plugin: str | None, secret: str | None) -> None:
    if plugin != PLUGIN_ID or not PLUGIN_SECRET or not hmac.compare_digest(secret or "", PLUGIN_SECRET):
        raise HTTPException(status_code=401, detail="Invalid plugin credential")


def _headers() -> dict[str, str]:
    return {"X-TestBench-Replication-Token": TOKEN}


def _get_all(client: httpx.Client, base: str, entity: str) -> list[dict]:
    items: list[dict] = []
    for tombstones in (False, True):
        cursor = ""
        while True:
            response = client.get(f"{base}/replication/snapshot", params={
                "entity": entity, "after": cursor, "tombstones": str(tombstones).lower(),
            }, headers=_headers())
            response.raise_for_status()
            page = response.json()
            items.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                break
    return items


def _apply(client: httpx.Client, base: str, items: list[dict], *, force: bool = False) -> dict:
    result = {"applied": 0, "skipped": 0}
    for start in range(0, len(items), 250):
        response = client.post(f"{base}/replication/apply", headers=_headers(), json={
            "items": items[start:start + 250], "force": force,
        })
        response.raise_for_status()
        for key, count in response.json().items():
            result[key] += count
    return result


def sync_once() -> dict:
    if MODE not in {"push", "push_pull"}:
        raise ValueError("Replication mode must be push or push_pull")
    if not LOCAL_URL or not PEER_URL or not TOKEN or LOCAL_URL == PEER_URL:
        raise ValueError("Local URL, different peer URL, and replication token are required")
    summary = {"mode": MODE, "entities": {}, "started_at": datetime.now(timezone.utc).isoformat()}
    with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
        for entity in ENTITIES:
            local = _get_all(client, LOCAL_URL, entity)
            peer = _get_all(client, PEER_URL, entity)
            winners = select_winners(local, peer, MODE)
            # Target first. If the local apply then fails, retrying the same
            # winner set is safe; the source has not lost its old version.
            peer_by_id = _latest_map(peer)
            local_by_id = _latest_map(local)
            remote_result = _apply(client, PEER_URL, [
                item for item in winners if _different(item, peer_by_id.get(item["id"]))
            ], force=MODE == "push")
            local_result = _apply(client, LOCAL_URL, [
                item for item in winners if _different(item, local_by_id.get(item["id"]))
            ]) if MODE == "push_pull" else None
            summary["entities"][entity] = {
                "selected": len(winners), "remote": remote_result, "local": local_result,
            }
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def _run(run_id: str, trigger: str) -> None:
    global RUNNING, LAST_RUN, CURRENT_RUN
    try:
        result = sync_once()
        result.update(run_id=run_id, trigger=trigger, state="succeeded")
    except Exception as exc:
        result = {"run_id": run_id, "trigger": trigger, "state": "failed",
                  "error": str(exc)[:500], "finished_at": datetime.now(timezone.utc).isoformat()}
    for attempt in range(3):
        try:
            with httpx.Client(timeout=httpx.Timeout(10, connect=5)) as client:
                response = client.post(
                    f"{LOCAL_URL}/replication/run-result",
                    headers={"X-TestBench-Plugin": PLUGIN_ID,
                             "X-TestBench-Plugin-Secret": PLUGIN_SECRET},
                    json={**result, "mode": MODE, "peer_url": PEER_URL},
                )
                response.raise_for_status()
            break
        except Exception as exc:
            if attempt == 2:
                logger.exception("Could not audit replication run %s", run_id)
                result["audit_error"] = str(exc)[:500]
            else:
                time.sleep(1)
    with STATE_LOCK:
        LAST_RUN = result
        CURRENT_RUN = None
        RUNNING = False


def start_run(trigger: str) -> dict:
    global RUNNING, CURRENT_RUN
    with STATE_LOCK:
        if RUNNING:
            raise HTTPException(status_code=409, detail="Replication is already running")
        RUNNING = True
        run_id = str(uuid4())
        CURRENT_RUN = {"run_id": run_id, "trigger": trigger, "state": "running",
                       "peer_url": PEER_URL, "mode": MODE,
                       "started_at": datetime.now(timezone.utc).isoformat()}
        accepted = dict(CURRENT_RUN)
    threading.Thread(target=_run, args=(run_id, trigger), daemon=True).start()
    return accepted


def _schedule() -> None:
    if INTERVAL_MINUTES <= 0:
        return
    while not STOP.wait(INTERVAL_MINUTES * 60):
        try:
            start_run("scheduled")
        except HTTPException:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    STOP.clear()
    threading.Thread(target=_schedule, daemon=True).start()
    yield
    STOP.set()


app = FastAPI(title="TestBench replication plugin", lifespan=lifespan)


@app.get("/plugin/v1/health")
def health():
    return {"status": "ok"}


@app.get("/plugin/v1/manifest")
def manifest(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    return {"id": PLUGIN_ID, "label": "Replication", "version": "1.0.0",
            "protocol_version": 1, "actions": []}


@app.get("/plugin/v1/status")
def status(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    with STATE_LOCK:
        return {"mode": MODE, "interval_minutes": INTERVAL_MINUTES,
                "peer_url": PEER_URL, "running": RUNNING,
                "current_run": CURRENT_RUN, "last_run": LAST_RUN}


@app.post("/plugin/v1/run", status_code=202)
def run(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    return start_run("manual")
