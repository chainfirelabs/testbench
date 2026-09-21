"""The FastMCP server instance and its instructions block."""

from __future__ import annotations

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from . import __version__
from .auth import StaticTokenVerifier
from .client import ApiClient
from .config import settings

INSTRUCTIONS = """
TestBench — a fleet of network devices, the software that targets them,
and the test results that tie the two together. This server is read-only: it
can find and describe things, and cannot change anything.

TERMINOLOGY — read this first.
"Tool" in this server always means an MCP tool, i.e. one of the callables listed
below. It never means a row in TestBench. The domain entity — nmap,
iperf3, and so on — is called SOFTWARE. This entity used to be called a Tool,
and the old word survives where you may see it: migration filenames such as
`0006_drop_tool_targets` and `0009_tool_name_ci`, `tool_targets` in older
sections of the project spec, and a `tool_name` column in any CSV exported
before the rename. All of those refer to what is now Software.

THE ENTITIES.
- Device: a physical unit, addressed by its `unique_id` (e.g. "dev-0042").
  Statuses: available, checked_out, inventory, missing, broken. `online` is
  separate and comes from network scanning, not from status.
- Software: a name plus a version. Several versions of one name form a version
  group; a bare name always means the CURRENT version (the highest numeric
  version; request a specific version explicitly when needed).
- Test: evidence that a piece of software was run against a device, with an
  outcome of pass, fail or warn and a tag of adhoc, acceptance, end-to-end or automated.
- Vendor device: a hardware entry on a vendor's compatibility list for a piece
  of software. A CLAIM, not evidence, and not necessarily hardware we own.

THE AMBIGUITY THAT MATTERS MOST.
"What devices does nmap work against?" has two correct and different answers,
and they routinely disagree:
  * `list_vendor_supported_devices` — what the VENDOR CLAIMS supports it. These
    may be devices nobody here owns, and carry no test evidence.
  * `list_devices_tested_with` — devices it has ACTUALLY BEEN RUN against, with
    outcome counts. Absence here means untested, not unsupported.
Never merge the two into a single "supported devices" answer. When a question
is ambiguous, either ask which sense is meant, or return both clearly labelled.
The same split applies in reverse for a device: `list_software_tested_on` is the
evidence sense, and `find_vendor_devices` is the claim sense — it searches every
software's compatibility list at once, which is the only way to answer "does
anything claim to support this hardware?" without naming a software first.

IDENTIFIERS.
Use human identifiers everywhere: "dev-0042", "nmap". Do not construct UUIDs —
the tools do not need them and a guessed UUID will simply miss. Every response
echoes back what it resolved to; check that echo, especially the version, before
reporting a result.

RESULTS.
Lists are capped and return `{items, total, returned}`. If `truncated` is true
you are seeing part of the answer — say so rather than presenting it as the
whole. Read `testbench://schema` before filtering by a status, outcome or
tag; guessing at those values wastes a round trip.

CHECKOUT DEADLINES.
For overdue devices or devices due soon, call `find_due_devices`. Its default
window is the next 3 days and it includes already-overdue checkouts. Use
`include_overdue=False` when the user asks only about upcoming returns.
""".strip()

def _auth() -> dict:
    """The SDK's auth wiring, or nothing at all when no key is configured.

    `token_verifier` is what actually rejects a caller; `auth` has to be
    present alongside it or the constructor refuses the pair. Both halves are
    omitted entirely when TB_MCP_AUTH_TOKEN is unset, which leaves the endpoint
    open — the startup banner warns about that.

    `resource_server_url=None` is the load-bearing part of this settings
    object. Set it, and the SDK publishes OAuth protected-resource metadata and
    points the 401's WWW-Authenticate header at it, sending an OAuth-capable
    client off to discover an authorization server that does not exist. Left
    None, an unauthenticated request gets a plain 401 and the client falls back
    to the key it was configured with. `issuer_url` is required by the model
    but unused on this path: it is only read when building the OAuth endpoints,
    which need an `auth_server_provider` we do not pass.
    """
    if not settings.auth_required:
        return {}
    return {
        "token_verifier": StaticTokenVerifier(settings.auth_token_list),
        "auth": AuthSettings(
            issuer_url=f"http://localhost:{settings.port}",
            resource_server_url=None,
        ),
    }


# Named `server`, not `mcp`: the SDK's own package is `mcp`, and a module-level
# `mcp` here would shadow it for anything else in this file.
server = MCPServer(
    "testbench",
    title="TestBench",
    version=__version__,
    instructions=INSTRUCTIONS,
    **_auth(),
)

_client: ApiClient | None = None


def api() -> ApiClient:
    """The shared API client, created on first use inside the running loop."""
    global _client
    if _client is None:
        _client = ApiClient()
    return _client
