"""Authoritative ownership map for optional integration capabilities.

Keep capability names stable: route and UI names are used for discovery and
scheduled commands are used for both scheduler admission and module lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleCapabilities:
    scheduled_commands: frozenset[str] = frozenset()
    inbound_routes: frozenset[str] = frozenset()
    outbound_services: frozenset[str] = frozenset()
    ui_features: frozenset[str] = frozenset()
    always_on: bool = False


def _c(*, commands=(), routes=(), services=(), ui=(), always_on=False):
    return ModuleCapabilities(
        frozenset(commands), frozenset(routes), frozenset(services),
        frozenset(ui), always_on,
    )


MODULE_CAPABILITIES: dict[str, ModuleCapabilities] = {
    "plausible": _c(routes=("analytics.pageview", "email.tracking"), services=("plausible.analytics", "email_tracking.delivery"), ui=("modules.plausible",)),
    "syncro": _c(routes=("syncro.import",), services=("syncro.api",), ui=("syncro",)),
    "ollama": _c(commands=("process_transcription",), services=("ollama.ai",), ui=("ai.ollama",)),
    "smtp": _c(services=("smtp.delivery",), ui=("modules.smtp",)),
    "smtp2go": _c(routes=("webhooks.smtp2go",), services=("smtp2go.delivery",), ui=("modules.smtp2go",)),
    "imap": _c(commands=("imap_sync:*",), services=("imap.mailbox",), ui=("mail.imap",)),
    "receive-sms": _c(routes=("webhooks.receive_sms",), services=("receive_sms.admin",), ui=("receive_sms",)),
    "calls": _c(routes=("webhooks.calls",), services=("calls.admin",), ui=("calls",)),
    "m365-mail": _c(commands=("sync_m365_mailboxes", "m365_mail_sync:*"), services=("m365_mail.graph",), ui=("mail.m365",)),
    "tacticalrmm": _c(commands=("sync_tactical_assets", "push_tactical_companies", "pull_tactical_companies", "refresh_company_ids", "update_tray_icon_installer"), routes=("tacticalrmm.actions",), services=("tacticalrmm.api", "tacticalrmm.company_sync", "tacticalrmm.tray"), ui=("tacticalrmm",)),
    "ntfy": _c(services=("ntfy.delivery",), ui=("modules.ntfy",)),
    "apprise": _c(services=("apprise.delivery",), ui=("modules.apprise",)),
    "uptimekuma": _c(routes=("webhooks.uptimekuma",), services=("uptimekuma.api",), ui=("uptimekuma",)),
    "chatgpt-mcp": _c(routes=("mcp.chatgpt",), services=("chatgpt.mcp",), ui=("ai.chatgpt_mcp",)),
    "ollama-mcp": _c(routes=("mcp.ollama",), services=("ollama.mcp",), ui=("ai.ollama_mcp",)),
    "xero": _c(commands=("sync_to_xero", "sync_to_xero_auto_send", "refresh_company_ids"), routes=("webhooks.xero", "oauth.xero"), services=("xero.api",), ui=("xero",)),
    "sms-gateway": _c(services=("sms_gateway.delivery",), ui=("modules.sms_gateway",)),
    "m365-admin": _c(commands=("sync_m365_data", "sync_o365", "sync_m365_email_domains", "sync_m365_licenses", "sync_m365_contacts", "refresh_m365_consent_status", "refresh_company_ids"), services=("m365_admin.graph",), ui=("m365.admin",)),
    "call-recordings": _c(commands=("sync_recordings", "queue_transcriptions"), services=("call_recordings.discovery", "call_recordings.import"), ui=("call_recordings",)),
    "whisperx": _c(commands=("queue_transcriptions", "process_transcription"), services=("whisperx.transcription",), ui=("whisperx",)),
    "unifi-talk": _c(commands=("sync_unifi_talk_recordings",), services=("unifi_talk.recordings",), ui=("unifi_talk",)),
    "reprocess-ai": _c(services=("tickets.reprocess_ai",), ui=("tickets.reprocess_ai",), always_on=True),
    "password-pusher": _c(services=("password_pusher.api",), ui=("password_pusher",)),
    "hudu": _c(services=("hudu.api",), ui=("hudu",)),
    "huntress": _c(commands=("sync_huntress",), services=("huntress.api",), ui=("huntress",)),
    "trello": _c(routes=("webhooks.trello",), services=("trello.api",), ui=("trello",)),
    "solidtime": _c(commands=("solidtime_reconcile",), services=("solidtime.api",), ui=("solidtime",)),
    "matrix-chat-assign": _c(commands=("matrix_chat_assign",), services=("matrix.assignment",), ui=("matrix.chat_assign",)),
}


COMMANDS_BY_MODULE = {
    slug: set(capabilities.scheduled_commands)
    for slug, capabilities in MODULE_CAPABILITIES.items()
    if capabilities.scheduled_commands
}


def modules_for_command(command: str) -> frozenset[str]:
    """Return every module owning *command*, including ``prefix:*`` entries."""
    return frozenset(
        slug for slug, capabilities in MODULE_CAPABILITIES.items()
        if command in capabilities.scheduled_commands
        or any(item.endswith("*") and command.startswith(item[:-1]) for item in capabilities.scheduled_commands)
    )
