from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment for the MCP server, prefixed `TB_MCP_`.

    Deliberately separate from the backend's `TB_` namespace: this process runs
    in its own container and knows nothing about the database, only the API.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TB_MCP_", extra="ignore")

    # Where the TestBench REST API lives, including the version prefix.
    api_base: str = "http://backend:8000/api/v1"

    # Auth: a TestBench API key, minted by a user under their profile and
    # pasted in here. Every call is made as that user, with the role the key
    # carries. A readonly key suits this server — it only reads — and an admin
    # key is needed only for `search_audit`.
    #
    # There is no username/password fallback. A key is revocable on its own,
    # without disturbing the account it belongs to or any other key; it carries
    # its own expiry; and it never puts a password where a container can read
    # it. See §11 of MCP_SERVER.md.
    api_token: str = ""

    # "stdio" for a local desktop client, "streamable-http" for shared/remote use.
    transport: str = "streamable-http"
    host: str = "0.0.0.0"
    port: int = 8003
    # Path the streamable-HTTP endpoint is served under.
    mount_path: str = "/mcp"

    # DNS-rebinding protection for the HTTP transports. Off unless hosts are
    # named, which suits a container reached over an internal compose network;
    # set both when the endpoint is exposed to a browser-reachable origin.
    allowed_hosts: str = ""
    allowed_origins: str = ""

    # The key a CALLER must present to this server, as
    # `Authorization: Bearer <key>`, on the HTTP transports. Not to be confused
    # with api_token above, which is what this server presents to the Device
    # Manager: this one is an arbitrary shared secret and means nothing to the
    # REST API.
    #
    # Empty leaves the endpoint open, which is the pre-existing behaviour and
    # is only defensible on a network where everything that can reach the port
    # is already trusted — the server logs a warning saying so at startup.
    # Ignored on the stdio transport, which has no callers to authenticate.
    #
    # More than one key may be given, comma-separated, so keys can be rotated
    # without downtime: add the new one, move clients across, drop the old one.
    auth_token: str = ""

    http_timeout_seconds: float = 30.0

    # Default and hard ceilings on rows returned to the model. Tools take a
    # `limit`; it is clamped to `max_limit`. The default stays small because
    # most questions are answered by a handful of rows; the ceiling is high
    # enough to sweep a fleet in a few chunks rather than dozens.
    #
    # `max_limit` must stay at or below the API's own `page_size` cap (1000, in
    # api/devices.py). Above it the API returns 422 — an error, not a smaller
    # page — so the sweep fails outright instead of taking one more call.
    default_limit: int = 50
    max_limit: int = 500

    # Soft byte budget per tool response (§14: nothing over this without
    # `truncated: true`). Items are dropped from the tail until the payload
    # fits, and a paginated tool resumes from the last row actually kept, so
    # trimming here costs a round trip and never loses a row.
    #
    # Sized so a full `max_limit` page of device rows arrives whole. A row is
    # 172 B narrow and 234 B at its widest (checked out, long location and
    # model), so 500 rows is ~115 KB and 128 KB leaves headroom. Set this below
    # a full page and the budget, not `limit`, decides how many rows a caller
    # gets — the failure this number exists to prevent.
    #
    # It is also the ceiling for every OTHER tool, whose rows are wider: an
    # audit row carries a field diff. Raising it raises the worst case
    # everywhere, which is why it tracks `max_limit` rather than leading it.
    max_response_bytes: int = 131072

    # How many rows a tool may pull from the API while filtering client-side
    # (only `find_tests` date filters need this — the API has no date range).
    max_scan_rows: int = 2000

    @property
    def token_is_api_key(self) -> bool:
        """Whether the configured token has the shape of an API key."""
        parts = self.api_token.split("_", 2)
        # "dm" is the pre-TestBench prefix, still carried by keys minted then.
        return len(parts) == 3 and parts[0] in ("tb", "dm") and all(parts)

    @property
    def token_prefix(self) -> str:
        """The public half of the key, `tb_<prefix>` — safe to log.

        It is the same prefix the profile page lists beside the key, so a log
        line here names the row a user can go and revoke. The secret half is
        never logged.
        """
        if not self.token_is_api_key:
            return "(not an API key)"
        return "_".join(self.api_token.split("_", 2)[:2])

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def auth_token_list(self) -> list[str]:
        """Every key a caller may authenticate with. Empty means no auth."""
        return [t.strip() for t in self.auth_token.split(",") if t.strip()]

    @property
    def auth_required(self) -> bool:
        return bool(self.auth_token_list)


settings = Settings()
