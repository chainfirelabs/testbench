# TestBench — MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM client answer
questions about the fleet: which devices exist, what software targets them, and
what has actually been tested against what.

**Read-only.** Every tool queries data; none modifies inventory or test results.

It is deployed as a separate service through the TestBench Helm chart.
It holds no database credentials and owns no state: it reaches the fleet only
through the REST API, so it can be started, stopped and upgraded on its own.

```
┌──────────────┐  stdio / streamable HTTP  ┌────────────────────┐  HTTP   ┌──────────┐
│  MCP client  │◄─────────────────────────►│  MCP server        │◄───────►│ /api/v1  │
│  (Claude)    │                           │  (this container)  │  +JWT   │ FastAPI  │
└──────────────┘                           └────────────────────┘         └──────────┘
```

The server uses the REST API, so the application's permissions and response
contracts apply to MCP queries as well.

## Installation

Enable `mcp.enabled=true` in the [TestBench Helm chart](../../helm/testbench/README.md).
The chart configures read-only API access and exposes the server through an
internal ClusterIP Service on port 8003. For external clients, configure an
ingress or gateway with TLS and set `mcp.authToken.existingSecret` to a Secret
containing the inbound bearer token (`mcp.authToken.key` defaults to
`auth-token`).

For local use, install the package from the repository root:

```bash
pip install -e src/mcp-server
export TB_MCP_API_BASE=http://localhost:8001/api/v1
export TB_MCP_API_TOKEN=tb_your_prefix_your_secret
TB_MCP_TRANSPORT=stdio testbench-mcp
```

Mint the outbound API key under **Profile → API keys**. The outbound
`TB_MCP_API_TOKEN` authenticates this server to TestBench; the inbound
`TB_MCP_AUTH_TOKEN` authenticates HTTP clients to this server. With an empty
inbound token, the HTTP endpoint allows unauthenticated callers. Stdio uses
the parent process connection and does not require an inbound token.

## Connecting a client

**Claude Code**

```bash
claude mcp add --transport http testbench https://mcp.example.com/mcp
```

With `TB_MCP_AUTH_TOKEN` set, pass the key as a header:

```bash
claude mcp add --transport http testbench https://mcp.example.com/mcp \
  --header "Authorization: Bearer $TB_MCP_AUTH_TOKEN"
```

**Claude Desktop** (`claude_desktop_config.json`) — install the Python package
first, then configure stdio with the absolute path to `testbench-mcp`:

```json
{
  "mcpServers": {
    "testbench": {
      "command": "/absolute/path/to/testbench-mcp",
      "env": {
        "TB_MCP_TRANSPORT": "stdio",
        "TB_MCP_API_BASE": "https://testbench.example.com/api/v1",
        "TB_MCP_API_TOKEN": "tb_your_prefix_your_secret"
      }
    }
  }
}
```

The server needs an outbound API key. Sign in to the web app, open **Profile → API keys**,
create one and copy it — the full key is shown once, at creation.

The stdio configuration does not need `TB_MCP_AUTH_TOKEN`: that key authenticates callers
arriving over HTTP, and on stdio the caller is the parent process that started
the server. It is ignored there.

## Tools

Task-shaped, not endpoint-shaped — these do not mirror the 30 REST paths. Every
one takes **human identifiers** (`dev-0042`, `nmap`); UUIDs stay inside the
server, and every response echoes back what it resolved to.

| Tool | Answers |
|---|---|
| `find_devices(search?, device_type?, status?, location?, limit?)` | Which devices match this? |
| `find_due_devices(due_within_days=3, include_overdue=true, ...)` | What is overdue or due back soon? |
| `get_device(identifier)` | Everything about one device, plus its recent tests |
| `list_device_actions(identifier)` | What automation can run against it, and why the rest cannot |
| `find_software(search?, latest_only?, limit?)` | Which software matches this? |
| `get_software(identifier, version?)` | Everything about one piece of software |
| `list_software_versions(name)` | What versions of this exist? |
| `list_devices_tested_with(software, version?, limit?)` | What has it **been run against**? |
| `list_vendor_supported_devices(software, version?, ...)` | What does the **vendor claim**? |
| `list_software_tested_on(device, limit?)` | What has been run **against this device**? |
| `find_tests(device?, software?, outcome?, tag?, since?, until?, ...)` | Individual test runs |
| `search_audit(user?, action?, entity?, since?, until?, ...)` | Who changed what (admin only) |

### The distinction the tool names are protecting

*"What devices does nmap work against?"* has two correct answers in this data
model, and they routinely disagree:

- **`list_vendor_supported_devices`** — the vendor's compatibility list. A
  claim. Possibly hardware nobody here owns, and carrying no test evidence.
- **`list_devices_tested_with`** — devices it has actually been run against,
  with pass/fail/warn counts. Absence means *untested*, not *unsupported*.

A single `get_supported_devices` would have to pick one, and whichever it picked
would be wrong half the time — silently, with the model presenting it
confidently. So there are two tools, they are never merged, and the server's
instructions tell the model to ask which sense is meant or return both labelled.

