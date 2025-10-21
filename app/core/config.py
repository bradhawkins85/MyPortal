from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import AnyHttpUrl, AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    The settings preserve the existing environment variable semantics from prior
    deployments while providing strongly-typed access for the Python stack.
    """

    app_name: str = "MyPortal"
    environment: str = "development"
    secret_key: str = Field(validation_alias=AliasChoices("SESSION_SECRET", "SECRET_KEY"))
    totp_encryption_key: str = Field(validation_alias="TOTP_ENCRYPTION_KEY")
    database_host: str = Field(validation_alias="DB_HOST")
    database_user: str = Field(validation_alias="DB_USER")
    database_password: str = Field(validation_alias="DB_PASSWORD")
    database_name: str = Field(validation_alias="DB_NAME")
    migration_lock_timeout: int = Field(
        default=60, validation_alias="MIGRATION_LOCK_TIMEOUT"
    )
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")
    session_cookie_name: str = Field(
        default="myportal_session",
        validation_alias=AliasChoices("SESSION_COOKIE_NAME", "SESSION_COOKIE"),
    )
    allowed_origins: List[AnyHttpUrl] = Field(
        default_factory=list,
        validation_alias="ALLOWED_ORIGINS",
    )
    smtp_host: str | None = Field(default=None, validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, validation_alias="SMTP_USER")
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
    m365_admin_client_id: str | None = Field(
        default=None, validation_alias="M365_ADMIN_CLIENT_ID"
    )
    m365_admin_client_secret: str | None = Field(
        default=None, validation_alias="M365_ADMIN_CLIENT_SECRET"
    )
    default_timezone: str = Field(default="UTC", validation_alias="CRON_TIMEZONE")
    enable_csrf: bool = Field(default=True, validation_alias="ENABLE_CSRF")
    enable_auto_refresh: bool = Field(
        default=False, validation_alias="ENABLE_AUTO_REFRESH"
    )
    swagger_ui_url: str = Field(default="/docs", validation_alias="SWAGGER_UI_URL")
    opnform_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("OPNFORM_BASE_URL", "OPNFORM_URL"),
    )
    fail2ban_log_path: Path | None = Field(
        default=None,
        validation_alias="FAIL2BAN_LOG_PATH",
    )

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
