import json
import os
import socket
import time

import paramiko


def _ssh_online(host: str, ssh_port: int, timeout: float = 1) -> bool:
    """Probe one endpoint so a sequential multi-port scan cannot miss reboot."""
    try:
        with socket.create_connection((host, ssh_port), timeout=timeout):
            return True
    except OSError:
        return False


def _progress(message: str) -> None:
    print(message, flush=True)


def main() -> None:
    host = os.environ["TB_REBOOT_ADDRESS"]
    command = os.getenv("TB_REBOOT_SSH_COMMAND", "reboot")
    ssh_port = int(os.getenv("TB_REBOOT_SSH_PORT", "22"))
    if not command.strip():
        raise ValueError("TB_REBOOT_SSH_COMMAND must not be empty")
    started = time.monotonic()
    _progress(f"Connecting to SSH on {host}:{ssh_port}…")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        host,
        port=ssh_port,
        username=os.environ["TB_REBOOT_SSH_USERNAME"],
        password=os.environ["TB_REBOOT_SSH_PASSWORD"],
        look_for_keys=False,
        allow_agent=False,
        timeout=10,
    )
    _progress("SSH authentication succeeded.")
    ssh.exec_command(command, timeout=10)
    _progress(f"Reboot command sent: {command}")
    ssh.close()

    recovery_timeout = int(os.getenv("TB_REBOOT_RECOVERY_TIMEOUT_SECONDS", "300"))
    deadline = time.monotonic() + recovery_timeout
    offline_deadline = min(deadline, time.monotonic() + 60)
    saw_offline = False
    stable_online = 0
    _progress(f"Waiting for SSH on {host}:{ssh_port} to go offline…")
    while time.monotonic() < deadline:
        online = _ssh_online(host, ssh_port)
        if not saw_offline and not online:
            saw_offline = True
            _progress("SSH went offline; waiting for stable recovery…")
        elif not saw_offline and time.monotonic() >= offline_deadline:
            raise RuntimeError("Reboot command was sent, but SSH did not go offline within 60 seconds")
        if saw_offline:
            stable_online = stable_online + 1 if online else 0
        if stable_online >= 2:
            _progress("SSH is reachable again; reboot verified.")
            print("TESTBENCH_REBOOT_JSON=" + json.dumps({
                "method": "ssh", "verified_online": True,
                "successful_steps": [
                    {"step": 1, "action": "connect", "target": f"SSH on {host}:{ssh_port}",
                     "outcome": "Authenticated successfully", "yielded_fields": []},
                    {"step": 2, "action": "execute", "target": command,
                     "outcome": "Reboot command issued", "yielded_fields": []},
                    {"step": 3, "action": "verify", "target": host,
                     "outcome": "Device went offline", "yielded_fields": []},
                    {"step": 4, "action": "verify", "target": host,
                     "outcome": "Device returned online", "yielded_fields": []},
                ],
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }), flush=True)
            return
        time.sleep(1)
    raise RuntimeError("Device did not go offline and return before the recovery timeout")
