#!/usr/bin/env python3
"""Seed test runs against the devices, software and vendor claims already in the DB.

Deliberately separate from seed_dev_data.py, which creates devices, software and
tests in one pass and correlates a test with software targets it made up in the
same run. This one seeds *only* tests, against whatever is already there, and
correlates them with two things that script cannot see:

  - **Vendor claims.** Most runs land on hardware the software's vendor says it
    supports (from seed_vendor_devices.py), and the outcome follows the claim:
    `supported` mostly passes, `partial` warns more, `unsupported` fails more.
    A minority of runs deliberately land on hardware nobody claimed, which is
    where a fleet's test history disagrees with its compatibility matrix.
  - **Versions.** Runs spread across a software's versions (from
    seed_software.py), weighted so older versions carry more accumulated history
    and the newest has only a run or two. The API snapshots the version onto the
    test, so the evidence stays attached to the build that produced it.

Every test is created by `admin`: local user creation was removed from the API
(POST /users is 405), so there are no other local accounts to act as.

Run: python3 seed_tests.py [--force] [--count N]
"""
import json
import argparse
import os
import random
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("TB_SEED_API_BASE", "http://localhost:8001/api/v1")
random.seed(8675309)  # reproducible dataset

DEFAULT_COUNT = 600


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
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach the API at {BASE} ({e.reason}).\n"
                 f"Is the stack up? Override the address with TB_SEED_API_BASE.")


# How a vendor's claim about a device tends to play out when you actually run
# against it. Unclaimed hardware is the worst bet, which is the whole point of
# tracking claims separately from evidence.
OUTCOME_WEIGHTS = {
    "supported":   {"pass": 88, "warn":  8, "fail":  4},
    "partial":     {"pass": 55, "warn": 30, "fail": 15},
    "planned":     {"pass": 40, "warn": 25, "fail": 35},
    "unsupported": {"pass": 20, "warn": 25, "fail": 55},
    None:          {"pass": 60, "warn": 20, "fail": 20},  # nobody claimed it
}

NOTES_PASS = [
    "Clean run, no regressions.",
    "Matches the previous release's numbers.",
    "Re-run after the firmware bump; still green.",
]
NOTES_WARN = [
    "Intermittent timeout on the first attempt, passed on retry.",
    "Throughput ~15% below the vendor figure.",
    "Completed, but logged a deprecation warning.",
    "Slower than the same test on comparable hardware.",
]
NOTES_FAIL = [
    "Crashed partway through; core dumped.",
    "Did not come back after the reboot step.",
    "Authentication rejected against this firmware.",
    "Unsupported instruction on this architecture.",
    "Ran out of memory on a device this size.",
]


def test_data(category, outcome):
    """Plausible per-run metrics. Shape follows the software's category so the
    JSON on a test looks like it came from that kind of tool."""
    d = {"duration_s": round(random.uniform(1.5, 240.0), 1)}
    if category in ("diagnostic", "monitoring"):
        d["throughput_mbps"] = round(random.uniform(40, 960), 1)
        d["packet_loss_pct"] = round(random.uniform(0, 0.4 if outcome == "pass" else 12), 2)
        d["latency_ms"] = round(random.uniform(0.3, 4 if outcome == "pass" else 90), 2)
    elif category == "firmware":
        d["image_size_mb"] = round(random.uniform(9, 180), 1)
        d["reboot_s"] = random.randint(25, 210)
        d["checksum_ok"] = outcome != "fail"
    elif category in ("logging", "time"):
        d["events_processed"] = random.randint(500, 250_000)
        d["drift_ms"] = round(random.uniform(0, 3 if outcome == "pass" else 400), 1)
    elif category in ("auth", "vpn", "security"):
        d["handshakes"] = random.randint(10, 5_000)
        d["rejected"] = 0 if outcome == "pass" else random.randint(1, 300)
    else:
        d["records"] = random.randint(5, 20_000)
    if outcome != "pass":
        d["errors"] = random.randint(1, 40)
    return d


def parse_args():
    parser = argparse.ArgumentParser(description="Seed component-aware test history.")
    parser.add_argument("--url", default=BASE, help="TestBench API base URL (env: TB_SEED_API_BASE)")
    parser.add_argument("--api-key", default=os.environ.get("TB_SEED_API_KEY"),
                        help="API token (env: TB_SEED_API_KEY)")
    parser.add_argument("--force", action="store_true", help="add tests when tests already exist")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="test records to create")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    return args


