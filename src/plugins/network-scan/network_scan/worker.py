import concurrent.futures
import os
import socket
import struct
import time
import httpx


def _checksum(data: bytes) -> int:
    if len(data) % 2: data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return (~total) & 0xFFFF


def _icmp(host: str, timeout: float) -> bool:
    packet_id = os.getpid() & 0xFFFF
    header = struct.pack("!BBHHH", 8, 0, 0, packet_id, 1)
    payload = b"testbench-scan"
    packet = struct.pack("!BBHHH", 8, 0, _checksum(header + payload), packet_id, 1) + payload
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP) as sock:
            sock.settimeout(timeout)
            sock.sendto(packet, (host, 0))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                reply = sock.recv(65535)
                offset = 20 if reply and reply[0] >> 4 == 4 else 0
                if len(reply) >= offset + 8 and reply[offset] == 0: return True
    except (OSError, TimeoutError):
        pass
    return False


def probe(entity: dict, timeout: float) -> dict:
    starting = entity.get("_scan_addresses", {})
    roles = entity.get("_plugin_roles", {})
    addresses = {
        role: value for role, value in roles.items()
        if role in {"scan_address_wan", "scan_address_lan"} and value
    }
    probes, online = [], False
    label = entity.get("unique_id") or entity.get("id") or "unknown device"
    for role, address in addresses.items():
        print(f"Scanning {label} — {role.removeprefix('scan_address_').upper()} {address}", flush=True)
        services = []
        if _icmp(str(address), timeout): services.append("icmp")
        for name, port in (("http", 80), ("https", 443), ("telnet", 23), ("ssh", 22)):
            try:
                with socket.create_connection((address, port), timeout=timeout): services.append(name)
            except OSError: pass
        probes.append({"role": role.removeprefix("scan_address_"), "ip": address, "online": bool(services), "services": services})
        result = ", ".join(services) if services else "no ICMP or configured TCP services responded"
        print(f"Result {label} — {address}: {result}", flush=True)
        online = online or bool(services)
    if not addresses: print(f"Skipped {label}: no LAN or WAN scan address", flush=True)
    return {"device_id": entity["id"], "starting_addresses": starting, "probed": bool(addresses), "online": online, "probes": probes}


def main():
    run_id, base = os.environ["TB_SCAN_RUN_ID"], os.environ["TB_SCAN_CONTROLLER_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['TB_SCAN_RUN_TOKEN']}"}
    entities = httpx.get(f"{base}/worker/v1/runs/{run_id}", headers=headers, timeout=30).json()["entities"]
    print(f"Starting scan of {len(entities)} device(s)", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.getenv("TB_SCAN_CONCURRENCY", "10"))) as pool:
        items = list(pool.map(lambda item: probe(item, float(os.getenv("TB_SCAN_TIMEOUT_SECONDS", "2"))), entities))
    online_count = sum(bool(item["online"]) for item in items)
    print(f"Scan complete: {online_count}/{len(items)} device(s) online", flush=True)
    httpx.post(f"{base}/worker/v1/runs/{run_id}/complete", headers=headers, json={"items": items}, timeout=60).raise_for_status()
