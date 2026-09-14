"""Validate and resolve plugin settings at configuration boundaries."""

from typing import Any


def validate_plugin_configuration(plugin_id: str, configuration: dict) -> None:
    if not isinstance(configuration, dict):
        raise ValueError("Plugin configuration must be an object")
    rules = configuration.get("_rules", [])
    overrides = configuration.get("_device_overrides", {})
    if not isinstance(rules, list) or not isinstance(overrides, dict):
        raise ValueError("Plugin rules and device overrides must be collections")
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("field_key"), str):
            raise ValueError("Each plugin rule must name a field")
        if rule.get("operator", "equals") not in {"equals", "contains", "starts_with"}:
            raise ValueError("Plugin rule operator must be equals, contains, or starts_with")
        if not isinstance(rule.get("configuration", {}), dict):
            raise ValueError("Each plugin rule configuration must be an object")
        if any(str(key).startswith("_") for key in rule.get("configuration", {})):
            raise ValueError("Plugin rules cannot contain nested rules or device overrides")
        validate_plugin_configuration(plugin_id, rule.get("configuration", {}))
    for override in overrides.values():
        if not isinstance(override, dict):
            raise ValueError("Each device override must be an object")
        if any(str(key).startswith("_") for key in override):
            raise ValueError("Device overrides cannot contain rules or other device overrides")
        validate_plugin_configuration(plugin_id, override)
    port_keys = {
        "device-reboot": ("ssh_port",),
        "device-info-agent": ("http_port", "https_port"),
    }.get(plugin_id, ())
    for key in port_keys:
        port = configuration.get(key)
        if port is not None and (isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535):
            raise ValueError(f"{key.replace('_', ' ').title()} must be an integer from 1 to 65535 or null to inherit")
    if plugin_id != "device-reboot":
        return
    if configuration.get("method") not in (None, "ssh", "ai"):
        raise ValueError("Reboot method must be ssh, ai, or null to inherit")
    command = configuration.get("ssh_command")
    if command is not None and (not isinstance(command, str) or not command.strip()):
        raise ValueError("Reboot SSH command must not be empty; use null to inherit")


def _merge(base: dict, override: dict) -> dict:
    """Apply explicit values while null continues to mean inherit."""
    return {**base, **{key: value for key, value in override.items()
                     if not key.startswith("_") and value is not None}}


def resolve_plugin_configuration(configuration: dict, device: Any) -> tuple[dict, str]:
    """Resolve type, first matching ordered rule, then device configuration."""
    base = {key: value for key, value in (configuration or {}).items() if not key.startswith("_")}
    resolved = base
    source = "device type" if any(value is not None for value in base.values()) else "global"
    document = getattr(device, "data", None) or getattr(device, "_data", {}) or {}
    for rule in (configuration or {}).get("_rules", []):
        actual = document.get(rule.get("field_key"))
        expected = rule.get("value")
        left = str(actual or "")
        right = str(expected or "")
        if not rule.get("case_sensitive", False):
            left, right = left.casefold(), right.casefold()
        operator = rule.get("operator", "equals")
        matches = (left == right if operator == "equals" else
                   right in left if operator == "contains" else left.startswith(right))
        if matches:
            resolved = _merge(resolved, rule.get("configuration", {}))
            source = f"rule:{rule.get('id') or rule.get('field_key')}"
            break
    direct = (configuration or {}).get("_device_overrides", {}).get(device.id)
    if direct:
        resolved = _merge(resolved, direct)
        source = "device"
    return resolved, source
