#!/usr/bin/env python3
"""Seed TestBench with correlated dev data:
~100 devices, 50 software (with device targets), 500 tests.

Every test references real software AND a device it actually targets,
so Expected Software / Verified Tests tabs are meaningful.
Run: python3 seed_dev_data.py [--force]
"""
import json
import random
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BASE = "http://localhost:8001/api/v1"
random.seed(42)  # reproducible dataset

# ---------------------------------------------------------------- helpers

def req(method, path, token=None, body=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def parallel(method, path_fn, bodies, token, workers=8):
    """POST/PUT each body; return list of (index, status, result)."""
    def one(i_body):
        i, body = i_body
        return (i,) + req(method, path_fn(i), token, body)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, enumerate(bodies)))

# ---------------------------------------------------------------- devices

VENDORS = {
    "Cisco":    (["ISR 4331", "ISR 4451", "C9300-24T", "ASR 1002-HX", "Catalyst 9200"], "17.9.4a"),
    "Juniper":  (["MX204", "EX4300-24T", "SRX345", "PTX-1000-36C"], "21.4R3.7"),
    "Arista":   (["7050X3-48YC8", "7280R3-64C", "DCS-7050S-16"], "4.28.1.7"),
    "HPE":      (["Aruba 2930F-24G", "Aruba CX 6300M", "S1400-26G"], "16.11.0024"),
    "Dell":     (["PowerSwitch S5224F", "N1148T-ON"], "6.5.36"),
    "Ubiquiti": (["EdgeRouter X", "ER-X-SFP", "USW-24-PoE"], "4.4.5"),
    "Fortinet": (["FortiGate 60F", "FortiGate 100F"], "7.4.2"),
    "Palo Alto":(["PA-220", "PA-440"], "10.2.8"),
    "MikroTik": (["CCR2004-1G-12S+2XS", "RB4011i"], "7.15"),
    "Netgear":  (["GS728TPro", "M4300-24X"], "10.3.2.72"),
}
LOCATIONS = ["Lab A", "Lab B", "Rack R1", "Rack R2", "Rack R3", "Rack R4",
             "DC-East", "DC-West", "Warehouse", "Field"]

# The closed set the API enforces (DEVICE_ARCHITECTURES in
# backend/app/models/device.py) — anything outside it is a 422, so this list has
# to track that one. Weighted rather than uniform: x86_64 and the ARM/MIPS
# families are what most fleet hardware actually reports, while tilegx and
# lexra_mips are niche enough that a uniform draw would over-represent them
# tenfold.
ARCH_MIX = (["x86_64"] * 22 + ["arm64"] * 16 + ["aarch64"] * 10 + ["arm"] * 14 +
            ["mipsbe"] * 12 + ["mipsel"] * 10 + ["x86"] * 8 + ["ppc"] * 5 +
            ["tilegx"] * 2 + ["lexra_mips"] * 1)
STATUS_MIX = (["available"] * 55 + ["checked_out"] * 12 + ["in_testing"] * 12 +
              ["maintenance"] * 12 + ["retired"] * 9)


CHECKOUT_USERS = ["tester1", "tester2", "tester3"]


def make_device(i):
    make = random.choice(list(VENDORS))
    models, fw = VENDORS[make]
    status = STATUS_MIX[i]
    n = i + 1
    d = {
        "unique_id": f"dev-{n:04d}",
        # create checked_out devices as available; we transition them later so
        # checked_out_by / checked_out_at get stamped by a real actor
        "status": "available" if status == "checked_out" else status,
        "location": random.choice(LOCATIONS),
        "make": make,
        "model": random.choice(models),
        "firmware_version": fw,
        "architecture": random.choice(ARCH_MIX),
        "hardware_version": f"Rev {random.choice(['A', 'B', 'C'])}",
        "lan_ip": f"10.0.{random.randint(0, 3)}.{random.randint(2, 250)}",
        "online_status": status in ("available", "in_testing") and random.random() < 0.9,
        "misc_data": {
            "serial_number": f"SN{random.randint(10**9, 10**10 - 1)}",
            "asset_tag": f"AST-{n:05d}",
            "purchased": f"20{random.randint(18, 24)}-{random.randint(1, 12):02d}",
            "warranty_until": f"20{random.randint(25, 28)}-{random.randint(1, 12):02d}",
        },
    }
    if random.random() < 0.4:
        d["wan_ip"] = f"{random.randint(1, 220)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(2, 250)}"
    if d["online_status"]:
        d["last_seen_online"] = (datetime.now(timezone.utc) - timedelta(hours=random.randint(0, 72))).isoformat()
    return d

# ---------------------------------------------------------------- software

