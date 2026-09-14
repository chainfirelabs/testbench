"""Minimal status-change hook registry.

No built-in integrations ship (NetBird was stripped per spec §13 Q6).
Future hooks register via `register_hook`; failures are logged, never
block the status change.
"""

import logging
from typing import Protocol

from sqlalchemy.orm import Session

from ..models import Device, User

logger = logging.getLogger(__name__)


class DeviceStatusHook(Protocol):
    name: str

    def on_status_change(self, db: Session, device: Device, old: str, new: str, actor: User) -> None:
        ...


_hooks: list[DeviceStatusHook] = []


def register_hook(hook: DeviceStatusHook) -> None:
    _hooks.append(hook)


def emit_status_change(db: Session, device: Device, old: str, new: str, actor: User) -> dict:
    """Run all registered hooks. Returns {hook_name: error} for failures."""
    failures: dict[str, str] = {}
    for hook in _hooks:
        try:
            hook.on_status_change(db, device, old, new, actor)
        except Exception as exc:  # noqa: BLE001 - log-and-continue policy
            logger.exception("Hook %s failed", hook.name)
            failures[hook.name] = str(exc)
    return failures
