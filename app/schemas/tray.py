"""Pydantic schemas for the MyPortal Tray App."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TrayDeviceFacts(BaseModel):
    """Facts a tray client reports about the host machine."""

    os: str = Field(min_length=1, max_length=32)
    os_version: Optional[str] = Field(default=None, max_length=64)
    hostname: Optional[str] = Field(default=None, max_length=255)
    serial_number: Optional[str] = Field(default=None, max_length=128)
    agent_version: Optional[str] = Field(default=None, max_length=32)
    console_user: Optional[str] = Field(default=None, max_length=255)


class TrayEnrolRequest(TrayDeviceFacts):
    install_token: str = Field(min_length=16, max_length=200)
    device_uid: Optional[str] = Field(default=None, max_length=64)


class TrayEnrolResponse(BaseModel):
    device_uid: str
    auth_token: str
    company_id: Optional[int] = None
    asset_id: Optional[int] = None
    poll_interval_seconds: int = 30


class TrayHeartbeatRequest(BaseModel):
    console_user: Optional[str] = Field(default=None, max_length=255)
    agent_version: Optional[str] = Field(default=None, max_length=32)
    last_ip: Optional[str] = Field(default=None, max_length=64)


class TrayMenuNode(BaseModel):
    """One node in the tray menu tree.

    The ``type`` discriminator selects how the client renders the node:

    * ``label`` — non-interactive caption.
    * ``link`` — opens ``url`` in the default browser.
    * ``submenu`` — has ``children``.
    * ``display_text`` — opens a popup with ``text`` (sanitised HTML).
    * ``env_var`` — reads an env var named ``name`` and shows / copies it.
    * ``open_chat`` — opens the chat window.
    * ``separator`` — visual divider.
    """

    type: str = Field(min_length=1, max_length=32)
    label: Optional[str] = Field(default=None, max_length=200)
    url: Optional[str] = Field(default=None, max_length=500)
    name: Optional[str] = Field(default=None, max_length=128)
    text: Optional[str] = Field(default=None)
    children: Optional[list["TrayMenuNode"]] = None


TrayMenuNode.model_rebuild()


class TrayConfigResponse(BaseModel):
    version: int
    menu: list[TrayMenuNode]
    display_text: Optional[str] = None
    branding_icon_url: Optional[str] = None
    env_allowlist: list[str] = Field(default_factory=list)
    chat_enabled: bool = False


class TrayMenuConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    scope: str = Field(default="global")
    scope_ref_id: Optional[int] = None
    payload: list[TrayMenuNode] = Field(default_factory=list)
    display_text: Optional[str] = None
    env_allowlist: list[str] = Field(default_factory=list)
    branding_icon_url: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = True


class TrayMenuConfigUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=150)
    payload: Optional[list[TrayMenuNode]] = None
    display_text: Optional[str] = None
    env_allowlist: Optional[list[str]] = None
    branding_icon_url: Optional[str] = Field(default=None, max_length=500)
    enabled: Optional[bool] = None


class TrayInstallTokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=150)
    company_id: Optional[int] = None
    expires_at: Optional[datetime] = None


class TrayInstallTokenResponse(BaseModel):
    id: int
    label: str
    company_id: Optional[int] = None
    token: Optional[str] = None  # populated only on creation
    token_prefix: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    use_count: int = 0


class TrayChatStartRequest(BaseModel):
    subject: Optional[str] = Field(default=None, max_length=500)
    message: Optional[str] = Field(default=None, max_length=4000)


class TrayChatStartResponse(BaseModel):
    room_id: int
    matrix_room_id: Optional[str] = None
    delivered: bool = False


class TrayDeviceSummary(BaseModel):
    id: int
    device_uid: str
    company_id: Optional[int] = None
    asset_id: Optional[int] = None
    hostname: Optional[str] = None
    os: Optional[str] = None
    os_version: Optional[str] = None
    console_user: Optional[str] = None
    last_ip: Optional[str] = None
    last_seen_utc: Optional[datetime] = None
    status: str
    agent_version: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 5 schemas
# ---------------------------------------------------------------------------


class TrayVersionResponse(BaseModel):
    version: str
    download_url: Optional[str] = None
    required: bool = False


class TrayDiagnosticSummary(BaseModel):
    id: int
    device_id: int
    device_uid: Optional[str] = None
    hostname: Optional[str] = None
    filename: str
    size_bytes: int = 0
    uploaded_at: datetime


class TrayVersionPublish(BaseModel):
    version: str = Field(min_length=1, max_length=32)
    platform: str = Field(default="all", max_length=16)
    download_url: str = Field(min_length=1, max_length=500)
    required: bool = False
    release_notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Phase 6 schemas
# ---------------------------------------------------------------------------


class TrayNotificationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=1000)


class TrayNotificationResponse(BaseModel):
    delivered: bool
