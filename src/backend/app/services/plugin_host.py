"""Discovery and authenticated HTTP transport for optional plugin services."""

import logging
import re
import threading
import time
from threading import RLock

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,99}$")


class PluginRegistry:
    def __init__(self):
        self._lock = RLock()
        self._manifests: dict[str, dict] = {}
        self._errors: dict[str, str] = {}

    def refresh(self) -> None:
        manifests: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for configured_id, url in settings.plugin_endpoints.items():
            try:
                if not PLUGIN_ID_RE.fullmatch(configured_id):
                    raise ValueError("invalid plugin id")
                response = httpx.get(
                    f"{url}/plugin/v1/manifest",
                    headers=self.headers(configured_id),
                    timeout=settings.plugin_timeout_seconds,
                )
                response.raise_for_status()
                manifest = response.json()
                if manifest.get("id") != configured_id:
                    raise ValueError(f"manifest id {manifest.get('id')!r} does not match")
                if int(manifest.get("protocol_version", 0)) != 1:
                    raise ValueError("unsupported protocol version")
                actions = manifest.get("actions", [])
                if not isinstance(actions, list):
                    raise ValueError("actions must be a list")
                manifest["endpoint"] = url
                manifests[configured_id] = manifest
            except Exception as exc:  # noqa: BLE001
                errors[configured_id] = str(exc)
                logger.warning("Plugin %s is unavailable: %s", configured_id, exc)
        with self._lock:
            self._manifests = manifests
            self._errors = errors

    def headers(self, plugin_id: str) -> dict[str, str]:
        return {
            "X-TestBench-Plugin": plugin_id,
            "X-TestBench-Plugin-Secret": settings.plugin_shared_secret,
        }

    def manifests(self) -> list[dict]:
        with self._lock:
            return [dict(value) for value in self._manifests.values()]

    def manifest(self, plugin_id: str) -> dict | None:
        with self._lock:
            value = self._manifests.get(plugin_id)
            return dict(value) if value else None

    def status(self) -> list[dict]:
        configured = settings.plugin_endpoints
        with self._lock:
            return [
                {
                    "id": plugin_id,
                    "endpoint": url,
                    "state": "loaded" if plugin_id in self._manifests else "unhealthy",
                    "error": self._errors.get(plugin_id),
                    "version": self._manifests.get(plugin_id, {}).get("version"),
                }
                for plugin_id, url in configured.items()
            ]

    def request(self, plugin_id: str, method: str, path: str, json: dict | None = None) -> dict:
        manifest = self.manifest(plugin_id)
        if manifest is None:
            raise LookupError("Plugin is not loaded")
        response = httpx.request(
            method,
            f"{manifest['endpoint']}{path}",
            headers=self.headers(plugin_id),
            json=json,
            timeout=settings.plugin_timeout_seconds,
        )
        response.raise_for_status()
        return response.json() if response.content else {}


registry = PluginRegistry()


def start_refresh_loop(after_refresh=None) -> None:
    if not settings.plugin_endpoints:
        return

    def loop():
        while True:
            time.sleep(30)
            registry.refresh()
            if after_refresh is not None:
                try:
                    after_refresh()
                except Exception:  # noqa: BLE001 — discovery retries on the next interval.
                    logger.exception("Could not reconcile installed plugin fields")

    threading.Thread(target=loop, name="plugin-refresh", daemon=True).start()
