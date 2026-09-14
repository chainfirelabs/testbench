import ast
import os
import secrets
import threading
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException
from kubernetes import client, config

app = FastAPI(title="TestBench network scan plugin")
PLUGIN_ID = "network-scan"
SHARED_SECRET = os.getenv("TB_PLUGIN_SHARED_SECRET", "")
TESTBENCH_URL = os.getenv("TB_TESTBENCH_URL", "http://testbench-backend:8000/api/v1").rstrip("/")
WORKER_NAMESPACE = os.getenv("TB_SCAN_WORKER_NAMESPACE", "testbench-scan")
WORKER_IMAGE = os.getenv("TB_SCAN_WORKER_IMAGE", "testbench-network-scan-plugin:latest")
MAX_CONCURRENT_PODS = int(os.getenv("TB_SCAN_MAX_CONCURRENT_PODS", "5"))
RUNS: dict[str, dict] = {}
RUNS_LOCK = threading.RLock()


def _log_output(output: str | bytes) -> str:
    if isinstance(output, bytes): output = output.decode("utf-8", errors="replace")
    elif output.startswith(("b'", 'b"')):
        try:
            raw = ast.literal_eval(output)
            if isinstance(raw, bytes): output = raw.decode("utf-8", errors="replace")
        except (SyntaxError, ValueError): pass
    return output[-100_000:]


def _reserve_run(run: dict) -> bool:
    with RUNS_LOCK:
        active = sum(item.get("state") in {"starting", "running"} for item in RUNS.values())
        if active >= MAX_CONCURRENT_PODS:
            return False
        RUNS[run["run_id"]] = run
        return True


def _fail_run(run_id: str, error: str) -> None:
    with RUNS_LOCK:
        run = RUNS[run_id]
        if run.get("state") in {"starting", "running"}:
            run.update(state="failed", token="", error=error,
                       finished_at=datetime.now(timezone.utc).isoformat())


def _auth(plugin: str | None, secret: str | None):
    if plugin != PLUGIN_ID or not SHARED_SECRET or not secrets.compare_digest(secret or "", SHARED_SECRET):
        raise HTTPException(status_code=401, detail="Invalid plugin credential")


