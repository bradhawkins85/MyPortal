from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    AnyHttpUrl,
    AliasChoices,
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


# Placeholder values that must be replaced before running in production. These
# match the defaults distributed in ``.env.example`` and other template files.
_PLACEHOLDER_SECRETS: frozenset[str] = frozenset(
    {
        "change-me",
        "changeme",
        "change_me",
        "please-change",
        "replace-me",
        "secret",
        "password",
    }
)

# Minimum byte length required for cryptographic secrets used in production.
_MIN_PRODUCTION_SECRET_LENGTH: int = 32


def _is_weak_secret(value: str | None) -> tuple[bool, str]:
    """Return ``(is_weak, reason)`` for a secret value.

    The check is intentionally simple: it flags empty/placeholder values, short
    secrets, and values that have almost no unique characters (e.g. ``aaaaaa``).
    """

    if value is None or not value.strip():
        return True, "is empty"
    stripped = value.strip()
    if stripped.lower() in _PLACEHOLDER_SECRETS:
        return True, "uses a placeholder default"
    if len(stripped) < _MIN_PRODUCTION_SECRET_LENGTH:
        return True, (
            f"is shorter than the required {_MIN_PRODUCTION_SECRET_LENGTH} characters"
        )
    # Entropy sanity check – reject values with only a handful of unique
    # characters (e.g. ``aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa``).
    if len(set(stripped)) < 8:
        return True, "has too few unique characters (low entropy)"
    return False, ""


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    The settings preserve the existing environment variable semantics from prior
    deployments while providing strongly-typed access for the Python stack.
    """

    app_name: str = "MyPortal"
    environment: str = "development"
    secret_key: str = Field(validation_alias=AliasChoices("SESSION_SECRET", "SECRET_KEY"))
    totp_encryption_key: str = Field(validation_alias="TOTP_ENCRYPTION_KEY")
    database_host: str | None = Field(default=None, validation_alias="DB_HOST")
    database_user: str | None = Field(default=None, validation_alias="DB_USER")
    database_password: str | None = Field(default=None, validation_alias="DB_PASSWORD")
    database_name: str | None = Field(default=None, validation_alias="DB_NAME")
    migration_lock_timeout: int = Field(
        default=60, validation_alias="MIGRATION_LOCK_TIMEOUT"
    )
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    session_cookie_name: str = Field(
        default="myportal_session",
        validation_alias=AliasChoices("SESSION_COOKIE_NAME", "SESSION_COOKIE"),
    )
    allowed_origins: str = Field(
        default="",
        validation_alias="ALLOWED_ORIGINS",
    )
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, validation_alias="SMTP_USER")
    smtp_from: str | None = Field(default=None, validation_alias="SMTP_FROM")
    smtp_password: str | None = Field(default=None, validation_alias="SMTP_PASS")
    smtp_use_tls: bool = Field(default=True, validation_alias="SMTP_SECURE")
    stock_feed_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="STOCK_FEED_URL"
    )
    syncro_webhook_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="SYNCRO_WEBHOOK_URL"
    )
    syncro_api_key: str | None = Field(default=None, validation_alias="SYNCRO_API_KEY")
    verify_webhook_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="VERIFY_WEBHOOK_URL"
    )
    verify_api_key: str | None = Field(default=None, validation_alias="VERIFY_API_KEY")
    sms_endpoint: AnyHttpUrl | None = Field(default=None, validation_alias="SMS_ENDPOINT")
    sms_auth: str | None = Field(default=None, validation_alias="SMS_AUTH")
    portal_url: AnyHttpUrl | None = Field(default=None, validation_alias="PORTAL_URL")
    azure_client_id: str | None = Field(default=None, validation_alias="AZURE_CLIENT_ID")
    azure_client_secret: str | None = Field(default=None, validation_alias="AZURE_CLIENT_SECRET")
    azure_tenant_id: str | None = Field(default=None, validation_alias="AZURE_TENANT_ID")
    licenses_webhook_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="LICENSES_WEBHOOK_URL"
    )
    licenses_webhook_api_key: str | None = Field(
        default=None, validation_alias="LICENSES_WEBHOOK_API_KEY"
    )
    shop_webhook_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="SHOP_WEBHOOK_URL"
    )
    shop_webhook_api_key: str | None = Field(
        default=None, validation_alias="SHOP_WEBHOOK_API_KEY"
    )
    quote_expiry_days: int = Field(
        default=7, validation_alias="QUOTE_EXPIRY_DAYS", ge=1
    )
    m365_admin_client_id: str | None = Field(
        default=None, validation_alias="M365_ADMIN_CLIENT_ID"
    )
    m365_admin_client_secret: str | None = Field(
        default=None, validation_alias="M365_ADMIN_CLIENT_SECRET"
    )
    m365_bootstrap_client_id: str | None = Field(
        default=None, validation_alias="M365_BOOTSTRAP_CLIENT_ID"
    )
    m365_bootstrap_client_secret: str | None = Field(
        default=None, validation_alias="M365_BOOTSTRAP_CLIENT_SECRET"
    )
    m365_pkce_client_id: str | None = Field(
        default=None, validation_alias="M365_PKCE_CLIENT_ID"
    )
    m365_client_secret_lifetime_days: int = Field(
        default=730, validation_alias="M365_CLIENT_SECRET_LIFETIME_DAYS", ge=1
    )
    m365_client_secret_renewal_days: int = Field(
        default=14, validation_alias="M365_CLIENT_SECRET_RENEWAL_DAYS", ge=1
    )
    default_timezone: str = Field(default="UTC", validation_alias="CRON_TIMEZONE")
    enable_csrf: bool = Field(default=True, validation_alias="ENABLE_CSRF")
    enable_auto_refresh: bool = Field(
        default=False, validation_alias="ENABLE_AUTO_REFRESH"
    )
    disable_caching: bool = Field(
        default=True, validation_alias="DISABLE_CACHING"
    )
    swagger_ui_url: str = Field(default="/docs", validation_alias="SWAGGER_UI_URL")
    public_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PUBLIC_BASE_URL", "PUBLIC_URL"),
        description=(
            "Public base URL of this MyPortal instance (e.g. 'https://portal.example.com'). "
            "Used to build callback URLs registered with external services such as Trello "
            "webhooks when the reverse proxy does not forward 'X-Forwarded-Proto' / "
            "'X-Forwarded-Host' headers. If unset, the URL is inferred from the incoming "
            "request, defaulting to https:// when a proxy is detected."
        ),
    )
    opnform_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("OPNFORM_BASE_URL", "OPNFORM_URL"),
    )
    fail2ban_log_path: Path | None = Field(
        default=None,
        validation_alias="FAIL2BAN_LOG_PATH",
    )
    log_rotation: str | None = Field(
        default="50 MB",
        validation_alias="LOG_ROTATION",
        description=(
            "Loguru rotation policy for the disk log sink. Accepts a size (e.g. '50 MB'), "
            "an interval (e.g. '1 day'), or a clock time (e.g. '00:00'). Set empty to disable."
        ),
    )
    log_retention: str | None = Field(
        default="30 days",
        validation_alias="LOG_RETENTION",
        description=(
            "Loguru retention policy controlling how long old rotated log files are kept "
            "(e.g. '30 days', '4 weeks'). Set empty to keep indefinitely."
        ),
    )
    log_compression: str | None = Field(
        default="gz",
        validation_alias="LOG_COMPRESSION",
        description=(
            "Compression format applied to rotated log files (e.g. 'gz', 'zip'). "
            "Set empty to keep rotated files uncompressed."
        ),
    )
    error_log_path: Path | None = Field(
        default=None,
        validation_alias="ERROR_LOG_PATH",
        description=(
            "Optional dedicated log file that receives WARNING and above only. "
            "Useful for tailing 'just the bad stuff' for troubleshooting."
        ),
    )
    audit_retention_days: int = Field(
        default=365,
        validation_alias="AUDIT_RETENTION_DAYS",
        ge=0,
        description=(
            "Number of days of audit_logs history to retain. Set to 0 to disable pruning."
        ),
    )
    ai_tag_threshold: int = Field(
        default=1,
        validation_alias="AI_TAG_THRESHOLD",
        ge=1,
    )
    bcp_enabled: bool = Field(
        default=True,
        validation_alias="BCP_ENABLED",
    )
    enable_hsts: bool = Field(
        default=False,
        validation_alias="ENABLE_HSTS",
    )
    
    # MCP (Model Context Protocol) Server Configuration
    mcp_enabled: bool = Field(
        default=False,
        validation_alias="MCP_ENABLED",
    )
    mcp_token: str | None = Field(
        default=None,
        validation_alias="MCP_TOKEN",
    )
    mcp_allowed_models: str = Field(
        default="users,tickets,change_log",
        validation_alias="MCP_ALLOWED_MODELS",
    )
    mcp_readonly: bool = Field(
        default=True,
        validation_alias="MCP_READONLY",
    )
    mcp_rate_limit: int = Field(
        default=60,
        validation_alias="MCP_RATE_LIMIT",
    )
    mcp_log_tools_enabled: bool = Field(
        default=True,
        validation_alias="MCP_LOG_TOOLS_ENABLED",
        description=(
            "Enable the audit-log and application-log MCP tools "
            "(search_audit_logs, get_audit_log, get_application_logs). "
            "Set to false to hide these tools from MCP clients."
        ),
    )
    mcp_log_max_lines: int = Field(
        default=500,
        validation_alias="MCP_LOG_MAX_LINES",
        ge=1,
        description=(
            "Maximum number of log lines that get_application_logs may return "
            "in a single call. Capped at this value even when the caller requests more."
        ),
    )

    # Matrix.org Chat Integration
    matrix_enabled: bool = Field(default=False, validation_alias="MATRIX_ENABLED")
    matrix_homeserver_url: str | None = Field(default=None, validation_alias="MATRIX_HOMESERVER_URL")
    matrix_server_name: str | None = Field(default=None, validation_alias="MATRIX_SERVER_NAME")
    matrix_bot_user_id: str | None = Field(default=None, validation_alias="MATRIX_BOT_USER_ID")
    matrix_bot_access_token: str | None = Field(default=None, validation_alias="MATRIX_BOT_ACCESS_TOKEN")
    matrix_device_id: str | None = Field(default=None, validation_alias="MATRIX_DEVICE_ID")
    matrix_is_self_hosted: bool = Field(default=False, validation_alias="MATRIX_IS_SELF_HOSTED")
    matrix_admin_access_token: str | None = Field(default=None, validation_alias="MATRIX_ADMIN_ACCESS_TOKEN")
    matrix_default_room_preset: str = Field(default="private_chat", validation_alias="MATRIX_DEFAULT_ROOM_PRESET")
    matrix_e2ee_enabled: bool = Field(default=False, validation_alias="MATRIX_E2EE_ENABLED")
    matrix_invite_domain: str | None = Field(default=None, validation_alias="MATRIX_INVITE_DOMAIN")

    # IP Whitelisting Configuration
    ip_whitelist_enabled: bool = Field(
        default=False,
        validation_alias="IP_WHITELIST_ENABLED",
    )
    ip_whitelist: str = Field(
        default="",
        validation_alias="IP_WHITELIST",
    )
    ip_whitelist_admin_only: bool = Field(
        default=True,
        validation_alias="IP_WHITELIST_ADMIN_ONLY",
    )

    # Comma-separated list of trusted proxy IP/CIDR ranges. When set, the
    # application will honour ``X-Forwarded-For`` and ``X-Real-IP`` headers
    # only when the direct peer is one of these addresses. When empty, proxy
    # headers are ignored and the direct socket peer is used as the client IP.
    trusted_proxies: str = Field(
        default="",
        validation_alias="TRUSTED_PROXIES",
    )

    # GitHub integration (used for fetching the latest tray MSI on startup)
    github_token: str | None = Field(
        default=None,
        validation_alias="GITHUB_TOKEN",
    )
    github_tray_msi_repo: str = Field(
        default="bradhawkins85/MyPortal",
        validation_alias="GITHUB_TRAY_MSI_REPO",
    )

    @model_validator(mode="after")
    def _enforce_production_secret_strength(self) -> "Settings":
        """Refuse to boot in production with weak or placeholder secrets.

        In non-production environments (``development``, ``test``) these checks
        are skipped so tests and local work are not disrupted. The same
        validation is applied to ``SESSION_SECRET`` and ``TOTP_ENCRYPTION_KEY``
        because both are used for authenticated encryption / signing.
        """

        environment = (self.environment or "").strip().lower()
        if environment != "production":
            return self

        errors: list[str] = []
        for name, value in (
            ("SESSION_SECRET", self.secret_key),
            ("TOTP_ENCRYPTION_KEY", self.totp_encryption_key),
        ):
            weak, reason = _is_weak_secret(value)
            if weak:
                errors.append(f"{name} {reason}")

        # Feature-gated secrets: only required when the related feature is on.
        if self.mcp_enabled:
            weak, reason = _is_weak_secret(self.mcp_token)
            if weak:
                errors.append(f"MCP_TOKEN {reason} (required when MCP_ENABLED=true)")

        if errors:
            raise ValueError(
                "Refusing to start in production with weak secrets: "
                + "; ".join(errors)
                + ". Generate strong values with "
                + '`python -c "import secrets; print(secrets.token_urlsafe(48))"` '
                + "and update your .env file."
            )
        return self

    @field_validator(
        "smtp_use_tls",
        "enable_csrf",
        "enable_auto_refresh",
        "disable_caching",
        "bcp_enabled",
        "enable_hsts",
        "mcp_enabled",
        "mcp_readonly",
        "mcp_log_tools_enabled",
        "ip_whitelist_enabled",
        "ip_whitelist_admin_only",
        "matrix_enabled",
        "matrix_is_self_hosted",
        "matrix_e2ee_enabled",
        mode="before",
    )
    @classmethod
    def _strip_inline_comments(cls, value: bool | str) -> bool | str:
        """Strip inline comments from boolean fields in environment variables.
        
        Environment files may contain inline comments like:
            IP_WHITELIST_ADMIN_ONLY=true  # admin routes only
        
        This validator removes the comment portion to allow proper boolean parsing.
        """
        if isinstance(value, str):
            # Split on '#' and take the first part, then strip whitespace
            value = value.split("#")[0].strip()
        return value

    @field_validator(
        "syncro_webhook_url",
        "verify_webhook_url",
        "portal_url",
        "licenses_webhook_url",
        "shop_webhook_url",
        "sms_endpoint",
        "opnform_base_url",
        "stock_feed_url",
        mode="before",
    )
    @classmethod
    def _empty_string_to_none(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:  # type: ignore[override]
        """Coerce blank environment variables to ``None`` so optional URLs stay optional."""

        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("allowed_origins")
    @classmethod
    def _validate_allowed_origins(cls, value: str) -> str:
        """Validate comma-separated CORS origins and reject wildcard origins."""

        if not value.strip():
            return value

        url_adapter = TypeAdapter(AnyHttpUrl)
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]

        for origin in origins:
            if origin == "*":
                raise ValueError("ALLOWED_ORIGINS cannot include wildcard '*'.")
            try:
                url_adapter.validate_python(origin)
            except ValidationError as exc:  # pragma: no cover - exercised in settings construction
                raise ValueError(f"Invalid CORS origin in ALLOWED_ORIGINS: {origin}") from exc

        return value

    def is_production(self) -> bool:
        """Return True when the application is running in production mode."""

        return (self.environment or "").strip().lower() == "production"

    def hsts_effective(self) -> bool:
        """Whether the HSTS header should be sent.

        Defaults to ``True`` in production and to the explicit ``enable_hsts``
        setting everywhere else. Operators can still force-disable HSTS in
        production by setting ``ENABLE_HSTS=false``.
        """

        if self.enable_hsts:
            return True
        # In production, default to on unless explicitly disabled. We detect
        # an explicit disable by checking whether the raw env var was set.
        import os

        raw = os.environ.get("ENABLE_HSTS")
        if self.is_production() and (raw is None or raw.strip() == ""):
            return True
        return self.enable_hsts

    def trusted_proxy_networks(self) -> list[Any]:
        """Parse the TRUSTED_PROXIES setting into a list of ``ip_network`` objects."""

        from ipaddress import ip_network

        entries: list[Any] = []
        for entry in self.trusted_proxies.split(","):
            value = entry.strip()
            if not value:
                continue
            try:
                entries.append(ip_network(value, strict=False))
            except ValueError:  # pragma: no cover - logged in middleware layer
                continue
        return entries

    model_config = SettingsConfigDict(
        env_file=(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class TemplatesConfig(BaseModel):
    """Configuration for templating and theming."""

    static_path: Path = Path(__file__).resolve().parent.parent / "static"
    template_path: Path = Path(__file__).resolve().parent.parent / "templates"
    theme_name: str = "default"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_templates_config() -> TemplatesConfig:
    return TemplatesConfig()
