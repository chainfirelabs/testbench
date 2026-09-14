import importlib.util
import json
import sys
import types
from pathlib import Path


class _App:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: lambda function: function


fastapi = types.ModuleType("fastapi")
fastapi.FastAPI = lambda **_kwargs: _App()
fastapi.Header = lambda default=None: default
fastapi.HTTPException = type("HTTPException", (Exception,), {})
sys.modules.setdefault("fastapi", fastapi)
sys.modules.setdefault("httpx", types.ModuleType("httpx"))
kubernetes = types.ModuleType("kubernetes")
kubernetes.client = types.SimpleNamespace()
kubernetes.config = types.SimpleNamespace()
sys.modules.setdefault("kubernetes", kubernetes)


MODULE_PATH = Path(__file__).parents[1] / "reboot_plugin" / "server.py"
SPEC = importlib.util.spec_from_file_location("testbench_reboot_server_output", MODULE_PATH)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


def test_assistant_output_is_not_dropped_after_500_events():
    events = [
        {"type": "assistant.delta", "at": str(index), "text": f"message-{index}\n"}
        for index in range(600)
    ]
    output = "\n".join(server.EVENT_PREFIX + json.dumps(event) for event in events)

    parsed = server._worker_events(output)
    rendered = server._display_worker_output(output, parsed)

    assert len(parsed) == 600
    assert "message-0" in rendered
    assert "message-599" in rendered


def test_result_is_recovered_from_split_assistant_events():
    pieces = [
        "Reboot complete\nTESTBENCH_REBOOT_",
        'JSON={"method":"ai","verified_online":true,',
        '"model":{"name":"qwen3.8-27b"},"successful_steps":[]}\n',
    ]
    output = "\n".join(
        server.EVENT_PREFIX + json.dumps({"type": "assistant.delta", "text": piece})
        for piece in pieces
    )

    marker = server._result_marker(output)

    assert json.loads(marker)["verified_online"] is True


def test_reboot_settings_inheritance_and_explicit_methods(monkeypatch):
    monkeypatch.setenv('TB_REBOOT_METHOD', 'ai')
    monkeypatch.setenv('TB_REBOOT_SSH_COMMAND', 'sudo -n reboot')
    monkeypatch.setenv('TB_REBOOT_SSH_PORT', '2222')
    assert server._reboot_settings({}) == ('ai', 'sudo -n reboot', 2222)
    assert server._reboot_settings({'_plugin_configuration': {'method': 'ssh'}}) == ('ssh', 'sudo -n reboot', 2222)
    monkeypatch.setenv('TB_REBOOT_METHOD', 'ssh')
    assert server._reboot_settings({'_plugin_configuration': {'method': 'ai', 'ssh_command': '/sbin/reboot', 'ssh_port': 2200}}) == ('ai', '/sbin/reboot', 2200)
    assert server._reboot_settings({'_plugin_configuration': {'method': None, 'ssh_command': None, 'ssh_port': None}}) == ('ssh', 'sudo -n reboot', 2222)


def test_reboot_settings_reject_invalid_values():
    import pytest
    for configuration in ({'method': 'auto'}, {'ssh_command': '  '}, {'ssh_command': 42}, {'ssh_port': 0}, {'ssh_port': True}):
        with pytest.raises(ValueError):
            server._reboot_settings({'_plugin_configuration': configuration})
