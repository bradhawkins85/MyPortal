"""Tri-state menu permission catalogue and compatibility helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

AccessLevel = Literal["none", "read", "write"]
ACCESS_LEVELS: tuple[AccessLevel, ...] = ("none", "read", "write")


@dataclass(frozen=True)
class MenuPermission:
    key: str
    label: str
    group: str
    description: str
    legacy_permissions: tuple[str, ...] = ()
    legacy_boolean: str | None = None
    admin_only: bool = False


MENU_PERMISSIONS: tuple[MenuPermission, ...] = (
    MenuPermission("menu.dashboard", "Dashboard", "General", "View the company dashboard and overview."),
    MenuPermission("menu.service_status", "Service status", "General", "View service status dashboards."),
    MenuPermission("menu.notifications", "Notifications", "General", "View and manage notification settings.", admin_only=True),
    MenuPermission("menu.knowledge_base", "Knowledge base", "General", "View knowledge base articles."),
    MenuPermission("menu.help", "Help", "General", "View help and support documentation."),
    MenuPermission("menu.chat", "Chat", "General", "Access the chat interface.", ("chat.access",), "can_access_chat"),
    MenuPermission("menu.tickets", "Tickets", "Company", "View tickets; write access allows ticket actions.", ("helpdesk.technician",), None),
    MenuPermission("menu.issues", "Issue tracker", "Company", "View or manage issue tracker items.", ("issues.manage",), "can_manage_issues"),
    MenuPermission("menu.marketing", "Marketing", "Company", "View or manage marketing pages and contacts.", ("marketing.access",), None),
    MenuPermission("menu.shop", "Shop", "Commerce", "Browse shop products; write access allows cart actions.", ("shop.access",), "can_access_shop"),
    MenuPermission("menu.quotes", "Quotes", "Commerce", "View quotes; write access allows quote actions.", (), "can_access_quotes"),
    MenuPermission("menu.orders", "Orders", "Commerce", "View orders; write access allows order actions.", ("orders.access",), "can_access_orders"),
    MenuPermission("menu.forms", "Forms", "Company", "View forms; write access allows submissions.", ("forms.access",), "can_access_forms"),
    MenuPermission("menu.assets", "Assets", "Company", "View assets; write access allows asset changes.", ("assets.manage",), "can_manage_assets"),
    MenuPermission("menu.m365.configuration", "Office 365 Configuration", "Office 365", "View or manage Microsoft 365 tenant configuration.", ("licenses.manage",), "can_manage_licenses"),
    MenuPermission("menu.m365.best_practices", "Office 365 Best Practices", "Office 365", "View or run Microsoft 365 best-practice checks.", ("m365_best_practices.access",), "can_view_m365_best_practices"),
    MenuPermission("menu.m365.user_mailboxes", "User Mailboxes", "Office 365", "View user mailboxes; write access allows mailbox actions.", ("m365_user_mailboxes.access",), "can_view_m365_user_mailboxes"),
    MenuPermission("menu.m365.shared_mailboxes", "Shared Mailboxes", "Office 365", "View shared mailboxes; write access allows mailbox actions.", ("m365_shared_mailboxes.access",), "can_view_m365_shared_mailboxes"),
    MenuPermission("menu.m365.licenses", "Licenses", "Office 365", "View licenses; write access allows license changes.", ("licenses.manage", "licenses.order"), "can_manage_licenses"),
    MenuPermission("menu.m365.diagnostics", "Office 365 Diagnostics", "Office 365", "View or repair Microsoft 365 diagnostics.", admin_only=True),
    MenuPermission("menu.subscriptions", "Subscriptions", "Commerce", "View subscriptions; write access allows subscription change/order actions.", ("billing.manage", "licenses.manage", "cart.access"), None),
    MenuPermission("menu.invoices", "Invoices", "Commerce", "View invoices; write access allows invoice management.", ("billing.manage", "invoices.manage"), "can_manage_invoices"),
    MenuPermission("menu.staff", "Staff", "Company", "View staff; write access allows staff management.", ("staff.manage",), "can_manage_staff"),
    MenuPermission("menu.compliance", "Compliance", "Compliance", "View or manage Essential 8 compliance.", ("compliance.access",), "can_view_compliance"),
    MenuPermission("menu.reports", "Reports", "Reporting", "View generated company reports."),
    MenuPermission("menu.reporting", "Reporting", "Reporting", "View or build reporting dashboards.", ("helpdesk.technician",), None),
    MenuPermission("menu.compliance_checks", "Compliance Checks", "Compliance", "View assigned compliance checks.", ("compliance_checks.access",), "can_view_compliance_checks"),
    MenuPermission("menu.compliance_checks.library", "Compliance Checks Library", "Compliance", "Manage compliance check library items.", ("compliance_checks.manage",), "can_manage_compliance_checks", admin_only=True),
    MenuPermission("menu.continuity", "Continuity", "Compliance", "View or manage business continuity plans.", ("continuity.access", "bcp:view", "bcp:edit"), "can_view_bcp"),
    MenuPermission("menu.admin.profile", "My Profile", "Administration", "View and update the user's administration profile."),
    MenuPermission("menu.admin.impersonation", "Impersonation", "Administration", "Access impersonation administration.", admin_only=True),
    MenuPermission("menu.admin.call_recordings", "Call Recordings", "Administration", "Access call recordings administration.", admin_only=True),
    MenuPermission("menu.admin.scheduled_tasks", "Scheduled Tasks", "Administration", "Access scheduled task administration.", admin_only=True),
    MenuPermission("menu.admin.backup_history", "Backup History", "Administration", "Access backup history.", admin_only=True),
    MenuPermission("menu.admin.backup_summary", "Backup Summary", "Administration", "Access backup summary.", admin_only=True),
    MenuPermission("menu.admin.message_templates", "Message Templates", "Administration", "Manage message templates.", admin_only=True),
    MenuPermission("menu.admin.webhooks", "Webhooks", "Administration", "Monitor and manage webhooks.", admin_only=True),
    MenuPermission("menu.admin.api_keys", "API Keys", "Administration", "Manage API keys.", admin_only=True),
    MenuPermission("menu.admin.modules", "Modules", "Administration", "Manage integration modules.", admin_only=True),
    MenuPermission("menu.admin.feature_packs", "Feature Packs", "Administration", "Manage feature packs.", admin_only=True),
    MenuPermission("menu.admin.roles", "Roles", "Administration", "Manage roles and permissions.", admin_only=True),
    MenuPermission("menu.admin.change_log", "Change Log", "Administration", "View application change logs.", admin_only=True),
    MenuPermission("menu.admin.audit_trail", "Audit Trail", "Administration", "View audit logs.", ("audit.view",), None, True),
    MenuPermission("menu.admin.company", "Company admin", "Administration", "Manage company administration settings.", ("company.admin", "company.manage", "membership.manage"), "is_admin"),
)

MENU_PERMISSION_MAP = {item.key: item for item in MENU_PERMISSIONS}
LEGACY_TO_MENU: dict[str, list[tuple[str, AccessLevel]]] = {}
for item in MENU_PERMISSIONS:
    for legacy in item.legacy_permissions:
        level: AccessLevel = "write" if any(part in legacy for part in ("manage", "order", "admin", "edit")) else "read"
        LEGACY_TO_MENU.setdefault(legacy, []).append((item.key, level))


def catalogue_for_api() -> list[dict[str, Any]]:
    return [asdict(item) | {"levels": list(ACCESS_LEVELS)} for item in MENU_PERMISSIONS]


def normalize_access_level(value: Any) -> AccessLevel:
    value_str = str(value or "none").strip().lower().replace("-", "_")
    aliases = {
        "no_access": "none",
        "no access": "none",
        "readonly": "read",
        "read_only": "read",
        "read only": "read",
        "read/write": "write",
        "read_write": "write",
        "read write": "write",
        "rw": "write",
    }
    value_str = aliases.get(value_str, value_str)
    if value_str not in ACCESS_LEVELS:
        raise ValueError(f"Invalid menu permission level: {value}")
    return value_str  # type: ignore[return-value]


def merge_access(existing: AccessLevel, new: AccessLevel) -> AccessLevel:
    rank = {"none": 0, "read": 1, "write": 2}
    return new if rank[new] > rank[existing] else existing


def normalize_menu_permissions(raw: Any) -> dict[str, AccessLevel]:
    """Normalize dict or legacy list permissions into a full menu permission map."""
    normalized: dict[str, AccessLevel] = {item.key: "none" for item in MENU_PERMISSIONS}
    if not raw:
        return normalized

    if isinstance(raw, dict):
        source = raw.get("menu") if isinstance(raw.get("menu"), dict) else raw
        for key, value in source.items():
            if key in MENU_PERMISSION_MAP:
                normalized[key] = normalize_access_level(value)
        # Also accept a legacy list nested under permissions for compatibility.
        legacy = raw.get("legacy") or raw.get("permissions")
        if isinstance(legacy, list):
            legacy_map = normalize_menu_permissions(legacy)
            for key, level in legacy_map.items():
                normalized[key] = merge_access(normalized[key], level)
        return normalized

    if isinstance(raw, list):
        for permission in raw:
            if not isinstance(permission, str):
                continue
            if permission in MENU_PERMISSION_MAP:
                normalized[permission] = merge_access(normalized[permission], "write")
                continue
            mapped_items = LEGACY_TO_MENU.get(permission, [])
            for key, level in mapped_items:
                normalized[key] = merge_access(normalized[key], level)
        return normalized

    return normalized


def compact_menu_permissions(raw: Any) -> dict[str, AccessLevel]:
    return {key: level for key, level in normalize_menu_permissions(raw).items() if level != "none"}


def menu_permissions_to_legacy(menu_permissions: Any) -> list[str]:
    normalized = normalize_menu_permissions(menu_permissions)
    legacy: set[str] = set()
    for item in MENU_PERMISSIONS:
        level = normalized.get(item.key, "none")
        if level == "none":
            continue
        if item.legacy_permissions:
            if level == "write":
                legacy.update(item.legacy_permissions)
            else:
                legacy.update(p for p in item.legacy_permissions if not any(part in p for part in ("manage", "order", "admin", "edit")))
    return sorted(legacy)


def menu_has_access(menu_access: dict[str, Any] | None, key: str, *, write: bool = False) -> bool:
    if not menu_access:
        return False
    level = normalize_access_level(menu_access.get(key, "none"))
    return level == "write" if write else level in {"read", "write"}
