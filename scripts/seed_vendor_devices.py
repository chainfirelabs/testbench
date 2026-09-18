#!/usr/bin/env python3
"""Seed vendor devices: the hardware each software's vendor CLAIMS to support.

Deliberately separate from seed_dev_data.py, which creates devices, software and
tests. Vendor devices are claims on a compatibility list, not evidence and not
inventory, so most claims here point at make/model combos we actually own (that
is what makes them comparable against test history) and a minority point at
hardware nobody here has.

Run: python3 seed_vendor_devices.py [--force] [--count N]
"""
import json
import argparse
import os
import random
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("TB_SEED_API_BASE", "http://localhost:8001/api/v1")
random.seed(1337)  # reproducible dataset
DEFAULT_COUNT = 300


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


# Hardware nobody here owns: the rest of each vendor's line-up. A claim about
# one of these is the case where a compatibility list runs ahead of inventory.
UNOWNED = [
    ("Cisco", "ISR 4331", "17.9.4a"), ("Cisco", "ISR 4451", "17.9.4a"),
    ("Cisco", "C9300-24T", "17.12.1"), ("Cisco", "ASR 1002-HX", "17.9.4a"),
    ("Cisco", "Catalyst 9200", "17.12.1"),
    ("Juniper", "SRX345", "21.4R3.7"), ("Juniper", "QFX5120-48Y", "22.2R1"),
    ("Arista", "7060X5-64S", "4.31.2F"), ("Arista", "7010T-48", "4.28.1.7"),
    ("HPE", "Aruba 2930F-24G", "16.11.0024"), ("HPE", "Aruba CX 8360", "10.13"),
    ("Dell", "PowerSwitch Z9332F", "10.5.4"), ("Dell", "N3248TE-ON", "6.5.36"),
    ("Ubiquiti", "EdgeRouter X", "4.4.5"), ("Ubiquiti", "USW-24-PoE", "6.5.59"),
    ("Fortinet", "FortiGate 200F", "7.4.2"), ("Fortinet", "FortiSwitch 148F", "7.2.5"),
    ("Palo Alto", "PA-440", "10.2.8"), ("Palo Alto", "PA-3220", "11.1.2"),
    ("MikroTik", "CRS354-48G", "7.15"), ("MikroTik", "hEX S", "7.13.5"),
    ("Netgear", "M4250-40G8XF", "13.0.4.9"), ("Netgear", "GS110TP", "10.3.2.72"),
]

ARCH_BY_MAKE = {
    "Cisco": ["x86_64", "ppc"], "Juniper": ["x86_64", "ppc"],
    "Arista": ["x86_64"], "HPE": ["arm64", "ppc"], "Dell": ["x86_64"],
    "Ubiquiti": ["mips", "arm64"], "Fortinet": ["x86_64", "arm64"],
    "Palo Alto": ["x86_64"], "MikroTik": ["arm64", "mips"], "Netgear": ["arm64", "mips"],
}

SUPPORT_MIX = (["supported"] * 60 + ["partial"] * 20 +
               ["unsupported"] * 10 + ["planned"] * 10)

SOURCES = [
    "https://software.example.com/{name}/compatibility",
    "Compatibility matrix 2026-Q1",
    "Compatibility matrix 2025-Q4",
    "{vendor} datasheet rev C",
    "Vendor support portal export",
]

NOTES = {
    "supported": ["fully qualified", "certified in vendor lab", None, None, None],
    "partial": ["reduced feature set", "no hardware offload on this model",
                "vendor lists caveats for older firmware", "throughput capped"],
    "unsupported": ["dropped after EOL", "vendor withdrew support in this release",
                    "known incompatibility"],
    "planned": ["listed for next release", "qualification in progress",
                "vendor roadmap item"],
}

IDENTITY_FIELDS = ("make", "model", "firmware_version",
                   "hardware_version", "architecture")


def match_key(v):
    """Mirror of the API's dedupe key, so we do not post collisions."""
    return "|".join((v.get(f) or "").strip().lower() for f in IDENTITY_FIELDS)


def make_claim(name, make, model, firmware, hardware=None, architecture=None):
    status = random.choice(SUPPORT_MIX)
    claim = {
        "make": make,
        "model": model,
        # Claims often lag or lead the firmware actually on the shelf.
        "firmware_version": firmware if random.random() < 0.6 else
            f"{random.randint(1, 12)}.{random.randint(0, 9)}.{random.randint(0, 9)}",
        "hardware_version": hardware if hardware is not None else f"Rev {random.choice('ABC')}",
        "architecture": architecture or random.choice(ARCH_BY_MAKE.get(make, ["x86_64"])),
        "support_status": status,
        "source": random.choice(SOURCES).format(name=name, vendor=make),
        "misc_data": {
            "listed_since": f"20{random.randint(22, 26)}-{random.randint(1, 12):02d}",
            "matrix_row": random.randint(1, 400),
            "vendor_verified": status == "supported" and random.random() < 0.8,
        },
    }
    note = random.choice(NOTES[status])
    if note:
        claim["notes"] = note
    return claim