@app.get("/plugin/v1/manifest")
def manifest(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    return {"id": PLUGIN_ID, "label": "Network Scan", "version": "1.0.0", "protocol_version": 1,
      "recommended_fields": [
        {"key": "wan_ip", "label": "WAN IP", "type": "text", "role": "scan_address_wan"},
        {"key": "online_status", "label": "Online", "type": "boolean", "role": "scan_state", "writable": False},
        {"key": "last_seen_online", "label": "Last Seen", "type": "text", "role": "last_seen", "writable": False},
        {"key": "last_scanned_at", "label": "Last Scanned", "type": "text", "writable": False},
      ], "actions": [
        # Probing an address is read-only, so it is safe to enable everywhere.
        # It still needs the device type to provide an address to probe.
        {"id": "network-scan.scan-device", "entity": "devices", "scope": "row", "label": "Scan",
         "title": "Check network services", "icon": "wifi", "risk": "read_only",
         "required_role_groups": [["scan_address_wan", "scan_address_lan"]],
         "allow_global_assignment": True},
        {"id": "network-scan.scan-all", "entity": "devices", "scope": "collection", "label": "Scan all",
         "icon": "wifi", "risk": "read_only",
         "required_role_groups": [["scan_address_wan", "scan_address_lan"]],
         "allow_global_assignment": True},
    ]}


@app.get("/plugin/v1/health")
def health(): return {"status": "ok"}


def _create_job(run_id: str, token: str):
    try:
        if RUNS[run_id].get("state") == "cancelled": return
        config.load_incluster_config()
        batch, core = client.BatchV1Api(), client.CoreV1Api()
        secret_name = f"scan-run-{run_id}"
        labels = {"testbench.io/plugin": PLUGIN_ID, "testbench.io/run": run_id}
        core.create_namespaced_secret(WORKER_NAMESPACE, client.V1Secret(
            metadata=client.V1ObjectMeta(name=secret_name, labels=labels), string_data={"token": token}))
        env = [
            client.V1EnvVar(name="TB_SCAN_RUN_ID", value=run_id),
            client.V1EnvVar(name="TB_SCAN_CONTROLLER_URL", value=os.getenv("TB_SCAN_CONTROLLER_URL", "http://testbench-network-scan:8080")),
            client.V1EnvVar(name="TB_SCAN_TIMEOUT_SECONDS", value=os.getenv("TB_SCAN_TIMEOUT_SECONDS", "2")),
            client.V1EnvVar(name="TB_SCAN_CONCURRENCY", value=os.getenv("TB_SCAN_CONCURRENCY", "10")),
            client.V1EnvVar(name="TB_SCAN_RUN_TOKEN", value_from=client.V1EnvVarSource(secret_key_ref=client.V1SecretKeySelector(name=secret_name, key="token"))),
        ]
        container = client.V1Container(name="scanner", image=WORKER_IMAGE,
            image_pull_policy=os.getenv("TB_SCAN_WORKER_IMAGE_PULL_POLICY", "IfNotPresent"),
            args=["worker"], env=env,
            security_context=client.V1SecurityContext(allow_privilege_escalation=False, run_as_non_root=True, capabilities=client.V1Capabilities(drop=["ALL"])),
            resources=client.V1ResourceRequirements(requests={"cpu": "100m", "memory": "128Mi"}, limits={"cpu": "1", "memory": "512Mi"}))
        pull_secrets = [client.V1LocalObjectReference(name=name) for name in os.getenv("TB_SCAN_WORKER_IMAGE_PULL_SECRETS", "").split(",") if name]
        pod = client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(labels=labels), spec=client.V1PodSpec(
            restart_policy="Never", service_account_name=os.getenv("TB_SCAN_WORKER_SERVICE_ACCOUNT", "testbench-network-scan-worker"), automount_service_account_token=False,
            image_pull_secrets=pull_secrets, containers=[container], security_context=client.V1PodSecurityContext(run_as_non_root=True, seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"))))
        if RUNS[run_id].get("state") == "cancelled": return
        batch.create_namespaced_job(WORKER_NAMESPACE, client.V1Job(metadata=client.V1ObjectMeta(name=f"scan-{run_id}", labels=labels),
            spec=client.V1JobSpec(template=pod, backoff_limit=0, ttl_seconds_after_finished=int(os.getenv("TB_SCAN_TTL_SECONDS", "300")), active_deadline_seconds=int(os.getenv("TB_SCAN_DEADLINE_SECONDS", "1800")))))
        deadline = time.monotonic() + int(os.getenv("TB_SCAN_DEADLINE_SECONDS", "1800"))
        pod_name = None
        while time.monotonic() < deadline and RUNS[run_id].get("state") != "cancelled":
            pods = core.list_namespaced_pod(WORKER_NAMESPACE, label_selector=f"testbench.io/run={run_id}").items
            if pods: pod_name = pods[0].metadata.name
            if pod_name:
                try: RUNS[run_id]["output"] = _log_output(core.read_namespaced_pod_log(pod_name, WORKER_NAMESPACE))
                except Exception: pass
            job = batch.read_namespaced_job_status(f"scan-{run_id}", WORKER_NAMESPACE)
            if job.status.failed:
                _fail_run(run_id, "Scan worker Job failed")
                break
            if job.status.succeeded: break
            time.sleep(1)
        if pod_name:
            try: RUNS[run_id]["output"] = _log_output(core.read_namespaced_pod_log(pod_name, WORKER_NAMESPACE))
            except Exception: pass
    except Exception as exc:
        _fail_run(run_id, str(exc))


@app.post("/plugin/v1/actions/{action_id}/invoke", status_code=202)
def invoke(action_id: str, body: dict, x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    if action_id not in {"network-scan.scan-device", "network-scan.scan-all"}: raise HTTPException(404, "Unknown action")
    run_id, token = secrets.token_hex(8), secrets.token_urlsafe(32)
    run = {"run_id": run_id, "action_id": action_id, "state": "starting", "output": "", "entities": body.get("entities", []), "token": token, "scanned": 0, "total": len(body.get("entities", [])), "started_at": datetime.now(timezone.utc).isoformat()}
    if not _reserve_run(run):
        raise HTTPException(429, f"Network scan already has {MAX_CONCURRENT_PODS} worker Pods running")
    threading.Thread(target=_create_job, args=(run_id, token), daemon=True).start()
    return {"run_id": run_id, "state": "starting", "started_at": run["started_at"]}


@app.get("/plugin/v1/runs")
def list_runs(x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    return {"runs": [{
        **{k: v for k, v in run.items() if k not in {"entities", "token"}},
        "entity_ids": [str(entity.get("id")) for entity in run.get("entities", []) if entity.get("id")],
    } for run in RUNS.values() if run.get("state") in {"starting", "running"}]}


@app.get("/plugin/v1/runs/{run_id}")
def status(run_id: str, x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    if run_id not in RUNS: raise HTTPException(404, "Run not found")
    return {k: v for k, v in RUNS[run_id].items() if k not in {"entities", "token"}}


@app.delete("/plugin/v1/runs/{run_id}")
def cancel(run_id: str, x_testbench_plugin: str | None = Header(None), x_testbench_plugin_secret: str | None = Header(None)):
    _auth(x_testbench_plugin, x_testbench_plugin_secret)
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run: raise HTTPException(404, "Run not found")
        if run.get("state") not in {"starting", "running"}:
            return {k: v for k, v in run.items() if k not in {"entities", "token"}}
        run.update(state="cancelled", token="", finished_at=datetime.now(timezone.utc).isoformat())
    config.load_incluster_config()
    try: client.BatchV1Api().delete_namespaced_job(f"scan-{run_id}", WORKER_NAMESPACE, propagation_policy="Background")
    except client.ApiException as exc:
        if exc.status != 404: raise
    try: client.CoreV1Api().delete_namespaced_secret(f"scan-run-{run_id}", WORKER_NAMESPACE)
    except Exception: pass
    return {k: v for k, v in run.items() if k not in {"entities", "token"}}


def _worker_run(run_id: str, authorization: str | None):
    run = RUNS.get(run_id)
    scheme, _, token = (authorization or "").partition(" ")
    if (not run or not run.get("token") or scheme.lower() != "bearer"
            or not token or not token.isascii()
            or not secrets.compare_digest(run["token"], token)):
        raise HTTPException(401, "Invalid run token")
    if run.get("state") not in {"starting", "running"}:
        raise HTTPException(409, "Run is no longer active")
    return run


@app.get("/worker/v1/runs/{run_id}")
def worker_input(run_id: str, authorization: str | None = Header(None)):
    with RUNS_LOCK:
        run = _worker_run(run_id, authorization)
        run["state"] = "running"
        return {"entities": run["entities"]}


@app.post("/worker/v1/runs/{run_id}/complete")
def worker_complete(run_id: str, body: dict, authorization: str | None = Header(None)):
    # Serialize final submission with cancellation: after cancellation wins,
    # no worker can use its former token to submit a result or reopen the run.
    with RUNS_LOCK:
        run = _worker_run(run_id, authorization)
        entities = {entity["id"]: entity for entity in run["entities"]}
        items = body.get("items", [])
        if not isinstance(items, list):
            raise HTTPException(422, "items must be an array")
        submitted = set()
        authorized_items = []
        for item in items:
            device_id = item.get("device_id") if isinstance(item, dict) else None
            if not isinstance(device_id, str) or device_id not in entities or device_id in submitted:
                raise HTTPException(422, "Each result must name a distinct device from this run")
            submitted.add(device_id)
            # The controller owns the snapshot, not the reporting worker.
            authorized_items.append({
                **item, "starting_addresses": entities[device_id].get("_scan_addresses", {}),
            })
        response = httpx.post(f"{TESTBENCH_URL}/plugin-host/network-scan/device-results",
            headers={"X-TestBench-Plugin": PLUGIN_ID, "X-TestBench-Plugin-Secret": SHARED_SECRET},
            json={"run_id": run_id, "items": authorized_items}, timeout=30)
        response.raise_for_status()
        run.update(state="completed", scanned=len(authorized_items), result=response.json(),
                   token="", finished_at=datetime.now(timezone.utc).isoformat())
    try:
        config.load_incluster_config()
        client.CoreV1Api().delete_namespaced_secret(f"scan-run-{run_id}", WORKER_NAMESPACE)
    except Exception:
        pass
    return run["result"]


def _scheduled_scans():
    interval = int(os.getenv("TB_SCAN_INTERVAL_MINUTES", "15"))
    while interval > 0:
        time.sleep(interval * 60)
        if any(run.get("state") in {"starting", "running"} for run in RUNS.values()):
            continue
        try:
            entities = httpx.get(f"{TESTBENCH_URL}/plugin-host/network-scan/devices", headers={
                "X-TestBench-Plugin": PLUGIN_ID, "X-TestBench-Plugin-Secret": SHARED_SECRET,
            }, timeout=30).json()
            run_id, token = secrets.token_hex(8), secrets.token_urlsafe(32)
            run = {"run_id": run_id, "action_id": "network-scan.scan-all", "state": "starting", "output": "", "entities": entities, "token": token, "scanned": 0, "total": len(entities), "scheduled": True, "started_at": datetime.now(timezone.utc).isoformat()}
            if not _reserve_run(run):
                continue
            threading.Thread(target=_create_job, args=(run_id, token), daemon=True).start()
        except Exception:
            pass


threading.Thread(target=_scheduled_scans, name="scheduled-scans", daemon=True).start()