def main():
    global BASE
    args = parse_args()
    BASE = args.url.rstrip("/")
    if not BASE.endswith("/api/v1"):
        BASE += "/api/v1"
    count = args.count

    if args.api_key:
        tok = args.api_key
    else:
        s, login = req("POST", "/auth/login", body={"username": "admin", "password": "admin"})
        if s != 200:
            sys.exit(f"login failed: {s} {login}")
        tok = login["access_token"]

    s, page = req("GET", "/tests?page_size=1", tok)
    if s != 200:
        sys.exit(f"test fetch failed: {s} {page}")
    if page.get("total") and not args.force:
        sys.exit(f"DB already has {page['total']} tests — pass --force to add more")

    s, devs = req("GET", "/devices?page_size=500", tok)
    if s != 200:
        sys.exit(f"device fetch failed: {s} {devs}")
    devices = devs["items"]
    if not devices:
        sys.exit("no devices to test against — run seed_dev_data.py first")

    s, softs = req("GET", "/software?page_size=500", tok)
    if s != 200:
        sys.exit(f"software fetch failed: {s} {softs}")
    software = softs["items"]
    if not software:
        sys.exit("no software to test — run seed_software.py or seed_dev_data.py first")

    # Every version row of one name is a separate row; group them so a run can
    # be aimed at a specific build.
    versions_by_name = defaultdict(list)
    for sw in software:
        versions_by_name[sw["name"]].append(sw)
    for rows in versions_by_name.values():
        rows.sort(key=lambda r: r["created_at"])  # oldest first

    # Which physical devices match a given make/model claim.
    by_make_model = defaultdict(list)
    for d in devices:
        if d.get("make") and d.get("model"):
            by_make_model[(d["make"].casefold(), d["model"].casefold())].append(d)

    print(f"devices: {len(devices)}   software: {len(software)} rows / "
          f"{len(versions_by_name)} names")
    print("fetching vendor claims...")

    def claims_for(sw):
        s, page = req("GET", f"/software/{sw['id']}/vendor-devices?page_size=500", tok)
        return sw["id"], (page["items"] if s == 200 else [])

    with ThreadPoolExecutor(max_workers=8) as ex:
        claim_pages = dict(ex.map(claims_for, software))

    # For each software row: the devices we own that its vendor named, carrying
    # the support status that was claimed for them.
    claimed = {}
    for sw in software:
        hits = []
        for c in claim_pages.get(sw["id"], []):
            if not c.get("make") or not c.get("model"):
                continue
            for d in by_make_model.get((c["make"].casefold(), c["model"].casefold()), []):
                hits.append((d, c.get("support_status")))
        claimed[sw["id"]] = hits
    with_claims = sum(1 for v in claimed.values() if v)
    total_claims = sum(len(v) for v in claimed.values())
    print(f"  {total_claims} claim/device matches across {with_claims}/{len(software)} software rows")

    names = list(versions_by_name)
    now = datetime.now(timezone.utc)
    bodies = []
    for _ in range(count):
        rows = versions_by_name[random.choice(names)]
        # Older versions have had longer to accumulate runs. Weight rises toward
        # the front of the (oldest-first) list, so the newest build is the one
        # with barely any history — which is what a real fleet looks like.
        weights = [len(rows) - i for i in range(len(rows))]
        sw = random.choices(rows, weights=weights, k=1)[0]

        pool = claimed.get(sw["id"]) or []
        if pool and random.random() < 0.75:
            device, status = random.choice(pool)
        else:
            # Beyond the compatibility matrix: nobody claimed this combination.
            device, status = random.choice(devices), None

        w = OUTCOME_WEIGHTS[status if status in OUTCOME_WEIGHTS else None]
        outcome = random.choices(list(w), weights=list(w.values()), k=1)[0]

        # Recency-biased over the last ~18 months, so charts have a tail.
        days_ago = min(int(random.expovariate(1 / 90)), 540)
        run_at = now - timedelta(days=days_ago, hours=random.randint(0, 23),
                                 minutes=random.randint(0, 59))

        notes = None
        if outcome == "fail":
            notes = random.choice(NOTES_FAIL)
        elif outcome == "warn":
            notes = random.choice(NOTES_WARN)
        elif random.random() < 0.20:
            notes = random.choice(NOTES_PASS)

        body = {
            "software_id": sw["id"],
            "device_id": device["id"],
            # Left unset on purpose: the API snapshots the software row's own
            # version, which is exactly the build this run exercised.
            "outcome": outcome,
            "tag": random.choices(["adhoc", "acceptance", "end-to-end", "automated"],
                                  weights=[40, 40, 20, 30], k=1)[0],
            "misc_data": test_data((sw.get("misc_data") or {}).get("category"), outcome),
            "notes": notes,
            "run_at": run_at.date().isoformat(),
        }
        components = sw.get("bundle_components") or []
        if components and random.random() < 0.75:
            component = random.choice(components)
            body["component_id"] = component["id"]
        bodies.append(body)

    print(f"creating {len(bodies)} tests...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda b: req("POST", "/tests", tok, b), bodies))
    ok = [r for r in results if r[0] == 201]
    bad = [r for r in results if r[0] != 201]
    for b in bad[:3]:
        print(f"  WARN {b[0]}: {str(b[1])[:120]}")
    if len(bad) > 3:
        print(f"  WARN ... and {len(bad) - 3} more failures")

    # Page through everything rather than reading one page: coverage counted off
    # a single 500-row page silently under-reports as soon as the table is
    # bigger than that, and a coverage figure that quietly shrinks is worse than
    # none at all.
    items, page_no, total = [], 1, None
    while True:
        s, page = req("GET", f"/tests?page={page_no}&page_size=500", tok)
        if s != 200:
            break
        total = page.get("total", total)
        items += page["items"]
        if len(items) >= (total or 0) or not page["items"]:
            break
        page_no += 1

    by_outcome = defaultdict(int)
    for t in items:
        by_outcome[t["outcome"]] += 1
    tested_devices = len({t["device_id"] for t in items})
    tested_software = len({t["software_id"] for t in items})

    print("\n=== summary ===")
    print(f"created        : {len(ok)}" + (f"  ({len(bad)} failed)" if bad else ""))
    print(f"tests in DB    : {total if total is not None else len(items)}")
    if items:
        breakdown = "  ".join(f"{k}={by_outcome[k]}" for k in ("pass", "warn", "fail") if by_outcome[k])
        print(f"outcomes       : {breakdown}")
    print(f"devices covered: {tested_devices}/{len(devices)}")
    print(f"software rows  : {tested_software}/{len(software)}")


if __name__ == "__main__":
    main()
