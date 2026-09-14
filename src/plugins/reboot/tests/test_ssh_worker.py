import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


SPEC = importlib.util.spec_from_file_location(
    "testbench_ssh_worker", Path(__file__).parents[1] / "reboot_plugin" / "worker.py"
)
worker = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"paramiko": MagicMock()}):
    SPEC.loader.exec_module(worker)


@pytest.mark.parametrize("command", [None, "sudo -n reboot", "sh -c 'sleep 1; reboot'"])
def test_executes_command_and_records_it_after_recovery(monkeypatch, capsys, command):
    monkeypatch.setenv("TB_REBOOT_ADDRESS", "device.example")
    monkeypatch.setenv("TB_REBOOT_SSH_USERNAME", "operator")
    monkeypatch.setenv("TB_REBOOT_SSH_PASSWORD", "test-password")
    monkeypatch.setenv("TB_REBOOT_SSH_PORT", "2222")
    monkeypatch.delenv("TB_REBOOT_SSH_COMMAND", raising=False)
    if command is not None:
        monkeypatch.setenv("TB_REBOOT_SSH_COMMAND", command)
    ssh = MagicMock()
    monkeypatch.setattr(worker.paramiko, "SSHClient", lambda: ssh)
    states = iter([True, False, True, True])
    monkeypatch.setattr(worker, "_ssh_online", lambda host, port: next(states))
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)

    worker.main()

    expected = command if command is not None else "reboot"
    ssh.exec_command.assert_called_once_with(expected, timeout=10)
    ssh.connect.assert_called_once_with(
        "device.example", port=2222, username="operator", password="test-password",
        look_for_keys=False, allow_agent=False, timeout=10,
    )
    ssh.close.assert_called_once()
    output = capsys.readouterr().out
    assert "Connecting to SSH on device.example:2222" in output
    assert "SSH went offline; waiting for stable recovery" in output
    assert "SSH is reachable again; reboot verified" in output
    result = json.loads(output.split("TESTBENCH_REBOOT_JSON=", 1)[1])
    assert result["method"] == "ssh"
    assert result["verified_online"] is True
    assert result["successful_steps"] == [
        {"step": 1, "action": "connect", "target": "SSH on device.example:2222",
         "outcome": "Authenticated successfully", "yielded_fields": []},
        {"step": 2, "action": "execute", "target": expected,
         "outcome": "Reboot command issued", "yielded_fields": []},
        {"step": 3, "action": "verify", "target": "device.example",
         "outcome": "Device went offline", "yielded_fields": []},
        {"step": 4, "action": "verify", "target": "device.example",
         "outcome": "Device returned online", "yielded_fields": []},
    ]


@pytest.mark.parametrize("command", ["", " \t\n"])
def test_rejects_empty_command_before_connecting(monkeypatch, command):
    monkeypatch.setenv("TB_REBOOT_ADDRESS", "device.example")
    monkeypatch.setenv("TB_REBOOT_SSH_COMMAND", command)
    ssh_factory = MagicMock()
    monkeypatch.setattr(worker.paramiko, "SSHClient", ssh_factory)
    with pytest.raises(ValueError, match="must not be empty"):
        worker.main()
    ssh_factory.assert_not_called()
