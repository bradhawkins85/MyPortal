"""Pydantic schemas for the MyPortal Tray App."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, IPvAnyAddress


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


class TrayTRMMSyncRequest(BaseModel):
    """Identifiers posted by a Tactical RMM agent-side sync script."""

    agent_id: str = Field(min_length=1, max_length=255)
    tray_agent_id: str = Field(min_length=1, max_length=255)
    create_asset_if_missing: bool = True


class TrayTRMMSyncResponse(BaseModel):
    status: str
    device_id: int
    asset_id: int
    agent_id: str


class TrayHeartbeatRequest(BaseModel):
    console_user: Optional[str] = Field(default=None, max_length=255)
    agent_version: Optional[str] = Field(default=None, max_length=32)
    last_ip: Optional[str] = Field(default=None, max_length=64)


class TrayMenuNode(BaseModel):
    """One node in the tray menu tree.

    The ``type`` discriminator selects how the client renders the node:

    * ``label`` — non-interactive caption.
    * ``app_version`` — non-interactive caption showing the tray app version.
    * ``link`` — opens ``url`` in the default browser.
      The ``{{service_status_url}}`` URL variable is resolved by the server to
      the absolute published status-page URL for the device's company.
    * ``submenu`` — has ``children``.
    * ``display_text`` — opens a popup with ``text`` (sanitised HTML).
    * ``env_var`` — reads an env var named ``name`` and shows / copies it.
    * ``open_chat`` — opens the chat window.
    * ``submit_ticket`` — opens the submit-a-ticket dialog.
    * ``submit_syncro_ticket`` — opens the same dialog and creates the ticket in Syncro.
    * ``TRMM_Script`` — asks MyPortal to run a selected Tactical RMM script on the linked asset.
    * ``scan_network`` — asks an enabled network-scanner device to scan now.
    * ``refresh_config`` — asks the tray service to pull the latest menu config.
    * ``separator`` — visual divider.
    * ``quit`` — exits the tray application.
    """

    type: str = Field(min_length=1, max_length=32)
    label: Optional[str] = Field(default=None, max_length=200)
    url: Optional[str] = Field(default=None, max_length=500)
    name: Optional[str] = Field(default=None, max_length=128)
    text: Optional[str] = Field(default=None)
    color: Optional[str] = Field(default=None, max_length=32)
    script_id: Optional[int] = Field(default=None, ge=1)
    script_name: Optional[str] = Field(default=None, max_length=200)
    visible_company_ids: Optional[list[int]] = None
    hidden_company_ids: Optional[list[int]] = None
    children: Optional[list["TrayMenuNode"]] = None


TrayMenuNode.model_rebuild()


class TrayConfigResponse(BaseModel):
    version: int
    menu: list[TrayMenuNode]
    display_text: Optional[str] = None
    branding_icon_url: Optional[str] = None
    branding_display_name: Optional[str] = Field(default=None, max_length=100)
    env_allowlist: list[str] = Field(default_factory=list)
    chat_enabled: bool = False
    # Controls how the tray client opens chat windows.
    # "" or "app" (default) = dedicated chat shell / browser app-mode.
    # "browser" = always open in the default system browser.
    # "shell"   = require the dedicated chat shell; no browser fallback.
    chat_client_mode: Optional[str] = None
    network_scanner_enabled: bool = False
    network_scan_interval_minutes: int = 360
    network_scan_wan_cidrs: list[str] = Field(default_factory=list)
    network_scan_local_cidrs: list[str] = Field(default_factory=list)


class TrayOutlookSignaturesRequest(BaseModel):
    """Email addresses of the Outlook accounts configured for the signed-in user."""

    addresses: list[str] = Field(default_factory=list, max_length=20)


class TrayOutlookSignature(BaseModel):
    address: str
    name: str
    html: str
    text: str
    hash: str


class TrayOutlookSignaturesResponse(BaseModel):
    enabled: bool
    template_slug: Optional[str] = None
    # Primary signature per account; the agent makes it the account default.
    signatures: list[TrayOutlookSignature] = Field(default_factory=list)
    # Other signatures available to the account's staff member; written so
    # they can be picked in Outlook but never set as the default.
    additional_signatures: list[TrayOutlookSignature] = Field(default_factory=list)
    skipped: dict[str, str] = Field(default_factory=dict)


class NetworkScanHost(BaseModel):
    ip_address: str = Field(min_length=1, max_length=45)
    mac_address: Optional[str] = Field(default=None, max_length=17)
    hostname: Optional[str] = Field(default=None, max_length=255)
    vendor: Optional[str] = Field(default=None, max_length=255)
    os_details: Optional[str] = Field(default=None, max_length=500)
    open_ports: Optional[str] = None


class NetworkScanRequest(BaseModel):
    wan_ip: IPvAnyAddress
    subnets: list[str] = Field(default_factory=list, max_length=128)
    hosts: list[NetworkScanHost] = Field(max_length=4096)


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


class TrayDeploymentLinkCreate(BaseModel):
    company_id: int = Field(ge=1)
    label: Optional[str] = Field(default=None, max_length=150)
    expires_in_days: Literal[0, 1, 7, 30, 90, 365] = 7


class TrayDeploymentLinkResponse(BaseModel):
    id: int
    company_id: int
    company_name: Optional[str] = None
    label: str
    url: Optional[str] = None
    status: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    download_count: int = 0
    last_downloaded_at: Optional[datetime] = None


class TrayDeploymentBuildFailure(BaseModel):
    error: str = Field(default="", max_length=4000)


class TrayTRMMScriptRunRequest(BaseModel):
    script_id: int = Field(ge=1)


class TrayTRMMScriptRunResponse(BaseModel):
    status: str
    script_id: int
    script_name: Optional[str] = None
    event_id: Optional[int] = None
    message: Optional[str] = None


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


class TrayChatTokenResponse(BaseModel):
    """Response returned by POST /api/tray/chat-token.

    The ``token`` is a one-time URL token valid for ``expires_in`` seconds.
    The tray client should open ``chat_url`` in the popup webview immediately;
    the token is consumed on first use.
    """

    token: str
    expires_in: int = 300
    chat_url: str


class TrayTicketTokenResponse(BaseModel):
    """Response returned by POST /api/tray/ticket-token."""

    token: str
    expires_in: int = 300
    ticket_url: str


# ---------------------------------------------------------------------------
# Phase 7 schemas — tray ticket submission
# ---------------------------------------------------------------------------


class TrayTicketSubmitRequest(BaseModel):
    """Fields submitted by a tray device when the user clicks Submit Ticket."""

    device_uid: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=1, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=50)
    subject: str = Field(min_length=1, max_length=500)
    description: Optional[str] = Field(default=None)
    answers: Optional[list["TrayTicketAnswer"]] = Field(default=None)


class TrayTicketAnswer(BaseModel):
    """A single dynamic question answer submitted with a ticket."""

    question_id: int
    value: Optional[str] = Field(default=None, max_length=2000)


class TrayTicketSubmitResponse(BaseModel):
    ticket_id: int
    ticket_number: Optional[str] = None


# ---------------------------------------------------------------------------
# Dynamic ticket question schemas
# ---------------------------------------------------------------------------


class TrayTicketQuestionCondition(BaseModel):
    """A conditional visibility rule for a ticket question."""

    id: Optional[int] = None
    parent_question_id: int
    operator: str = Field(default="equals", max_length=16)
    expected_value: str = Field(default="", max_length=255)


class TrayTicketQuestion(BaseModel):
    """A single dynamic intake question returned by the ticket-questions endpoint."""

    id: int
    scope: str
    company_id: Optional[int] = None
    field_type: str
    label: str
    placeholder: Optional[str] = None
    is_required: bool
    options: list[str] = Field(default_factory=list)
    sort_order: int
    conditions: list[TrayTicketQuestionCondition] = Field(default_factory=list)


class TrayTicketQuestionsResponse(BaseModel):
    """Response returned by GET /api/tray/ticket-questions."""

    questions: list[TrayTicketQuestion] = Field(default_factory=list)


class TrayTicketQuestionCreate(BaseModel):
    """Admin payload to create a ticket question definition."""

    scope: str = Field(default="global", pattern=r"^(global|company)$")
    company_id: Optional[int] = None
    field_type: str = Field(default="text", pattern=r"^(text|select|boolean)$")
    label: str = Field(min_length=1, max_length=255)
    placeholder: Optional[str] = Field(default=None, max_length=255)
    is_required: bool = False
    options: list[str] = Field(default_factory=list)
    sort_order: int = 0
    is_active: bool = True
    conditions: list[TrayTicketQuestionCondition] = Field(default_factory=list)


class TrayTicketQuestionUpdate(BaseModel):
    """Admin payload to update a ticket question definition."""

    field_type: Optional[str] = Field(default=None, pattern=r"^(text|select|boolean)$")
    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    placeholder: Optional[str] = Field(default=None, max_length=255)
    is_required: Optional[bool] = None
    options: Optional[list[str]] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    conditions: Optional[list[TrayTicketQuestionCondition]] = None


# ---------------------------------------------------------------------------
# AI troubleshooting agent
# ---------------------------------------------------------------------------


class TrayTroubleshootRequest(BaseModel):
    """Technician payload to start an AI troubleshooting run on a device."""

    ticket_id: int = Field(
        ...,
        ge=1,
        description="The support ticket the agent's guidance + log bundle attach to.",
    )
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="Description of the issue to troubleshoot, written by the technician.",
    )
    model: Optional[str] = Field(
        None,
        max_length=120,
        description="Optional LLM model override. Falls back to the site default model.",
    )


class TrayTroubleshootResponse(BaseModel):
    """Acknowledgement returned after the troubleshoot command is queued/delivered."""

    # None for the multi-stage ticket troubleshooter, which only sends a device
    # command later, if its recommended steps call for endpoint logs.
    command_id: Optional[int] = None
    device_uid: str
    ticket_id: int
    # "delivered" (device online), "queued" (will run on reconnect) or
    # "started" (multi-stage troubleshooter running on the server).
    status: str
    delivered: bool
    model: str


class TicketAssetTroubleshootRequest(BaseModel):
    """Technician payload to run the AI troubleshooter on a ticket's linked asset.

    All fields are optional. The ticket's own data (subject, description,
    priority, category, status) plus the asset under investigation are always
    fed to the LLM by the server; ``model`` overrides the LLM model and
    ``context`` appends free-form technician notes to that context.
    """

    model: Optional[str] = Field(
        None,
        max_length=120,
        description="Optional LLM model override. Falls back to the site default model.",
    )
    context: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional technician notes appended to the ticket context fed to the LLM.",
    )