def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed vendor compatibility with collapsed firmware groups and unique devices."
    )
    parser.add_argument("--url", default=BASE, help="TestBench API base URL (env: TB_SEED_API_BASE)")
    parser.add_argument("--api-key", default=os.environ.get("TB_SEED_API_KEY"),
                        help="API token (env: TB_SEED_API_KEY)")
    parser.add_argument("--force", action="store_true", help="add claims when claims already exist")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="vendor-device records to create")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    return args


def firmware_variant(value, offset):
    parts = str(value or "1.0.0").split(".")
    try:
        parts[-1] = str(int(parts[-1]) + offset)
        return ".".join(parts)
    except ValueError:
        return f"{value or '1.0'}-{offset}"


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

    s, softs = req("GET", "/software?page_size=500", tok)
    if s != 200:
        sys.exit(f"software fetch failed: {s} {softs}")
    software = softs["items"]
    existing = sum(sw.get("vendor_device_count") or 0 for sw in software)
    if existing and not args.force:
        sys.exit(f"DB already has {existing} vendor devices — pass --force to add more")

    s, devs = req("GET", "/devices?page_size=500", tok)
    if s != 200:
        sys.exit(f"device fetch failed: {s} {devs}")
    # Distinct hardware we actually own, so claims line up with test evidence.
    owned = sorted({(d["make"], d["model"], d.get("firmware_version") or "",
                     d.get("hardware_version") or "Rev A", d.get("architecture") or "x86_64")
                    for d in devs["items"] if d.get("make") and d.get("model")})
    print(f"software: {len(software)}   owned make/model combos: {len(owned)}")

    if not software:
        sys.exit("no software to attach vendor devices to — run seed_software.py first")
    if not owned:
        sys.exit("no inventory hardware to mirror — run seed_dev_data.py first")

    # Alternate grouped firmware records with unique hardware. Each grouped
    # pair shares make/model/hardware/architecture and differs only by firmware,
    # exactly the shape the Vendor Devices table collapses.
    bodies, seen_by_software, group_by_software = [], {}, {}
    attempts = 0
    while len(bodies) < args.count and attempts < args.count * 20:
        attempts += 1
        sw = software[len(bodies) % len(software)]
        seen = seen_by_software.setdefault(sw["id"], set())
        grouped_variant = (len(bodies) // len(software)) % 3 == 1 and sw["id"] in group_by_software
        if grouped_variant:
            make, model, fw, hardware, architecture = group_by_software[sw["id"]]
            fw = firmware_variant(fw, 1 + attempts % 3)
        elif random.random() < 0.7:
            make, model, fw, hardware, architecture = random.choice(owned)
            group_by_software[sw["id"]] = (make, model, fw, hardware, architecture)
        else:
            make, model, fw = random.choice(UNOWNED)
            hardware = f"Rev {random.choice('ABC')}"
            architecture = random.choice(ARCH_BY_MAKE.get(make, ["x86_64"]))
            group_by_software[sw["id"]] = (make, model, fw, hardware, architecture)
        claim = make_claim(sw["name"], make, model, fw, hardware, architecture)
        key = match_key(claim)
        if key in seen:
            continue
        seen.add(key)
        bodies.append((sw["id"], claim))

    if len(bodies) < args.count:
        sys.exit(f"could only build {len(bodies)} unique vendor devices out of {args.count} requested")

    print(f"posting {len(bodies)} vendor devices...")

    def post(item):
        sw_id, body = item
        return req("POST", f"/software/{sw_id}/vendor-devices", tok, body)

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(post, bodies))
    ok = [r for r in results if r[0] == 201]
    bad = [r for r in results if r[0] != 201]
    if bad:
        print(f"  WARN {len(bad)} failures: {bad[:3]}")
    print(f"  created: {len(ok)}")

    s, softs = req("GET", "/software?page_size=500", tok)
    total = sum(sw.get("vendor_device_count") or 0 for sw in softs["items"])
    with_claims = sum(1 for sw in softs["items"] if sw.get("vendor_device_count"))
    print("\n=== summary ===")
    print(f"vendor devices: {total} across {with_claims}/{len(softs['items'])} software")


if __name__ == "__main__":
    main()