# (name, category, weight) — weight = relative frequency of tests using it
SOFTWARE = [
    ("speedtest-cli", "bandwidth", 10), ("iperf3", "bandwidth", 8),
    ("bandwidth-baseline", "bandwidth", 6), ("throughput-test", "bandwidth", 5),
    ("ping", "latency", 12), ("traceroute", "latency", 6), ("mtr", "latency", 5),
    ("latency-baseline", "latency", 5), ("jitter-test", "latency", 4),
    ("packet-loss-test", "latency", 5),
    ("smartctl", "hardware", 6), ("lm-sensors", "hardware", 5),
    ("ipmitool", "hardware", 5), ("stress-ng", "hardware", 4),
    ("memtester", "hardware", 4), ("hdparm", "hardware", 3),
    ("fan-check", "hardware", 3), ("power-draw", "hardware", 3),
    ("firmware-verify", "software", 9), ("config-backup", "software", 6),
    ("backup-restore", "software", 5), ("reboot-cycle", "software", 6),
    ("failover-test", "software", 6), ("ha-failover", "software", 5),
    ("vlan-check", "software", 4), ("dns-resolve", "software", 4),
    ("http-load", "software", 4), ("ssl-check", "software", 4),
    ("cert-check", "software", 3), ("syslog-check", "software", 3),
    ("ntp-sync", "software", 4), ("radius-test", "software", 4),
    ("dhcp-lease", "software", 4), ("snmp-walk", "software", 3),
    ("netcat-scan", "software", 3), ("nmap-scan", "software", 4),
    ("curl-smoke", "software", 5), ("tshark-capture", "software", 3),
    ("port-mirror", "software", 2), ("qos-shape", "software", 3),
    ("sflow-sample", "software", 2), ("netflow-check", "software", 2),
    ("bgp-peer", "software", 4), ("ospf-adj", "software", 4),
    ("stp-topology", "software", 2), ("lldp-neighbors", "software", 3),
    ("cdp-neighbors", "software", 2), ("mac-table", "software", 2),
    ("routing-table", "software", 3), ("cli-script", "software", 2),
]
assert len(SOFTWARE) == 50

NOTES = [
    "baseline run", "flaky - retest scheduled", "firmware regression suspected",
    "passed after retry", "intermittent, see capture", "within tolerance",
    "threshold exceeded", "environmental: hot rack", "post-migration check",
    "nightly automated run", "manual spot check", "customer-reported issue",
]


def _band(outcome, good, warn, bad):
    """Pick a value range by outcome: pass=good, warn=borderline, fail=bad."""
    if outcome == "pass":
        return random.uniform(*good)
    if outcome == "warn":
        return random.uniform(*warn)
    return random.uniform(*bad)


def test_data(category, outcome):
    if category == "bandwidth":
        return {
            "download_mbps": round(_band(outcome, (400, 940), (300, 400), (30, 250)), 1),
            "upload_mbps": round(_band(outcome, (200, 900), (120, 200), (10, 120)), 1),
            "latency_ms": round(_band(outcome, (1, 12), (12, 25), (25, 90)), 2),
        }
    if category == "latency":
        return {
            "latency_ms": round(_band(outcome, (0.4, 8), (8, 20), (20, 120)), 2),
            "packet_loss_pct": round(_band(outcome, (0, 0.5), (0.5, 2), (2, 15)), 2),
            "jitter_ms": round(_band(outcome, (0.1, 2), (2, 8), (8, 40)), 2),
            "samples": random.choice([100, 200, 500, 1000]),
        }
    if category == "hardware":
        metric = random.choice(["temp_c", "fan_rpm", "voltage_v", "power_w", "smart_realloc"])
        thr = 80.0
        val = round(_band(outcome, (30, 70), (70, 85), (85, 120)), 1)
        return {"metric": metric, "value": val, "unit": {"temp_c": "°C", "fan_rpm": "rpm",
                "voltage_v": "V", "power_w": "W", "smart_realloc": "sectors"}[metric],
                "threshold": thr, "within_threshold": val <= thr}
    # software
    score = round(_band(outcome, (90, 100), (80, 90), (30, 80)), 2)
    return {"value": score, "unit": "score", "threshold": 90.0,
            "within_threshold": score >= 90.0}