### Result sizing

Every list returns `{items, total, returned}`, plus `truncated: true` and a note
saying what to do about it when it was cut short. Two ceilings apply: `limit`
(default 50, max 500) and a 128 KB response budget, which trims the tail. Rows
are projected down to the fields that answer fleet questions — `GET
/tests/export` is ~350 KB for 500 rows and no tool will ever hand that back.

The budget is sized to hold a full `max_limit` page of device rows. If it is
set below that, it — not `limit` — decides how many rows a caller gets, which
is the failure mode it exists to make visible.

`max_limit` must also stay at or below the API's own `page_size` cap of 1000
(`api/devices.py`): past that the API returns 422 rather than a smaller page,
so a sweep fails instead of taking one more call.

Rows cost roughly 48–65 tokens each, and once fetched they stay in the model's
context for the rest of the conversation. That, not server time, is what a big
`limit` spends — the API answers in ~15 ms whether it returns 50 rows or 1000.
Prefer a filter: `find_devices(online=True, make="Dell")` answers in three rows
and a `total`, where a sweep would spend thousands.

`find_devices` also paginates: it takes an `offset` and returns `next_offset`,
counted from the rows that survived trimming rather than from the page that was
requested. Sweeping a fleet is "call, follow `next_offset`, stop when
`truncated` is absent", and a trimmed page costs a round trip instead of losing
the rows it dropped. Prefer a filter to a sweep where one exists — `total` on a
filtered call already counts every match, so a count is one call.

## Resources and prompts

| Resource | Contents |
|---|---|
| `testbench://schema` | Enum values and field lists per entity, plus this installation's device types and the fields each carries. Reading this is why the model does not guess `status="braken"` and get an empty page back — and why it filters by the device type keys that actually exist. Device fields are configurable, so re-read it if a field key stops resolving. |
| `testbench://software-catalog` | Every name with its current version — small, and it makes name resolution nearly free. |

Devices (100+) and tests (500+) are deliberately *not* resources; those are tool
calls. Prompts: `triage_failing_tests`, `device_checkout_report`,
`software_coverage_gap`.

## Configuration

All environment variables are prefixed `TB_MCP_`:

| Variable | Default | Description |
|---|---|---|
| `TB_MCP_API_BASE` | `http://backend:8000/api/v1` | TestBench REST API, including the version prefix |
| `TB_MCP_API_TOKEN` | **required** | *Outbound.* A TestBench API key (`tb_<prefix>_<secret>`), minted under Profile → API keys |
| `TB_MCP_AUTH_TOKEN` | empty | *Inbound.* Bearer key(s) a caller must present to this server, comma-separated. Empty leaves the endpoint open |
| `TB_MCP_TRANSPORT` | `streamable-http` | `stdio`, `streamable-http` or `sse` |
| `TB_MCP_HOST` / `TB_MCP_PORT` | `0.0.0.0` / `8003` | HTTP transports only |
| `TB_MCP_MOUNT_PATH` | `/mcp` | Path the endpoint is served under |
| `TB_MCP_DEFAULT_LIMIT` / `TB_MCP_MAX_LIMIT` | `50` / `500` | Rows per result, and the ceiling `limit` is clamped to |
| `TB_MCP_MAX_RESPONSE_BYTES` | `131072` | Byte budget per response. Keep it above one full `MAX_LIMIT` page |
| `TB_MCP_ALLOWED_HOSTS` / `TB_MCP_ALLOWED_ORIGINS` | empty | Comma-separated allow-lists. Set both to enable DNS-rebinding protection |

### Which key should it read as?

A **`readonly`** key is the right choice. This server only reads, and the API
enforces that with `require_write` regardless of what the tools offer — so a
readonly key makes "read-only" a property of the deployment rather than a
promise made by this code. The one thing it gives up is `search_audit`, which
the API restricts to admins.

A key carries the role chosen when it was minted, capped at its owner's — so an
admin can mint a readonly key for this server without granting it anything the
account itself could do. The cap is re-applied on every request, so demoting
the owner weakens the key immediately, with nothing to redeploy here.

The key is checked at startup and the account and role it resolves to are
logged. A key the API *rejects* is fatal — that never fixes itself — while an
API that is merely unreachable is a warning, since the backend may still be
starting beside us. Keys do not refresh: when one expires or is revoked, mint a
replacement and restart the container.

Actions taken with a key are audited under the owner's username with the key's
id and label attached, so revoking one answers "what did it do?" and not only
"who owned it?". Nothing this server does mutates, so in practice only `export`
actions appear.

Reads are not audited by the backend; mutations and exports are audited.

## Current limitations

- All queries use the configured service account's effective API-key role.
  `search_audit` requires an admin key; other queries can use a readonly key.
- `list_software_tested_on` aggregates the test history returned by the device
  detail endpoint.
- `find_tests(since=, until=)` applies date filtering client-side over a bounded
  scan (`TB_MCP_MAX_SCAN_ROWS`, default 2000) and reports when that limit is hit.
