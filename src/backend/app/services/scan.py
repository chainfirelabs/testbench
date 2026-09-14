"""Network scanning: determines whether a device is online/offline.

A device is considered **online** if ANY of the following probes succeeds
against ANY of its configured IPs (wan_ip and/or lan_ip):

- ICMP echo (ping)
- TCP connect to port 80  (HTTP)
- TCP connect to port 443 (HTTPS)
- TCP connect to port 23  (TELNET)
- TCP connect to port 22  (SSH)

On success, ``online_status`` is set to True and ``last_seen_online`` is
refreshed. On failure, ``online_status`` is set to False (the last known
``last_seen_online`` timestamp is preserved). ``last_scanned_at`` is stamped
whenever the device actually has an IP to probe; a device with no IP (or that
has never been probed) is considered "unknown" rather than offline.

A "large scan" (all devices) runs in a background thread pool and is guarded
by a lock so only one large scan runs at a time. A periodic scan runs on an
interval configurable via ``TB_SCAN_INTERVAL_MINUTES`` (0 disables it).
"""

import itertools
import logging
import os
import select as select_module
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal, utcnow
from ..models import Device
from .audit import log_action

logger = logging.getLogger(__name__)

# ICMP echo identity. `ident` marks the packets as ours; `seq` distinguishes one
# probe from another and must be unique across concurrent probes — see
# _check_icmp for why a shared value is not enough.
_ICMP_IDENT = os.getpid() & 0xFFFF
_icmp_seq = itertools.count(1)

# TCP services probed on every address, in the order the request listed them.
TCP_PROBES = (("http", 80), ("https", 443), ("telnet", 23), ("ssh", 22))


