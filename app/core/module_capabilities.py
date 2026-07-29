"""Authoritative ownership map for optional integration capabilities.

Keep capability names stable: route and UI names are used for discovery and
scheduled commands are used for both scheduler admission and module lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from collections.abc import Iterable, Mapping


@dataclass(frozen=True)
class ModuleCapabilities:
    # Explicit mapping from the persistent database slug to the Python pack.
    # These identifiers deliberately have different naming rules.
    feature_pack_slug: str | None = None
    scheduled_commands: frozenset[str] = frozenset()
    inbound_routes: frozenset[str] = frozenset()
    outbound_services: frozenset[str] = frozenset()
    ui_features: frozenset[str] = frozenset()
    always_on: bool = False


def _c(*, pack=None, commands=(), routes=(), services=(), ui=(), always_on=False):
    return ModuleCapabilities(
        pack, frozenset(commands), frozenset(routes), frozenset(services),
        frozenset(ui), always_on,
    )


MODULE_CAPABILITIES: dict[str, ModuleCapabilities] = {
    "plausible": _c(pack="plausible", routes=("analytics.pageview", "email.tracking"), services=("plausible.analytics", "email_tracking.delivery"), ui=("modules.plausible",)),
    "syncro": _c(pack="syncro", routes=("syncro.import",), services=("syncro.api",), ui=("syncro",)),
    "ollama": _c(pack="ollama", commands=("process_transcription",), services=("ollama.ai",), ui=("ai.ollama",)),
    "smtp": _c(pack="smtp", services=("smtp.delivery",), ui=("modules.smtp",)),
    "smtp2go": _c(routes=("webhooks.smtp2go",), services=("smtp2go.delivery",), ui=("modules.smtp2go",)),
    "imap": _c(pack="imap", commands=("imap_sync:*",), services=("imap.mailbox",), ui=("mail.imap",)),
    "receive-sms": _c(pack="receive_sms", routes=("webhooks.receive_sms",), services=("receive_sms.admin",), ui=("receive_sms",)),
    "calls": _c(pack="calls", routes=("webhooks.calls",), services=("calls.admin",), ui=("calls",)),
    "m365-mail": _c(pack="m365_mail", commands=("sync_m365_mailboxes", "m365_mail_sync:*"), services=("m365_mail.graph",), ui=("mail.m365",)),
    "tacticalrmm": _c(pack="tacticalrmm", commands=("sync_tactical_assets", "push_tactical_companies", "pull_tactical_companies", "refresh_company_ids", "update_tray_icon_installer"), routes=("tacticalrmm.actions",), services=("tacticalrmm.api", "tacticalrmm.company_sync", "tacticalrmm.tray"), ui=("tacticalrmm",)),
    "ntfy": _c(pack="ntfy", services=("ntfy.delivery",), ui=("modules.ntfy",)),
    "apprise": _c(services=("apprise.delivery",), ui=("modules.apprise",)),
    "uptimekuma": _c(pack="uptimekuma", routes=("webhooks.uptimekuma",), services=("uptimekuma.api",), ui=("uptimekuma",)),
    "chatgpt-mcp": _c(pack="chatgpt_mcp", routes=("mcp.chatgpt",), services=("chatgpt.mcp",), ui=("ai.chatgpt_mcp",)),
    "ollama-mcp": _c(routes=("mcp.ollama",), services=("ollama.mcp",), ui=("ai.ollama_mcp",)),
    "xero": _c(pack="xero", commands=("sync_to_xero", "sync_to_xero_auto_send", "refresh_company_ids"), routes=("webhooks.xero", "oauth.xero"), services=("xero.api",), ui=("xero",)),
    "sms-gateway": _c(pack="sms_gateway", services=("sms_gateway.delivery",), ui=("modules.sms_gateway",)),
    "m365-admin": _c(pack="m365_admin", commands=("sync_m365_data", "sync_o365", "sync_m365_email_domains", "sync_m365_licenses", "sync_m365_contacts", "refresh_m365_consent_status", "refresh_company_ids"), services=("m365_admin.graph",), ui=("m365.admin",)),
    "call-recordings": _c(pack="call_recordings", commands=("sync_recordings", "queue_transcriptions"), services=("call_recordings.discovery", "call_recordings.import"), ui=("call_recordings",)),
    "whisperx": _c(commands=("queue_transcriptions", "process_transcription"), services=("whisperx.transcription",), ui=("whisperx",)),
    "unifi-talk": _c(commands=("sync_unifi_talk_recordings",), services=("unifi_talk.recordings",), ui=("unifi_talk",)),
    "reprocess-ai": _c(pack="reprocess_ai", services=("tickets.reprocess_ai",), ui=("tickets.reprocess_ai",), always_on=True),
    "password-pusher": _c(pack="password_pusher", services=("password_pusher.api",), ui=("password_pusher",)),
    "hudu": _c(pack="hudu", services=("hudu.api",), ui=("hudu",)),
    "huntress": _c(pack="huntress", commands=("sync_huntress",), services=("huntress.api",), ui=("huntress",)),
    "trello": _c(pack="trello", routes=("webhooks.trello",), services=("trello.api",), ui=("trello",)),
    "solidtime": _c(pack="solidtime", commands=("solidtime_reconcile",), services=("solidtime.api",), ui=("solidtime",)),
    "matrix-chat-assign": _c(pack="matrix_chat_assign", commands=("matrix_chat_assign",), services=("matrix.assignment",), ui=("matrix.chat_assign",)),
}


def feature_pack_for_module(module_slug: str) -> str | None:
    """Return the explicitly configured Python pack for a database slug."""
    capability = MODULE_CAPABILITIES.get(module_slug)
    return capability.feature_pack_slug if capability else None


def module_for_feature_pack(feature_pack_slug: str) -> str | None:
    """Return the database slug owning a Python pack, without normalisation."""
    return next((slug for slug, capability in MODULE_CAPABILITIES.items()
                 if capability.feature_pack_slug == feature_pack_slug), None)


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


# Registration inventories are intentionally separate from ownership.  Adding a
# capability without wiring its guard/dispatcher therefore fails validation.
REGISTERED_SCHEDULED_COMMANDS = frozenset(
    command for capabilities in MODULE_CAPABILITIES.values()
    for command in capabilities.scheduled_commands
)
REGISTERED_INBOUND_ROUTE_GUARDS = frozenset(
    route for capabilities in MODULE_CAPABILITIES.values()
    for route in capabilities.inbound_routes
)
REGISTERED_UI_FEATURES = frozenset(
    key for capabilities in MODULE_CAPABILITIES.values()
    for key in capabilities.ui_features
)
REGISTERED_EXTERNAL_SERVICE_GUARDS = frozenset(
    service for capabilities in MODULE_CAPABILITIES.values()
    for service in capabilities.outbound_services
)


def validate_capability_registry(
    default_modules: Iterable[Mapping[str, object]],
    *,
    configured_feature_packs: Iterable[str] = (),
    registered_commands: Iterable[str] = REGISTERED_SCHEDULED_COMMANDS,
    ui_keys: Iterable[str] = REGISTERED_UI_FEATURES,
    guarded_routes: Iterable[str] = REGISTERED_INBOUND_ROUTE_GUARDS,
    guarded_services: Iterable[str] = REGISTERED_EXTERNAL_SERVICE_GUARDS,
) -> list[str]:
    """Return actionable consistency errors for all module capability types."""
    errors: list[str] = []
    defaults = {str(module.get("slug") or "") for module in default_modules}
    commands = set(registered_commands)
    routes = set(guarded_routes)
    services = set(guarded_services)

    for slug, capability in MODULE_CAPABILITIES.items():
        if slug not in defaults:
            errors.append(f"capability references unknown DEFAULT_MODULES slug: {slug}")
        pack = capability.feature_pack_slug
        if pack and importlib.util.find_spec(f"app.features.{pack}") is None:
            errors.append(f"configured feature-pack slug does not exist: {pack} (module {slug})")
        for command in capability.scheduled_commands:
            if command not in commands:
                errors.append(f"module-owned scheduled command is not registered: {slug}:{command}")
        for route in capability.inbound_routes:
            if route not in routes:
                errors.append(f"module route is mounted without the enabled dependency: {slug}:{route}")
        for service in capability.outbound_services:
            if service not in services:
                errors.append(f"external-call service lacks the common enabled guard: {slug}:{service}")

    known_ui = {key for capability in MODULE_CAPABILITIES.values() for key in capability.ui_features}
    for key in ui_keys:
        if key not in known_ui:
            errors.append(f"module-backed sidebar/UI key has no known module: {key}")

    for pack in configured_feature_packs:
        if importlib.util.find_spec(f"app.features.{pack}") is None:
            errors.append(f"configured feature-pack slug does not exist: {pack}")
    return errors
