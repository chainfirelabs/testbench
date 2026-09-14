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


MODULE_PATH = Path(__file__).parents[1] / "device_info_plugin" / "server.py"
SPEC = importlib.util.spec_from_file_location("testbench_device_info_server_output", MODULE_PATH)
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


def test_device_urls_apply_port_overrides_and_preserve_paths():
    assert server._device_urls("http://router.example/admin?tab=info", {
        "http_port": 8080, "https_port": 8443,
    }) == [
        "https://router.example:8443/admin?tab=info",
        "http://router.example:8080/admin?tab=info",
    ]


def test_device_urls_preserve_an_explicit_port_without_an_override():
    assert server._device_urls("router.example:8080") == [
        "https://router.example:8080", "http://router.example:8080",
    ]


def test_result_is_recovered_from_split_assistant_events():
    pieces = [
        "Findings complete\nTESTBENCH_RESULT_",
        'JSON={"findings":{"discovery_hardware":{"value":"R7450"}},',
        '"model":{"name":"qwen3.8-27b"},"artifacts":{}}\n',
    ]
    output = "\n".join(
        server.EVENT_PREFIX + json.dumps({"type": "assistant.delta", "text": piece})
        for piece in pieces
    )

    marker = server._result_marker(output)

    assert json.loads(marker)["findings"]["discovery_hardware"]["value"] == "R7450"


def test_saved_recipe_run_cannot_return_replacement_steps():
    result = {
        "artifacts": {
            "discovery_recipe": {"payload": {"steps": ["replacement"]}},
            "diagnostic": {"payload": {"value": "retained"}},
        }
    }

    protected = server._protect_saved_recipe(result, "saved_recipe")

    assert "discovery_recipe" not in protected["artifacts"]
    assert "diagnostic" in protected["artifacts"]


def test_failed_discovery_does_not_publish_blank_steps():
    result = {
        "findings": {},
        "artifacts": {
            "discovery_recipe": {
                "schema_version": 1,
                "payload": {"steps": []},
                "metadata": {},
            },
        },
    }

    normalized = server._normalize_result(result, "provider", "model")

    assert "discovery_recipe" not in normalized["artifacts"]


def test_successful_discovery_retains_nonempty_steps():
    result = {
        "findings": {},
        "artifacts": {
            "discovery_recipe": {
                "schema_version": 1,
                "payload": {"steps": ["opened status page"]},
                "metadata": {},
            },
        },
    }

    normalized = server._normalize_result(result, "provider", "model")

    assert normalized["artifacts"]["discovery_recipe"]["payload"]["steps"] == [
        "opened status page"
    ]


def test_populated_mac_roles_are_not_requested_for_discovery():
    roles = {
        "discovery_hardware": "R7450",
        "discovery_lan_mac": "94:a6:7e:e3:fb:73",
        "discovery_wan_mac": None,
    }

    requested = server._discovery_roles_to_request(roles)

    assert "discovery_hardware" in requested
    assert "discovery_lan_mac" not in requested
    assert "discovery_wan_mac" in requested


def test_model_cannot_overwrite_a_mac_that_already_exists():
    result = {
        "findings": {
            "discovery_lan_mac": {"value": "00:11:22:33:44:55"},
            "discovery_wan_mac": {"value": "66:77:88:99:aa:bb"},
        }
    }
    roles = {
        "discovery_lan_mac": "94:a6:7e:e3:fb:73",
        "discovery_wan_mac": "",
    }

    server._drop_existing_mac_findings(result, roles)

    assert "discovery_lan_mac" not in result["findings"]
    assert "discovery_wan_mac" in result["findings"]
