#!/usr/bin/env python3
"""Seed TestBench with correlated dev data:
100 devices, 50 component software suites, 300 vendor devices, and 500 tests.

Tests reference real software, optional suite components, and inventory devices.
Vendor compatibility includes matching inventory hardware, collapsed firmware
groups, and vendor-only devices.
Run: python3 seed_dev_data.py [--force] [--devices N] [--software N]
       [--tests N] [--vendor-devices N] [--components N]
"""
import json
import argparse
import os
import random
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("TB_SEED_API_BASE", "http://localhost:8001/api/v1")
random.seed(42)  # reproducible dataset
DEFAULT_DEVICES = 100
DEFAULT_SOFTWARE = 50
DEFAULT_TESTS = 500
DEFAULT_VENDOR_DEVICES = 300

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


def load_all(path, token, page_size=1000):
    """Read a complete paginated collection without imposing a seed-size cap."""
    items, page = [], 1
    while True:
        separator = "&" if "?" in path else "?"
        status, result = req("GET", f"{path}{separator}page={page}&page_size={page_size}", token)
        if status != 200:
            sys.exit(f"fetch failed for {path}: {status} {result}")
        items.extend(result["items"])
        if len(items) >= result["total"]:
            return items
        page += 1

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
STATUS_MIX = (["available"] * 55 + ["checked_out"] * 12 + ["inventory"] * 18 +
              ["missing"] * 7 + ["broken"] * 8)


def make_device(i):
    make = random.choice(list(VENDORS))
    models, fw = VENDORS[make]
    status = STATUS_MIX[i % len(STATUS_MIX)]
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
        "online_status": status in ("available", "checked_out") and random.random() < 0.9,
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


def parse_args():
    parser = argparse.ArgumentParser(description="Seed correlated devices, component suites, and tests.")
    parser.add_argument("--url", default=BASE, help="TestBench API base URL (env: TB_SEED_API_BASE)")
    parser.add_argument("--api-key", default=os.environ.get("TB_SEED_API_KEY"),
                        help="API token (env: TB_SEED_API_KEY)")
    parser.add_argument("--force", action="store_true", help="add data when devices already exist")
    parser.add_argument("--devices", type=int, default=DEFAULT_DEVICES)
    parser.add_argument("--software", type=int, default=DEFAULT_SOFTWARE)
    parser.add_argument("--tests", type=int, default=DEFAULT_TESTS)
    parser.add_argument("--vendor-devices", type=int, default=DEFAULT_VENDOR_DEVICES)
    parser.add_argument("--components", type=int, default=3, help="components per software suite")
    args = parser.parse_args()
    if args.devices < 1 or args.tests < 1 or args.vendor_devices < 1:
        parser.error("--devices, --tests, and --vendor-devices must be at least 1")
    if args.software < 1:
        parser.error("--software must be at least 1")
    if args.components < 0:
        parser.error("--components cannot be negative")
    return args


