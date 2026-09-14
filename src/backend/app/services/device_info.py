"""Launch the short-lived agent that researches an online device."""

import http.client
import json
import os
import socket
import uuid
from urllib.parse import quote

from ..config import settings


class DockerError(RuntimeError):
    pass


class _UnixSocketConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


def device_value(device: object, field: str):
    """Read one device field by its stable key.

    The document first: an installation defines its own fields, and most of
    them exist only there. The named attributes are the fallback for the
    structural ones — `unique_id` above all — which are real columns.
    """
    document = getattr(device, "_data", None) or {}
    if field in document:
        return document[field]
    return getattr(device, field, None)


def missing_required_fields(device: object) -> list[str]:
    missing = []
    fields = list(dict.fromkeys(settings.device_info_required_field_list + [settings.device_info_url_field]))
    for field in fields:
        value = device_value(device, field)
        if value is None or value == "" or value == [] or value == {}:
            missing.append(field)
    return missing


def device_url(device: object) -> str:
    value = str(device_value(device, settings.device_info_url_field) or "").strip()
    if not value:
        return ""
    return value if "://" in value else f"http://{value}"


def render_prompt(device: object, inventory: dict) -> str:
    """Expand only documented tokens, leaving ordinary braces untouched."""
    return settings.device_info_prompt.replace("{device_url}", device_url(device)).replace(
        "{device_json}", json.dumps(inventory, sort_keys=True, default=str)
    )


def _docker_request(method: str, path: str, payload: dict | None = None) -> dict:
    connection = _UnixSocketConnection(settings.device_info_docker_socket)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
    except OSError as exc:
        raise DockerError(f"Cannot reach Docker at {settings.device_info_docker_socket}: {exc}") from exc
    finally:
        connection.close()
    data = json.loads(raw) if raw else {}
    if response.status >= 300:
        raise DockerError(data.get("message") or f"Docker returned HTTP {response.status}")
    return data


def launch_device_info(device: object, inventory: dict) -> str:
    prompt = render_prompt(device, inventory)
    env = [f"{name}={os.environ[name]}" for name in settings.device_info_forward_env_list if name in os.environ]
    name = f"testbench-device-info-{uuid.uuid4().hex[:12]}"
    host_config: dict[str, object] = {"AutoRemove": True}
    if settings.device_info_network_mode:
        host_config["NetworkMode"] = settings.device_info_network_mode
    created = _docker_request(
        "POST",
        f"/v1.41/containers/create?name={quote(name)}",
        {"Image": settings.device_info_image, "Cmd": [prompt], "Env": env, "HostConfig": host_config},
    )
    container_id = created["Id"]
    try:
        _docker_request("POST", f"/v1.41/containers/{container_id}/start")
    except Exception:
        try:
            _docker_request("DELETE", f"/v1.41/containers/{container_id}?force=true")
        except Exception:
            pass
        raise
    return container_id