def _check_tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _icmp_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack(f">{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return (~s) & 0xFFFF


def _build_echo_request(ident: int, seq: int) -> bytes:
    header = struct.pack("!BBHHH", 8, 0, 0, ident, seq)  # type=8 (echo req), code=0
    payload = b"testbench-ping"
    chk = _icmp_checksum(header + payload)
    return struct.pack("!BBHHH", 8, 0, chk, ident, seq) + payload


def _is_echo_reply(packet: bytes, ident: int, seq: int) -> bool:
    """Is this raw-socket read our echo reply?

    A raw IPv4 socket hands back the IP header along with the ICMP message, so
    the ICMP fields do not start at offset 0. The header is not a fixed 20
    bytes either — IHL counts 32-bit words — so the offset has to be read off
    the packet rather than assumed.
    """
    if len(packet) < 20:
        return False
    ihl = (packet[0] & 0x0F) * 4
    if ihl < 20 or len(packet) < ihl + 8:
        return False
    rtype, _code, _chk, rident, rseq = struct.unpack("!BBHHH", packet[ihl:ihl + 8])
    return rtype == 0 and rident == ident and rseq == seq  # type 0 = echo reply


def _check_icmp(host: str, timeout: float) -> bool:
    """ICMP echo probe. Uses a raw socket when permitted (root / CAP_NET_RAW),
    otherwise falls back to the UDP port-7 "dummy ICMP" trick which needs no
    privileges. Returns False when the probe cannot be performed."""
    try:
        target = socket.gethostbyname(host)
    except OSError:
        return False

    ident = _ICMP_IDENT
    # Unique per probe. A raw ICMP socket receives every echo reply the host
    # gets, not just the replies to what this socket sent, so with a shared
    # sequence number the concurrent probes of a large scan read each other's
    # replies and report devices online that never answered.
    seq = next(_icmp_seq) & 0xFFFF
    packet = _build_echo_request(ident, seq)

    # 1) raw socket (works when running as root, e.g. inside the container)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.getprotobyname("icmp")) as s:
            s.sendto(packet, (target, 0))
            deadline = time.monotonic() + timeout
            while True:
                # Budget the wait against the deadline: a reply meant for
                # another probe would otherwise restart the full timeout.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                s.settimeout(remaining)
                try:
                    data, addr = s.recvfrom(1500)
                except TimeoutError:
                    return False
                if addr[0] == target and _is_echo_reply(data, ident, seq):
                    return True
    except PermissionError:
        pass  # fall through to the unprivileged path
    except OSError:
        return False

    # 2) unprivileged "dummy ICMP" via UDP port 7
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(packet, (host, 7))
            ready, _, _ = select_module.select([s], [], [], timeout)
            return bool(ready)
    except OSError:
        return False


def _device_targets(db, device: Device) -> list[tuple[str, str]]:
    """Every address worth probing, as (role, ip) with role in {wan, lan}.

    The addresses are found by semantic role rather than by field name: an
    installation is free to call its management address `mgmt_ip`, and what
    makes it something to probe is the `scan_address_wan` role.

    Both addresses are always probed, and neither short-circuits the other: a
    device is regularly reachable on one and not the other, and knowing *which*
    one answered is the point of reporting them separately. A device that lists
    the same address as both is probed once.
    """
    from .device_schema import fields_for_device, role_map

    roles = role_map(field for field in fields_for_device(db, device) if field.visible)
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for role, semantic in (("wan", "scan_address_wan"), ("lan", "scan_address_lan")):
        field = roles.get(semantic)
        if field is None:
            continue
        ip = str((device._data or {}).get(field.key) or "").strip()
        if ip and ip not in seen:
            seen.add(ip)
            targets.append((role, ip))
    return targets


def scan_device(db, device: Device, timeout: float | None = None) -> dict:
    """Probe a device and update its online_status / last_seen_online.

    Returns a result dict::

        {"ips": [...],
         "checks": {ip: {probe: bool}},
         "probes": [{"role": "wan", "ip": ..., "online": bool, "services": [...]}],
         "online": bool}

    ``checks`` is the raw per-probe grid (kept for the audit log); ``probes``
    is the same information keyed by address and labelled with its role, which
    is what the UI reports. The caller is responsible for committing.
    """
    timeout = timeout if timeout is not None else settings.scan_timeout_seconds
    targets = _device_targets(db, device)
    checks: dict[str, dict[str, bool]] = {}
    probes: list[dict] = []
    online = False
    for role, ip in targets:
        result = {"icmp": _check_icmp(ip, timeout)}
        for name, port in TCP_PROBES:
            result[name] = _check_tcp(ip, port, timeout)
        checks[ip] = result
        services = [name for name, ok in result.items() if ok]
        probes.append({"role": role, "ip": ip, "online": bool(services), "services": services})
        if services:
            online = True
    if not targets:
        return {"ips": [], "checks": {}, "probes": [], "online": False}
    device.online_status = online
    if online:
        device.last_seen_online = utcnow()
    # Only mark as scanned when there was actually something to probe; a
    # device with no IP stays in the "unknown" state.
    if targets:
        device.last_scanned_at = utcnow()
    return {"ips": [ip for _, ip in targets], "checks": checks, "probes": probes, "online": online}


def _scan_one(device_id: str, timeout: float) -> dict | None:
    """Scan a single device using its own DB session (thread-safe)."""
    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if device is None:
            return None
        result = scan_device(db, device, timeout)
        db.commit()
        return result
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Scan failed for device %s", device_id)
        return None
    finally:
        db.close()


class ScanManager:
    """Tracks and runs the "large" all-devices scan."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.scanned = 0
        self.total = 0
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.last_summary: dict | None = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "scanned": self.scanned,
            "total": self.total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "interval_minutes": settings.scan_interval_minutes,
        }

    def start_all(self) -> bool:
        """Start a large scan in the background. Returns False if one is already running."""
        with self._lock:
            if self.running:
                return False
            self.running = True
            self.scanned = 0
            self.total = 0
            self.started_at = utcnow()
            self.finished_at = None
        threading.Thread(target=self._run_all, name="device-scan-all", daemon=True).start()
        return True

    def _run_all(self) -> None:
        online = offline = failed = no_ip = 0
        try:
            db = SessionLocal()
            try:
                ids = list(db.scalars(select(Device.id)).all())
            finally:
                db.close()
            self.total = len(ids)
            logger.info("Large scan started: %d devices", len(ids))
            with ThreadPoolExecutor(max_workers=max(1, settings.scan_concurrency)) as pool:
                futures = [pool.submit(_scan_one, i, settings.scan_timeout_seconds) for i in ids]
                for fut in as_completed(futures):
                    result = fut.result()
                    self.scanned += 1
                    if result is None:
                        failed += 1
                    elif result["online"]:
                        online += 1
                    elif not result["ips"]:
                        # Neither a WAN nor a LAN address: counted apart from
                        # "offline", which would claim a check that never ran.
                        no_ip += 1
                    else:
                        offline += 1
            self.last_summary = {
                "total": self.total,
                "online": online,
                "offline": offline,
                "no_ip": no_ip,
                "failed": failed,
            }
            db = SessionLocal()
            try:
                log_action(db, None, "device.scan_all", "device", None, self.last_summary)
                db.commit()
            finally:
                db.close()
            logger.info("Large scan finished: %s", self.last_summary)
        except Exception:  # noqa: BLE001
            logger.exception("Large scan failed")
        finally:
            self.running = False
            self.finished_at = utcnow()


manager = ScanManager()


def start_interval_loop() -> None:
    """Start the periodic background scan (no-op if interval <= 0)."""
    minutes = settings.scan_interval_minutes
    if minutes <= 0:
        logger.info("Periodic device scan disabled (TB_SCAN_INTERVAL_MINUTES=%s)", minutes)
        return

    def loop() -> None:
        while True:
            time.sleep(max(1, minutes) * 60)
            try:
                if not manager.running:
                    manager.start_all()
            except Exception:  # noqa: BLE001
                logger.exception("Periodic scan trigger failed")

    threading.Thread(target=loop, name="scan-interval", daemon=True).start()
    logger.info("Periodic device scan enabled: every %d minute(s)", minutes)
