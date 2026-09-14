"""HTTP client for the TestBench REST API.

Topology A from MCP_SERVER.md §3: this server is a separate process that talks
to `/api/v1` over HTTP rather than to the ORM. The invariants that make the
system correct — unique-name 409s and their guidance, checkout stamping,
per-row import savepoints, role checks, audit logging — live in the routers, so
going through them buys exact behavioural parity, including error strings.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .config import settings

logger = logging.getLogger(__name__)


class ApiError(ToolError):
    """An API response the model should read and act on.

    The REST API already returns good, human-readable `detail` strings; §12 says
    to pass them through verbatim rather than wrapping them in a generic failure
    message, because they frequently say exactly what to do next.

    It subclasses the SDK's ToolError for that reason: a ToolError's message
    reaches the model, while any other exception is treated as a crash and its
    text is withheld.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotFound(ApiError):
    """A 404. Not an error at the tool boundary — callers turn it into a
    "no match" result plus near misses (§6)."""


class ApiClient:
    """Thin async wrapper around the REST API, authenticating with an API key.

    The credential is a TestBench API key (`tb_<prefix>_<secret>`) minted
    by a user under their profile and presented as a bearer token, exactly as a
    login token would be. It carries the role chosen when it was minted, capped
    at its owner's current role — so a readonly key reads and nothing more, and
    demoting its owner weakens it without anyone touching this container.

    Nothing here refreshes. A key is valid until it expires or its owner revokes
    it, and neither is a state this server can recover from on its own: a 401 is
    reported as something a human has to go and fix, not retried.
    """

    def __init__(self) -> None:
        # The credential is static, so it belongs on the client rather than
        # being rebuilt per request.
        self._client = httpx.AsyncClient(
            base_url=settings.api_base.rstrip("/"),
            timeout=settings.http_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.api_token}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- auth -------------------------------------------------------------

    async def whoami(self) -> dict[str, Any]:
        """The account and effective role the configured key resolves to.

        Called at startup so a revoked or mistyped key is one clear line in the
        log, rather than every tool call failing later for reasons the model
        then has to explain to the user.
        """
        resp = await self._client.get("/auth/me")
        if resp.is_success:
            return resp.json()
        raise _to_error(resp, "/auth/me")

    # -- requests ---------------------------------------------------------

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET `path`, returning parsed JSON. Raises ApiError / NotFound."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = await self._client.get(path, params=clean)
        if resp.is_success:
            return resp.json()
        raise _to_error(resp, path)


def _detail(resp: httpx.Response) -> str:
    """The API's `detail` string, or something honest if the body isn't JSON."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()[:200] or f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, str):
            return detail
        # 422 bodies are a list of Pydantic errors; flatten to one line each.
        if isinstance(detail, list):
            return "; ".join(
                f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', e)}"
                if isinstance(e, dict) else str(e)
                for e in detail
            )
        return str(detail)
    return str(body)


def _to_error(resp: httpx.Response, path: str) -> ApiError:
    """Map an HTTP failure onto the tool-facing error (§12)."""
    status = resp.status_code
    detail = _detail(resp)
    if status == 404:
        return NotFound(detail, status)
    if status == 401:
        # Not retryable: the key was revoked, expired or mistyped, and no amount
        # of trying again will mint a new one.
        return ApiError(
            f"The TestBench API rejected this server's API key: {detail}. "
            f"It may have been revoked or expired — a replacement is minted from "
            f"the owner's profile page and set as TB_MCP_API_TOKEN.",
            status,
        )
    if status == 403:
        # Role checks stay server-side; surfacing the reason lets the model tell
        # the user to ask an admin instead of retrying the same call.
        return ApiError(
            f"Permission denied: {detail}. The API key this MCP server uses does "
            f"not grant the required role — its role is the lower of the one it "
            f"was minted with and its owner's current role.",
            status,
        )
    if status == 413:
        return ApiError(f"Payload too large (the API caps uploads at 16 MB): {detail}", status)
    if status >= 500:
        # Do not hand internal failure text to the model; log it here instead.
        logger.error("TestBench API %s on %s: %s", status, path, detail)
        return ApiError(
            f"The TestBench API returned an internal error ({status}). "
            f"The details were logged by the MCP server.",
            status,
        )
    return ApiError(detail, status)