def main():
    force = "--force" in sys.argv

    s, login = req("POST", "/auth/login", body={"username": "admin", "password": "admin"})
    if s != 200:
        sys.exit(f"login failed: {s} {login}")
    tok = login["access_token"]

    s, existing = req("GET", "/devices?page_size=1", tok)
    if existing.get("total", 0) > 0 and not force:
        sys.exit(f"DB already has {existing['total']} devices — pass --force to seed anyway")

    # ---- 0. a few tester users (so checkouts/tests have varied actors)
    print("creating tester users...")
    for u in CHECKOUT_USERS:
        s2, r2 = req("POST", "/users", tok,
                     {"username": u, "password": f"{u}-pass", "role": "tester"})
        if s2 not in (200, 201):
            print(f"  WARN user {u}: {s2} {r2}")
    tester_toks = {}
    for u in CHECKOUT_USERS:
        s2, r2 = req("POST", "/auth/login", body={"username": u, "password": f"{u}-pass"})
        if s2 == 200:
            tester_toks[u] = r2["access_token"]
    print(f"  testers: {len(tester_toks)}")

    # ---- 1. devices
    print("creating 100 devices...")
    bodies = [make_device(i) for i in range(100)]
    results = parallel("POST", lambda i: "/devices", bodies, tok)
    ok = [r for r in results if r[1] == 201]
    bad = [r for r in results if r[1] != 201]
    if bad:
        sys.exit(f"device failures: {bad[:3]}")
    devices = [r[2] for r in sorted(results, key=lambda r: r[0])]
    print(f"  devices: {len(devices)} created")

    # ---- 1b. transition checked_out devices (stamps checked_out_by/at)
    checkout_ids = [i for i in range(100) if STATUS_MIX[i] == "checked_out"]
    for n, i in enumerate(checkout_ids):
        tok_n = list(tester_toks.values())[n % len(tester_toks)]
        req("PATCH", f"/devices/{devices[i]['id']}", tok_n, {"status": "checked_out"})
    print(f"  checkouts: {len(checkout_ids)} stamped")

    # ---- 2. software
    print("creating 50 software...")
    software_bodies = [
        {"name": name, "version": f"{random.randint(1, 3)}.{random.randint(0, 9)}.{random.randint(0, 9)}",
         "docs": f"https://software.example.com/{name}",
         "misc_data": {"category": cat, "description": f"{name} verification suite"}}
        for name, cat, _w in SOFTWARE
    ]
    results = parallel("POST", lambda i: "/software", software_bodies, tok)
    bad = [r for r in results if r[1] != 201]
    if bad:
        sys.exit(f"software failures: {bad[:3]}")
    software = [r[2] for r in sorted(results, key=lambda r: r[0])]
    print(f"  software: {len(software)} created")

    # ---- 3. software -> device targets (correlation core)
    print("assigning software targets...")
    eligible = [d for d in devices if d["status"] != "retired"]
    targets_by_software = {}
    target_bodies = []
    for software, (name, cat, _w) in zip(software, SOFTWARE):
        k = random.randint(5, 15)
        picked = random.sample(eligible, k)
        targets_by_software[software["id"]] = picked
        target_bodies.append((software["id"], {"device_ids": [d["id"] for d in picked]}))

    def put_targets(i_id):
        tid, body = i_id
        return req("PUT", f"/software/{tid}/targets", tok, body)
    with ThreadPoolExecutor(max_workers=8) as ex:
        tresults = list(ex.map(put_targets, target_bodies))
    bad = [r for r in tresults if r[0] != 200]
    if bad:
        sys.exit(f"target failures: {bad[:3]}")
    print(f"  targets: {sum(len(v) for v in targets_by_software.values())} software-device links")

    # ---- 4. tests (each references software + one of ITS target devices)
    print("creating 500 tests...")
    software_ids = [t["id"] for t in software]
    weights = [w for _n, _c, w in SOFTWARE]
    software_cat = {t["id"]: cat for t, (_n, cat, _w) in zip(software, SOFTWARE)}
    now = datetime.now(timezone.utc)
    test_bodies = []
    for _ in range(500):
        software_id = random.choices(software_ids, weights=weights, k=1)[0]
        dev = random.choice(targets_by_software[software_id])
        outcome = random.choices(["pass", "fail", "warn"], weights=[70, 20, 10], k=1)[0]
        # recency-biased timestamp over the last 180 days
        days_ago = int(random.expovariate(1 / 30))
        run_at = now - timedelta(days=min(days_ago, 180), hours=random.randint(0, 23),
                                 minutes=random.randint(0, 59))
        test_bodies.append({
            "software_id": software_id,
            "device_id": dev["id"],
            "outcome": outcome,
            "tag": random.choices(["adhoc", "acceptance", "end-to-end", "automated"],
                                  weights=[40, 40, 20, 30], k=1)[0],
            "misc_data": test_data(software_cat[software_id], outcome),
            "notes": random.choice(NOTES) if random.random() < 0.35 else None,
            "run_at": run_at.isoformat(),
        })
    # rotate the acting user so created_by varies (admin + testers)
    actor_toks = [tok] + list(tester_toks.values())
    n_chunks = len(actor_toks)
    chunk = (len(test_bodies) + n_chunks - 1) // n_chunks
    all_results = []
    for c in range(n_chunks):
        part = test_bodies[c * chunk:(c + 1) * chunk]
        if not part:
            continue
        all_results += parallel("POST", lambda i: "/tests", part, actor_toks[c])
    ok = [r for r in all_results if r[1] == 201]
    bad = [r for r in all_results if r[1] != 201]
    if bad:
        print(f"  WARN test failures: {bad[:3]}")
    print(f"  tests: {len(ok)} created")

    # ---- summary
    s, devs = req("GET", "/devices?page_size=1", tok)
    s, tls = req("GET", "/software?page_size=1", tok)
    s, tsts = req("GET", "/tests?page_size=1", tok)
    print("\n=== summary ===")
    print(f"devices: {devs['total']}   software: {tls['total']}   tests: {tsts['total']}")
    print("done.")


if __name__ == "__main__":
    main()