def software_catalogue(count):
    """Use realistic fixtures first, then deterministic synthetic suites."""
    entries = list(SOFTWARE[:count])
    while len(entries) < count:
        number = len(entries) + 1
        entries.append((f"validation-suite-{number:04d}", "software", 2))
    return entries

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
    global BASE
    args = parse_args()
    BASE = args.url.rstrip("/")
    if not BASE.endswith("/api/v1"):
        BASE += "/api/v1"

    if args.api_key:
        tok = args.api_key
    else:
        s, login = req("POST", "/auth/login", body={"username": "admin", "password": "admin"})
        if s != 200:
            sys.exit(f"login failed: {s} {login}")
        tok = login["access_token"]

    # ---- 1. devices
    existing_devices = load_all("/devices", tok)
    existing_by_uid = {item["unique_id"].casefold(): item for item in existing_devices}
    planned = [make_device(i) for i in range(args.devices)]
    bodies = [body for body in planned if body["unique_id"].casefold() not in existing_by_uid]
    print(f"ensuring {args.devices} devices ({len(bodies)} to create, {args.devices - len(bodies)} reused)...")
    results = parallel("POST", lambda i: "/devices", bodies, tok)
    ok = [r for r in results if r[1] == 201]
    bad = [r for r in results if r[1] != 201]
    if bad:
        sys.exit(f"device failures: {bad[:3]}")
    created_devices = [r[2] for r in sorted(results, key=lambda r: r[0])]
    by_uid = {**existing_by_uid, **{item["unique_id"].casefold(): item for item in created_devices}}
    devices = [by_uid[body["unique_id"].casefold()] for body in planned]
    print(f"  devices: {len(created_devices)} created, {len(devices) - len(created_devices)} reused")

    # ---- 1b. transition checked_out devices (stamps checked_out_by/at)
    checkout_ids = [i for i in range(args.devices) if STATUS_MIX[i % len(STATUS_MIX)] == "checked_out"]
    for i in checkout_ids:
        req("PATCH", f"/devices/{devices[i]['id']}", tok, {"status": "checked_out"})
    print(f"  checkouts: {len(checkout_ids)} stamped")

    # ---- 2. software
    selected_software = software_catalogue(args.software)
    print(f"ensuring {args.software} software suites...")
    software_bodies = [
        {"name": name, "version": f"{random.randint(1, 3)}.{random.randint(0, 9)}.{random.randint(0, 9)}",
         "docs": f"https://software.example.com/{name}",
         "misc_data": {"category": cat, "description": f"{name} verification suite"},
         "bundle_components": [
             {"name": f"{name} {label}", "version": "1.0"}
             for label in ["Core", "Agent", "CLI", "API", "Reporting"][:args.components]
         ]}
        for name, cat, _w in selected_software
    ]
    existing_software = load_all("/software", tok)
    latest_by_name = {}
    for item in existing_software:
        key = item["name"].casefold()
        if item.get("is_latest") or key not in latest_by_name:
            latest_by_name[key] = item
    missing_software = [body for body in software_bodies if body["name"].casefold() not in latest_by_name]
    results = parallel("POST", lambda i: "/software", missing_software, tok)
    bad = [r for r in results if r[1] != 201]
    if bad:
        sys.exit(f"software failures: {bad[:3]}")
    created_software = [r[2] for r in sorted(results, key=lambda r: r[0])]
    latest_by_name.update({item["name"].casefold(): item for item in created_software})
    software = []
    for body in software_bodies:
        suite = latest_by_name[body["name"].casefold()]
        existing_components = suite.get("bundle_components") or []
        known = {(item["name"].casefold(), str(item.get("version") or "")) for item in existing_components}
        additions = [item for item in body["bundle_components"]
                     if (item["name"].casefold(), str(item.get("version") or "")) not in known]
        if additions:
            status, updated = req("PATCH", f"/software/{suite['id']}", tok, {
                "bundle_components": [*existing_components, *additions],
            })
            if status != 200:
                sys.exit(f"component update failed for {suite['name']}: {status} {updated}")
            suite = updated
        software.append(suite)
    print(f"  software: {len(created_software)} created, {len(software) - len(created_software)} reused")

    # ---- 3. vendor compatibility (correlation core)
    print(f"creating {args.vendor_devices} vendor devices...")
    eligible = [d for d in devices if d["status"] not in ("missing", "broken")]
    targets_by_software = {}
    for suite, (name, cat, _w) in zip(software, selected_software):
        k = min(len(eligible), random.randint(5, 15))
        picked = random.sample(eligible, k)
        targets_by_software[suite["id"]] = picked

    vendor_bodies = []
    group_by_software = {}
    seen_by_software = {}
    while len(vendor_bodies) < args.vendor_devices:
        suite = software[len(vendor_bodies) % len(software)]
        suite_id = suite["id"]
        seen = seen_by_software.setdefault(suite_id, set())
        cycle = (len(vendor_bodies) // len(software)) % 3
        if cycle == 1 and suite_id in group_by_software:
            device = group_by_software[suite_id]
            firmware = f"{device.get('firmware_version') or '1.0'}.{1 + len(vendor_bodies) % 4}"
            make, model = device["make"], device["model"]
        elif cycle == 2:
            device = random.choice(targets_by_software[suite_id])
            make = device["make"]
            model = f"{device['model']} Vendor-Only-{len(vendor_bodies) + 1}"
            firmware = device.get("firmware_version") or "1.0"
        else:
            device = random.choice(targets_by_software[suite_id])
            group_by_software[suite_id] = device
            make, model = device["make"], device["model"]
            firmware = device.get("firmware_version") or "1.0"
        key = (make.casefold(), model.casefold(), str(device.get("hardware_version") or "").casefold(), firmware.casefold())
        if key in seen:
            firmware = f"{firmware}.{len(seen) + 1}"
            key = (*key[:-1], firmware.casefold())
        seen.add(key)
        vendor_bodies.append((suite_id, {
            "make": make,
            "model": model,
            "firmware_version": firmware,
            "hardware_version": device.get("hardware_version"),
            "architecture": device.get("architecture"),
            "support_status": random.choices(
                ["supported", "partial", "unsupported", "planned"],
                weights=[65, 20, 10, 5], k=1,
            )[0],
            "source": f"Seeded compatibility matrix for {suite['name']}",
        }))

    def post_vendor(item):
        suite_id, body = item
        return req("POST", f"/software/{suite_id}/vendor-devices", tok, body)
    with ThreadPoolExecutor(max_workers=8) as ex:
        tresults = list(ex.map(post_vendor, vendor_bodies))
    bad = [r for r in tresults if r[0] not in (201, 409)]
    if bad:
        sys.exit(f"vendor-device failures: {bad[:3]}")
    created_vendor = sum(1 for status, _ in tresults if status == 201)
    skipped_vendor = sum(1 for status, _ in tresults if status == 409)
    print(f"  vendor devices: {created_vendor} created, {skipped_vendor} already present")

    # ---- 4. tests (each references software + one of ITS target devices)
    print(f"creating {args.tests} tests...")
    software_ids = [t["id"] for t in software]
    weights = [w for _n, _c, w in selected_software]
    software_cat = {t["id"]: cat for t, (_n, cat, _w) in zip(software, selected_software)}
    software_by_id = {t["id"]: t for t in software}
    now = datetime.now(timezone.utc)
    test_bodies = []
    for _ in range(args.tests):
        software_id = random.choices(software_ids, weights=weights, k=1)[0]
        dev = random.choice(targets_by_software[software_id])
        outcome = random.choices(["pass", "fail", "warn"], weights=[70, 20, 10], k=1)[0]
        # recency-biased timestamp over the last 180 days
        days_ago = int(random.expovariate(1 / 30))
        run_at = now - timedelta(days=min(days_ago, 180), hours=random.randint(0, 23),
                                 minutes=random.randint(0, 59))
        body = {
            "software_id": software_id,
            "device_id": dev["id"],
            "outcome": outcome,
            "tag": random.choices(["adhoc", "acceptance", "end-to-end", "automated"],
                                  weights=[40, 40, 20, 30], k=1)[0],
            "misc_data": test_data(software_cat[software_id], outcome),
            "notes": random.choice(NOTES) if random.random() < 0.35 else None,
            "run_at": run_at.date().isoformat(),
        }
        components = software_by_id[software_id].get("bundle_components") or []
        if components and random.random() < 0.75:
            body["component_id"] = random.choice(components)["id"]
        test_bodies.append(body)
    all_results = parallel("POST", lambda i: "/tests", test_bodies, tok)
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
