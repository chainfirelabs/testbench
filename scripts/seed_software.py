#!/usr/bin/env python3
"""Seed the software catalogue: real-ish software, each with a version history.

Deliberately separate from seed_dev_data.py, which creates devices, software and
tests together. That one gives every software a single version, because what it
is really seeding is test correlation. This one seeds the *catalogue*: a couple
of dozen software, each with the two to five versions it has shipped, so the
version picker on the software page has something to show and
`GET /software/{id}/versions` returns more than one row.

Rows sharing a name are the versions of one software (unique on
lower(name) + version), so this creates the first version with POST /software
and every later one with POST /software/{id}/versions.

Run: python3 seed_software.py [--force] [--count N] [--components N]
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
random.seed(20260826)  # reproducible dataset


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
        # The API being unreachable is the ordinary failure here — the stack is
        # not up, or TB_SEED_API_BASE points somewhere wrong. Say so instead of
        # unwinding a traceback out of urllib.
        sys.exit(f"cannot reach the API at {BASE} ({e.reason}).\n"
                 f"Is the stack up? Override the address with TB_SEED_API_BASE.")


# (name, vendor, category, license, one-line description)
#
# Names are distinct from seed_dev_data.py's list on purpose: software names are
# unique case-insensitively, so overlapping would just collide. These read as
# things you would install *on* or point *at* fleet hardware, rather than the
# test suites that script seeds.
CATALOGUE = [
    ("RouterOS",          "MikroTik",    "firmware",   "proprietary", "Router firmware and management OS"),
    ("JunOS",             "Juniper",     "firmware",   "proprietary", "Carrier routing and switching OS"),
    ("IOS-XE",            "Cisco",       "firmware",   "proprietary", "Enterprise switching and routing OS"),
    ("ArubaOS-CX",        "HPE Aruba",   "firmware",   "proprietary", "Campus switching OS"),
    ("FortiOS",           "Fortinet",    "firmware",   "proprietary", "Firewall and SD-WAN OS"),
    ("PAN-OS",            "Palo Alto",   "firmware",   "proprietary", "Next-generation firewall OS"),
    ("OpenWrt",           "OpenWrt",     "firmware",   "GPL-2.0",     "Linux distribution for embedded routers"),
    ("VyOS",              "VyOS",        "firmware",   "GPL-2.0",     "Open-source router and firewall OS"),
    ("pfSense",           "Netgate",     "firmware",   "Apache-2.0",  "BSD firewall and router distribution"),
    ("Zabbix Agent",      "Zabbix",      "monitoring", "AGPL-3.0",    "Metrics collection agent"),
    ("Telegraf",          "InfluxData",  "monitoring", "MIT",         "Plugin-driven metrics collector"),
    ("Prometheus SNMP Exporter", "Prometheus", "monitoring", "Apache-2.0", "SNMP-to-Prometheus metrics bridge"),
    ("LibreNMS Poller",   "LibreNMS",    "monitoring", "GPL-3.0",     "Network polling and discovery daemon"),
    ("Netdata Agent",     "Netdata",     "monitoring", "GPL-3.0",     "Per-second host and network telemetry"),
    ("rsyslog",           "Adiscon",     "logging",    "GPL-3.0",     "Syslog forwarding daemon"),
    ("Fluent Bit",        "Fluent",      "logging",    "Apache-2.0",  "Lightweight log processor and forwarder"),
    ("Vector",            "Datadog",     "logging",    "MPL-2.0",     "Log and metric pipeline agent"),
    ("iperf3",            "ESnet",       "diagnostic", "BSD-3-Clause", "Throughput measurement tool"),
    ("Nmap",              "Nmap Project", "diagnostic", "NPSL",       "Port scanner and host discovery"),
    ("tcpdump",           "Tcpdump Group", "diagnostic", "BSD-3-Clause", "Packet capture utility"),
    ("MTR",               "BitWizard",   "diagnostic", "GPL-2.0",     "Combined traceroute and ping"),
    ("chrony",            "chrony",      "time",       "GPL-2.0",     "NTP client and server"),
    ("FreeRADIUS",        "FreeRADIUS",  "auth",       "GPL-2.0",     "RADIUS authentication server"),
    ("strongSwan",        "strongSwan",  "vpn",        "GPL-2.0",     "IPsec VPN daemon"),
    ("WireGuard",         "WireGuard",   "vpn",        "GPL-2.0",     "Modern VPN tunnel"),
    ("Suricata",          "OISF",        "security",   "GPL-2.0",     "IDS/IPS and network security monitor"),
    ("Kea DHCP",          "ISC",         "addressing", "MPL-2.0",     "DHCPv4/DHCPv6 server"),
    ("BIND 9",            "ISC",         "addressing", "MPL-2.0",     "Authoritative and recursive DNS server"),
    ("FRRouting",         "FRRouting",   "routing",    "GPL-2.0",     "BGP/OSPF/IS-IS routing suite"),
    ("Ansible Collection: network", "Red Hat", "automation", "GPL-3.0", "Network device automation modules"),
]


def version_history(name):
    """A plausible ascending version history for one software.

    Ascending matters: the API treats the most recently *created* row as the
    current version, not the highest version string. Seeding oldest-first is
    what makes `is_latest` land on the newest number rather than whichever
    happened to be posted last.
    """
    n = random.randint(2, 5)
    major = random.randint(1, 9)
    minor = random.randint(0, 6)
    patch = random.randint(0, 5)
    out = []
    for _ in range(n):
        out.append(f"{major}.{minor}.{patch}")
        roll = random.random()
        if roll < 0.15:          # a major release
            major += 1
            minor, patch = 0, 0
        elif roll < 0.55:        # a feature release
            minor += 1
            patch = 0
        else:                    # a patch release
            patch += 1
    return out


def parse_args():
    parser = argparse.ArgumentParser(description="Seed versioned software suites with components.")
    parser.add_argument("--url", default=BASE, help="TestBench API base URL (env: TB_SEED_API_BASE)")
    parser.add_argument("--api-key", default=os.environ.get("TB_SEED_API_KEY"),
                        help="API token (env: TB_SEED_API_KEY)")
    parser.add_argument("--force", action="store_true", help="add data when software already exists")
    parser.add_argument("--count", type=int, default=len(CATALOGUE), help="software suites to create")
    parser.add_argument("--components", type=int, default=3, help="components per software suite")
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be at least 1")
    if args.components < 0:
        parser.error("--components cannot be negative")
    return args


def catalogue_entries(count):
    """Use named real-world fixtures first, then deterministic unique suites."""
    entries = list(CATALOGUE[:count])
    while len(entries) < count:
        number = len(entries) + 1
        entries.append((
            f"TestBench Validation Suite {number:04d}",
            "ChainFire Labs",
            "validation",
            "MIT",
            f"Generated validation suite {number:04d}",
        ))
    return entries


def components_for(name, version, count):
    labels = ["Core", "Agent", "CLI", "Web Console", "API", "Reporting"]
    return [
        {"name": f"{name} {labels[i] if i < len(labels) else f'Component {i + 1}'}", "version": version}
        for i in range(count)
    ]


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

    s, page = req("GET", "/software?page_size=500", tok)
    if s != 200:
        sys.exit(f"software fetch failed: {s} {page}")
    before = page["items"]
    if before and not args.force:
        sys.exit(f"DB already has {len(before)} software rows — pass --force to add more")

    # Names are unique case-insensitively. Skipping here keeps a second --force
    # run from failing on every row it already created, so the script is
    # re-runnable rather than one-shot.
    taken = {sw["name"].casefold() for sw in before}
    requested = catalogue_entries(args.count)
    catalogue = [c for c in requested if c[0].casefold() not in taken]
    skipped = len(requested) - len(catalogue)
    if not catalogue:
        print(f"nothing to do: all {len(requested)} requested catalogue entries already exist")
        return
    if skipped:
        print(f"skipping {skipped} already present")

    plan = [(entry, version_history(entry[0])) for entry in catalogue]
    total_versions = sum(len(v) for _, v in plan)
    print(f"creating {len(plan)} software, {total_versions} versions...")

    def seed_one(item):
        (name, vendor, category, lic, desc), versions = item
        misc = {"vendor": vendor, "category": category, "license": lic, "description": desc}

        # The first version is a new software; the rest branch off it. Versions
        # of one software have to go in sequence — they share a uniqueness rule
        # — so the parallelism is across software, not within one.
        s, first = req("POST", "/software", tok, {
            "name": name,
            "version": versions[0],
            "misc_data": misc,
            "bundle_components": components_for(name, versions[0], args.components),
        })
        if s != 201:
            return name, 0, [f"{versions[0]}: {s} {first}"]

        made, errors = 1, []
        for v in versions[1:]:
            s, res = req("POST", f"/software/{first['id']}/versions", tok,
                         {"version": v, "copy_vendor_devices": True})
            if s == 201:
                made += 1
            else:
                errors.append(f"{v}: {s} {res}")
        return name, made, errors

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(seed_one, plan))

    created = sum(made for _, made, _ in results)
    failed = [(n, e) for n, _, e in results if e]
    for name, errs in failed[:5]:
        print(f"  WARN {name}: {errs[0]}")
    if len(failed) > 5:
        print(f"  WARN ... and {len(failed) - 5} more software with failures")

    s, page = req("GET", "/software?page_size=500", tok)
    if s != 200:
        sys.exit(f"software re-fetch failed: {s} {page}")
    items = page["items"]
    names = {sw["name"].casefold() for sw in items}
    multi = sum(1 for sw in items if sw.get("is_latest") and (sw.get("version_count") or 1) > 1)

    print("\n=== summary ===")
    print(f"versions created : {created}")
    print(f"software rows    : {len(items)} across {len(names)} names")
    print(f"names with >1 ver: {multi}")
    if failed:
        print(f"software with errors: {len(failed)}")


if __name__ == "__main__":
    main()
