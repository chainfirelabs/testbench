"""Entrypoint. Selects the transport and starts the server.

`stdio` for a local desktop client — simplest, no network exposure.
`streamable-http` for shared or remote use, which is what the compose file runs.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server.transport_security import TransportSecuritySettings

from .auth import fingerprint
from .client import ApiClient, ApiError
from .config import settings

# Importing these registers the tools, resources and prompts on the server.
from . import prompts, resources, tools  # noqa: F401
from .server import server

TRANSPORTS = ("stdio", "streamable-http", "sse")


def _security() -> TransportSecuritySettings | None:
    """Host/Origin allow-lists, or None to leave the SDK's default (off).

    Left off, the endpoint accepts any Host — fine behind a compose network or
    a reverse proxy, not fine if it is reachable from a browser.
    """
    hosts, origins = settings.allowed_host_list, settings.allowed_origin_list
    if not hosts and not origins:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def _preflight(log: logging.Logger) -> None:
    """Check the API key against the API before serving.

    A revoked or mistyped key otherwise surfaces only as every tool call
    failing — the same failure mode the container healthcheck exists to prevent
    for `TB_MCP_API_BASE`. A rejected key is fatal, because it will never fix
    itself; an unreachable API is not, because the backend may still be coming
    up beside us.

    Runs in its own loop with its own client, so the shared client in
    `server.api()` is still created inside the loop the server actually runs.
    """

    async def check() -> dict:
        client = ApiClient()
        try:
            return await client.whoami()
        finally:
            await client.aclose()

    try:
        who = asyncio.run(check())
    except ApiError as exc:
        if exc.status in (401, 403):
            raise SystemExit(
                f"Startup check failed for TB_MCP_API_TOKEN "
                f"({settings.token_prefix}). {exc}"
            )
        log.warning("Could not verify the API key yet: %s. Starting anyway.", exc)
        return
    except Exception as exc:  # noqa: BLE001 — DNS, refused connection, backend still starting
        log.warning(
            "Could not reach the TestBench API at %s yet: %s. Starting anyway.",
            settings.api_base, exc,
        )
        return

    log.info(
        "API key %s authenticates as '%s' with role '%s'",
        settings.token_prefix, who.get("username"), who.get("role"),
    )
    if who.get("role") != "admin":
        # Not a problem — a readonly key is the recommended choice — but worth
        # saying once, so a permission error from that one tool is unsurprising.
        log.info("search_audit requires an admin key; it will return a permission error.")


def main() -> None:
    # stderr, never stdout: on the stdio transport stdout is the protocol channel
    # and a stray log line corrupts the stream.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs a line per request at INFO. One tool call is several requests,
    # and none of them is news unless it failed.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("tb_mcp")

    transport = settings.transport.strip().lower()
    if transport not in TRANSPORTS:
        raise SystemExit(
            f"TB_MCP_TRANSPORT must be one of {', '.join(TRANSPORTS)}, got '{settings.transport}'"
        )
    if not settings.api_token:
        raise SystemExit(
            "No API key configured. Mint one in TestBench under "
            "Profile -> API keys and set TB_MCP_API_TOKEN."
        )
    if not settings.token_is_api_key:
        # Any bearer token the API accepts will work, so this is a warning and
        # not a refusal — but a JWT expires in hours and cannot be refreshed
        # here, which is almost never what someone meant to configure.
        log.warning(
            "TB_MCP_API_TOKEN does not look like a TestBench API key "
            "(tb_<prefix>_<secret>); passing it through as a bearer token."
        )
    _preflight(log)

    if transport == "stdio":
        log.info("TestBench MCP (read-only) on stdio, API at %s", settings.api_base)
        server.run(transport="stdio")
        return

    # Who may call us, as opposed to who we call. On stdio there are no
    # callers to authenticate — the client is the parent process — so the
    # setting is only meaningful here, on the HTTP transports.
    if settings.auth_required:
        log.info(
            "Callers must present Authorization: Bearer <key>; %d key(s) accepted: %s",
            len(settings.auth_token_list),
            ", ".join(fingerprint(t) for t in settings.auth_token_list),
        )
    else:
        log.warning(
            "TB_MCP_AUTH_TOKEN is not set: %s is UNAUTHENTICATED. Anyone who can "
            "reach it reads the fleet with the role of API key %s. Set it to a "
            "shared secret unless every client that can reach this port is trusted.",
            settings.mount_path, settings.token_prefix,
        )

    log.info(
        "TestBench MCP (read-only) on %s at http://%s:%s%s, API at %s",
        transport, settings.host, settings.port, settings.mount_path, settings.api_base,
    )
    options = {
        "host": settings.host,
        "port": settings.port,
        "transport_security": _security(),
    }
    if transport == "streamable-http":
        options["streamable_http_path"] = settings.mount_path
    else:
        options["sse_path"] = settings.mount_path
    server.run(transport=transport, **options)


if __name__ == "__main__":
    main()
