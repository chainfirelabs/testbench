"""Bearer-token authentication for the HTTP transports.

The MCP server holds a TestBench API key and reads the fleet with that
key's role, so anyone who can reach the endpoint reads the fleet. This module
puts a key in front of it: callers present `Authorization: Bearer <key>`, and
anything else gets a 401 before a single tool runs.

Two different keys are in play and they are easy to confuse:

  TB_MCP_API_TOKEN   outbound — what this server presents to the TestBench
                     REST API. Minted in the web app under Profile -> API keys.
  TB_MCP_AUTH_TOKEN  inbound  — what a caller must present to this server.
                     Any random string; it means nothing to TestBench.

The SDK does the enforcing: `MCPServer(token_verifier=..., auth=...)` wraps the
endpoint in its `RequireAuthMiddleware`, which rejects an absent, malformed or
unrecognised bearer token. All this module supplies is the check itself.
"""

from __future__ import annotations

import hashlib
import secrets

from mcp.server.auth.provider import AccessToken


def fingerprint(token: str) -> str:
    """A short, stable, non-reversible name for a key — safe to log.

    Lets a log line say which key a caller used, and which keys are configured,
    without ever putting the key itself in the log.
    """
    return hashlib.sha256(token.encode()).hexdigest()[:12]


class StaticTokenVerifier:
    """Accepts a fixed set of pre-shared keys.

    Deliberately not OAuth. This server has no authorization server to defer
    to, and a key handed to a client out of band is the whole requirement —
    see `settings.auth_settings` for why the OAuth resource-metadata half of
    the SDK's auth support is left switched off.

    More than one key is allowed so keys can be rotated without downtime: add
    the new one, move clients over, drop the old one. Each is identified in
    logs by its fingerprint, so a key can be retired by name.
    """

    def __init__(self, tokens: list[str]) -> None:
        if not tokens:
            raise ValueError("StaticTokenVerifier needs at least one token")
        self._tokens = tokens

    @property
    def fingerprints(self) -> list[str]:
        return [fingerprint(t) for t in self._tokens]

    async def verify_token(self, token: str) -> AccessToken | None:
        # compare_digest, not ==, so a wrong key cannot be recovered one
        # character at a time from how long the comparison took. The loop runs
        # to the end for the same reason: returning early on a match would
        # leak which key matched, and with it the order of the list.
        matched: str | None = None
        for expected in self._tokens:
            if secrets.compare_digest(token, expected):
                matched = expected
        if matched is None:
            return None
        # No scopes: this server has one level of access, and what a caller may
        # actually see is decided by the role on TB_MCP_API_TOKEN, not here.
        return AccessToken(token=token, client_id=f"mcp-key-{fingerprint(matched)}", scopes=[])
