"""Deployment-disableable core components.

Some sections of MyPortal are not self-contained ``app/features/<slug>/``
packs: they are mounted from core routers or share a pack with other screens.
They are still switchable through ``DISABLED_FEATURE_PACKS`` so operators have
one kill-switch list.  Each component owns a set of URL path prefixes; a
disabled component hides its navigation entries and its prefixes return 404.

This module must stay free of application imports because the settings model
validates ``DISABLED_FEATURE_PACKS`` against it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoreComponent:
    slug: str
    label: str
    description: str
    path_prefixes: tuple[str, ...]
    # Built-in feature pack that serves these routes.  Disabling that pack
    # also makes the component unavailable.
    parent_pack: str | None = None


CORE_COMPONENTS: tuple[CoreComponent, ...] = (
    CoreComponent(
        "network_devices",
        "Network Devices",
        "Discovered devices, network scanners and device types.",
        ("/devices", "/network-scan"),
        parent_pack="assets",
    ),
    CoreComponent(
        "ipam",
        "IPAM",
        "IP address management networks and addresses.",
        (
            "/ipam",
            "/devices/ipam-preview",
            "/devices/ipam-import",
            "/api/infrastructure/networks",
            "/api/infrastructure/addresses",
        ),
        parent_pack="assets",
    ),
    CoreComponent(
        "racks",
        "Racks",
        "Rack layouts, equipment and reservations.",
        (
            "/racks",
            "/api/infrastructure/racks",
            "/api/infrastructure/rack-equipment",
            "/api/infrastructure/rack-reservations",
        ),
        parent_pack="assets",
    ),
    CoreComponent(
        "defender",
        "Windows Defender",
        "Defender detections, exclusions and tray agent Defender commands.",
        ("/defender", "/api/defender", "/api/tray/defender"),
    ),
    CoreComponent(
        "office365",
        "Office 365",
        "Office 365 configuration, best practices, mailboxes, signatures, "
        "licenses, diagnostics, out of office and spam purge.",
        ("/m365", "/licenses", "/api/licenses"),
    ),
    CoreComponent(
        "shared_credentials",
        "Shared Credentials",
        "Credential vault, shared credentials page and credential share links.",
        ("/shared-credentials", "/credential-share", "/api/vault"),
    ),
    CoreComponent(
        "backup_history",
        "Backup History",
        "Backup job history administration.",
        ("/admin/backup-jobs", "/api/backup-jobs"),
        parent_pack="backups",
    ),
    CoreComponent(
        "backup_summary",
        "Backup Summary",
        "Backup summary dashboard.",
        ("/admin/backup-summary",),
        parent_pack="backups",
    ),
    CoreComponent(
        "rag_index",
        "RAG Index",
        "RAG index administration, diagnostics and matching controls.",
        ("/admin/rag", "/rag", "/api/rag"),
    ),
    CoreComponent(
        "ai_quality",
        "AI Quality",
        "AI quality review dashboard.",
        ("/admin/ai-quality",),
    ),
    CoreComponent(
        "ai_tag_synonyms",
        "AI Tag Synonyms",
        "AI tag synonym groups used by chat assignment.",
        ("/admin/chat/ai-tag-synonyms", "/chat/configuration"),
    ),
    CoreComponent(
        "outlook_contacts",
        "Outlook Contacts",
        "My Profile Outlook contacts connection and the ticket "
        "\"Check Outlook contacts\" lookup.",
        ("/admin/profile/m365-contacts", "/api/profile/m365-contacts"),
    ),
    CoreComponent(
        "notification_contact",
        "Notification Contact",
        "My Profile notification contact (SMS mobile number) card.",
        (),
    ),
    CoreComponent(
        "email_signature",
        "Email Signature",
        "My Profile email signature card.",
        (),
    ),
    CoreComponent(
        "click_to_call",
        "Click to Call",
        "My Profile click-to-call settings and click-to-call phone links.",
        ("/api/click-to-call",),
    ),
    CoreComponent(
        "forms",
        "Forms",
        "Forms (OpnForm) portal pages, forms administration and forms API.",
        ("/myforms", "/forms", "/admin/forms", "/api/forms"),
    ),
    CoreComponent(
        "essential8",
        "Essential 8 Compliance",
        "Essential 8 compliance dashboard, controls, API, marketing help "
        "links and company report sections. Compliance Checks is unaffected.",
        ("/compliance", "/api/essential8", "/admin/marketing/essential8-help-links"),
        parent_pack="compliance",
    ),
    CoreComponent(
        "gmp_glp",
        "GMP/GLP Compliance Checks",
        "GMP and GLP categories and predefined checks in Compliance Checks. "
        "Other compliance content is unaffected.",
        (),
    ),
    CoreComponent(
        "tray",
        "Tray Agent",
        "Tray agent API, installers, devices and tray settings.",
        ("/admin/tray", "/api/tray", "/tray"),
    ),
)

CORE_COMPONENTS_BY_SLUG: dict[str, CoreComponent] = {
    component.slug: component for component in CORE_COMPONENTS
}
CORE_COMPONENT_SLUGS: tuple[str, ...] = tuple(CORE_COMPONENTS_BY_SLUG)


def _path_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def components_for_path(path: str) -> tuple[CoreComponent, ...]:
    """Return every core component owning *path* (segment-aware prefix match)."""

    return tuple(
        component
        for component in CORE_COMPONENTS
        if any(_path_matches(path, prefix) for prefix in component.path_prefixes)
    )


__all__ = [
    "CORE_COMPONENTS",
    "CORE_COMPONENTS_BY_SLUG",
    "CORE_COMPONENT_SLUGS",
    "CoreComponent",
    "components_for_path",
]
