"""The enumerated values, and a check that a filter uses one of them.

These mirror the tuples in the backend models: DEVICE_STATUSES and
DEVICE_ARCHITECTURES (models/device.py), TEST_OUTCOMES / TEST_TAGS (models/test.py),
VENDOR_SUPPORT_STATUSES (models/vendor_device.py).

The list endpoints do not validate their filter values — `?status=braken`
returns an empty page, not a 422. Unchecked, that reaches the model as "there
are no broken devices", which is a wrong answer rather than an error. So the
check happens here, before the request goes out.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

DEVICE_STATUSES = ("available", "checked_out", "inventory", "missing", "broken")
DEVICE_ARCHITECTURES = (
    "x86_64", "x86", "mipsbe", "mipsel", "arm", "arm64", "aarch64",
    "ppc", "tilegx", "lexra_mips",
)
TEST_OUTCOMES = ("pass", "fail", "warn")
TEST_TAGS = ("adhoc", "acceptance", "end-to-end", "automated")
VENDOR_SUPPORT_STATUSES = ("supported", "partial", "unsupported", "planned")
AUDIT_ENTITY_TYPES = ("device", "software", "test", "user", "saved_filter", "audit_log", "auth")


def check(value: str | None, field: str, allowed: tuple[str, ...]) -> str | None:
    """Return `value` if it is one of `allowed`, else raise a readable ToolError.

    ToolError specifically: the SDK passes its message through to the model and
    withholds the text of any other exception.
    """
    if value is None:
        return None
    if value in allowed:
        return value
    folded = value.strip().lower().replace(" ", "_")
    if folded in allowed:
        return folded
    raise ToolError(
        f"'{value}' is not a valid {field}. Valid values: {', '.join(allowed)}."
    )
