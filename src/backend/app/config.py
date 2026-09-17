from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TB_", extra="ignore")

    app_name: str = "TestBench"
    # Stamped into the image by the Dockerfile's VERSION build arg (which
    # docker-compose feeds from TB_VERSION). Stays "dev" when running straight
    # from a checkout, which is the honest answer there.
    version: str = "dev"

    # Database connection, as parts. The defaults are the Postgres that ships in
    # docker-compose.yml; point them anywhere to use an external instance.
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "device_manager"
    db_password: str = "device_manager"
    db_name: str = "device_manager"
    # libpq sslmode: disable/allow/prefer/require/verify-ca/verify-full. Empty
    # leaves it unset, which libpq treats as `prefer` — it will use TLS if the
    # server offers it, and silently fall back to plaintext if not. Set it to
    # `require` or stricter for a database reached over a network you do not own.
    db_sslmode: str = ""

    # Full SQLAlchemy URL. Wins over the parts above when set, for connection
    # strings the parts cannot express (a different driver, extra libpq options).
    database_url: str = ""
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expires_hours: int = 8
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://localhost:8080"

    # Seed local admin on startup
    admin_username: str = "admin"
    admin_password: str = "admin"

    # Frontend origin used for post-OIDC redirects
    frontend_url: str = "http://localhost:8082"

    # Authentik OIDC (Phase 3)
    authentik_issuer: str = ""
    authentik_client_id: str = ""
    authentik_client_secret: str = ""
    authentik_group_role_map: str = ""  # e.g. "admins:admin,testers:tester,readonly:readonly"
    authentik_default_role: str = "readonly"

    # The fleet's timezone, as an IANA name ("America/New_York"). Only dates
    # read it: it decides when "today" rolls over, and so which day a checkout
    # is stamped with and when one becomes overdue. Left at UTC, a 7pm
    # US-Eastern checkout is stamped with tomorrow's date.
    timezone: str = "UTC"

    # Checkout deadlines. The sweep sends the warning and overdue
    # notifications; it never changes a device.
    checkout_sweep_minutes: int = 60  # 0 disables the sweep
    checkout_warn_days: int = 3  # warn daily from this many days before the due date

    # Network scanning (online/offline detection)
    scan_interval_minutes: int = 15  # 0 disables the periodic scan
    scan_timeout_seconds: float = 2.0  # per-probe timeout
    scan_concurrency: int = 10  # parallel probes during a large scan

    # On-demand device research agent. The UI only offers this action for an
    # online device carrying every configured field; the API repeats that
    # check before asking the local Docker daemon to start the agent.
    device_info_enabled: bool = False
    device_info_image: str = "oh-my-pi:latest"
    device_info_required_fields: str = "wan_ip"
    device_info_url_field: str = "wan_ip"
    device_info_prompt: str = (
        "Use the browser tool to browse {device_url} and identify this device. "
        "Gather useful information about it. Device inventory data: {device_json}"
    )
    device_info_forward_env: str = "LITELLM_API_KEY,DOMAIN,VAULT_SECRET_PATH,VAULT_TOKEN"
    device_info_docker_socket: str = "/var/run/docker.sock"
    device_info_network_mode: str = "host"

    # Comma-separated plugin-id=internal-http-url entries. Helm renders this
    # from enabled plugin values; an empty value loads no external plugins.
    plugins: str = ""
    plugin_shared_secret: str = ""
    plugin_timeout_seconds: float = 10.0
    # Encrypts admin-managed AI provider credentials at rest. Required only
    # when a GUI profile stores an API key; Helm-locked profiles keep using a
    # Kubernetes Secret and never enter the database.
    credential_encryption_key: str = ""

    # Helm's optional MCP deployment receives the same generated credential.
    # It authenticates only as a synthetic readonly identity and is never
    # persisted as a user or API-key row.
    mcp_internal_token: str = ""

    # Optional JSON object used only the first time an empty database starts.
    # Thereafter the field catalog lives in the database and this value is
    # deliberately ignored. See docs/entity-fields.md for its shape.
    entity_fields_json: str = ""

    # ---- dynamic device schema (docs/device-schema.md) ----
    # Path to a mounted DeviceSchema YAML document. Empty disables YAML
    # configuration entirely and leaves the database and admin GUI in charge.
    device_schema_path: str = ""
    # bootstrap  — import the document only into an installation that has no
    #              user-owned schema yet; the GUI owns everything afterwards.
    # merge      — add what the document introduces, never delete GUI-created
    #              configuration, and report conflicts rather than guessing.
    # authoritative — the document is the source of truth and is reconciled on
    #              every start; YAML-owned objects are read-only in the GUI.
    device_schema_reconciliation: str = "bootstrap"
    # Whether a device type may take a global field back off itself. Off by
    # default so "global" keeps meaning global; the GUI labels it an advanced
    # override where it is enabled.
    device_schema_allow_global_exclusions: bool = False
    # Refuse writes carrying keys the catalog does not define. Off by default:
    # a value whose field was removed from a layout is still real data, and
    # preserving it is the point of a configurable schema.
    device_schema_reject_unknown_fields: bool = False

    @property
    def sqlalchemy_url(self) -> str:
        """The URL SQLAlchemy and Alembic connect with.

        Built from the parts unless TB_DATABASE_URL overrides it. The user and
        password are percent-encoded: a password containing @ : / or ? is
        ordinary, and interpolating one into a URL raw silently produces a
        connection string that points somewhere else entirely.
        """
        if self.database_url:
            return self.database_url
        auth = f"{quote(self.db_user, safe='')}:{quote(self.db_password, safe='')}"
        url = f"postgresql+psycopg2://{auth}@{self.db_host}:{self.db_port}/{self.db_name}"
        if self.db_sslmode:
            url += f"?sslmode={quote(self.db_sslmode, safe='')}"
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def group_role_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in self.authentik_group_role_map.split(","):
            if ":" in pair:
                g, r = pair.split(":", 1)
                out[g.strip()] = r.strip()
        return out

    @property
    def device_info_required_field_list(self) -> list[str]:
        return [field.strip() for field in self.device_info_required_fields.split(",") if field.strip()]

    @property
    def device_info_forward_env_list(self) -> list[str]:
        return [name.strip() for name in self.device_info_forward_env.split(",") if name.strip()]

    @property
    def plugin_endpoints(self) -> dict[str, str]:
        result = {}
        for entry in self.plugins.split(","):
            if "=" not in entry:
                continue
            plugin_id, url = entry.split("=", 1)
            if plugin_id.strip() and url.strip():
                result[plugin_id.strip()] = url.strip().rstrip("/")
        return result


settings = Settings()
