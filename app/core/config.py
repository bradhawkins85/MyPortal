from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import AnyHttpUrl, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    This configuration mirrors the semantics of the legacy Node.js implementation
    while providing strongly-typed access for the new Python stack.
    """

    app_name: str = "MyPortal"
    environment: str = "development"
    secret_key: str = Field(alias="SESSION_SECRET")
    totp_encryption_key: str = Field(alias="TOTP_ENCRYPTION_KEY")
    database_host: str = Field(alias="DB_HOST")
    database_user: str = Field(alias="DB_USER")
    database_password: str = Field(alias="DB_PASSWORD")
    database_name: str = Field(alias="DB_NAME")
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    session_cookie_name: str = Field(default="myportal_session", alias="SESSION_COOKIE_NAME")
    allowed_origins: List[AnyHttpUrl] = Field(default_factory=list, alias="ALLOWED_ORIGINS")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASS")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_SECURE")
    azure_client_id: str | None = Field(default=None, alias="AZURE_CLIENT_ID")
    azure_client_secret: str | None = Field(default=None, alias="AZURE_CLIENT_SECRET")
    azure_tenant_id: str | None = Field(default=None, alias="AZURE_TENANT_ID")
    default_timezone: str = Field(default="UTC", alias="CRON_TIMEZONE")
    enable_csrf: bool = Field(default=True, alias="ENABLE_CSRF")
    swagger_ui_url: str = Field(default="/docs", alias="SWAGGER_UI_URL")

    model_config = SettingsConfigDict(env_file=(Path(__file__).resolve().parent.parent.parent / ".env"), env_file_encoding="utf-8")


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
