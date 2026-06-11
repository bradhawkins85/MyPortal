"""Central catalogue of permissions represented by the left navigation menu.

The sidebar should use these stable permission keys when deciding whether a menu
entry is visible.  Route handlers can also reference the same catalogue when
checking whether a user can read or write a feature.  The catalogue is purposely
static and side-effect free so it can be imported safely by templates, tests,
startup tasks, and API documentation code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Iterable


class MenuAccessLevel(str, Enum):
    """Supported access levels for a left-menu permission."""

    NONE = "none"
    READ = "read"
    WRITE = "write"


ACCESS_LEVEL_LABELS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        MenuAccessLevel.NONE.value: "No Access",
        MenuAccessLevel.READ.value: "Read Only",
        MenuAccessLevel.WRITE.value: "Read/Write",
    }
)

DEFAULT_ACCESS_LEVELS: Final[tuple[MenuAccessLevel, ...]] = (
    MenuAccessLevel.NONE,
    MenuAccessLevel.READ,
    MenuAccessLevel.WRITE,
)


@dataclass(frozen=True, slots=True)
class MenuPermission:
    """Permission metadata for one assignable left-menu item."""

    key: str
    label: str
    route_prefixes: tuple[str, ...]
    supported_access_levels: tuple[MenuAccessLevel, ...] = DEFAULT_ACCESS_LEVELS
    legacy_flags: tuple[str, ...] = ()
    super_admin_only: bool = False
    always_visible: bool = False
    group: str | None = None

    def controls_route(self, path: str) -> bool:
        """Return whether this permission controls *path* by route prefix."""

        for prefix in self.route_prefixes:
            if prefix == "/":
                if path == "/":
                    return True
                continue
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return True
        return False


MENU_PERMISSIONS: Final[tuple[MenuPermission, ...]] = (
    # Always-visible top-level customer entries.
    MenuPermission("menu.dashboard", "Dashboard", ("/",), always_visible=True),
    MenuPermission("menu.service_status", "Service status", ("/service-status",), always_visible=True),
    MenuPermission("menu.knowledge_base", "Knowledge base", ("/knowledge-base",), always_visible=True),
    MenuPermission("menu.help", "Help", ("/help",), always_visible=True),
    MenuPermission("menu.reports.company_overview", "Reports", ("/reports",), always_visible=True),
    # Conditional top-level customer/company entries.
    MenuPermission("menu.notifications", "Notifications", ("/notifications",), super_admin_only=True),
    MenuPermission("menu.chat", "Chat", ("/chat",), legacy_flags=("can_access_chat",)),
    MenuPermission("menu.tickets", "Tickets", ("/tickets", "/admin/tickets")),
    MenuPermission("menu.issue_tracker", "Issue tracker", ("/admin/issues",)),
    MenuPermission("menu.marketing", "Marketing", ("/admin/marketing",), super_admin_only=True),
    MenuPermission("menu.shop", "Shop", ("/shop",), legacy_flags=("can_access_shop",)),
    MenuPermission("menu.quotes", "Quotes", ("/quotes",), legacy_flags=("can_access_quotes",)),
    MenuPermission("menu.orders", "Orders", ("/orders",), legacy_flags=("can_access_orders",)),
    MenuPermission("menu.forms", "Forms", ("/myforms", "/forms"), legacy_flags=("can_access_forms",)),
    MenuPermission("menu.assets", "Assets", ("/assets",), legacy_flags=("can_manage_assets",)),
    # Office 365 grouped menu children.  These are intentionally separate so a
    # user can, for example, read mailbox pages without seeing configuration.
    MenuPermission("menu.m365.configuration", "Office 365 Configuration", ("/m365",), legacy_flags=("can_manage_licenses",), group="Office 365"),
    MenuPermission("menu.m365.best_practices", "Best Practices", ("/m365/best-practices",), legacy_flags=("can_view_m365_best_practices",), group="Office 365"),
    MenuPermission("menu.m365.user_mailboxes", "User Mailboxes", ("/m365/mailboxes/users",), legacy_flags=("can_view_m365_user_mailboxes",), group="Office 365"),
    MenuPermission("menu.m365.shared_mailboxes", "Shared Mailboxes", ("/m365/mailboxes/shared",), legacy_flags=("can_view_m365_shared_mailboxes",), group="Office 365"),
    MenuPermission("menu.m365.licenses", "Licenses", ("/licenses",), legacy_flags=("can_manage_licenses",), group="Office 365"),
    MenuPermission("menu.m365.diagnostics", "Diagnostics", ("/m365/diagnostics",), super_admin_only=True, group="Office 365"),
    MenuPermission("menu.subscriptions", "Subscriptions", ("/subscriptions",), legacy_flags=("can_manage_licenses", "can_access_cart")),
    MenuPermission("menu.invoices", "Invoices", ("/invoices",), legacy_flags=("can_manage_invoices",)),
    MenuPermission("menu.staff", "Staff", ("/staff",), legacy_flags=("can_manage_staff", "staff_permission")),
    MenuPermission("menu.compliance", "Compliance", ("/compliance",), legacy_flags=("can_view_compliance",)),
    MenuPermission("menu.reporting", "Reporting", ("/reporting", "/admin/reporting")),
    # Compliance Checks grouped menu children.
    MenuPermission("menu.compliance_checks.my_checks", "My Checks", ("/compliance-checks",), legacy_flags=("can_view_compliance_checks",), group="Compliance Checks"),
    MenuPermission("menu.compliance_checks.library", "Library", ("/admin/compliance-checks/library",), legacy_flags=("can_manage_compliance_checks",), super_admin_only=True, group="Compliance Checks"),
    MenuPermission("menu.bcp", "Continuity", ("/bcp",), legacy_flags=("can_view_bcp",)),
    # Administration section entries.
    MenuPermission("menu.admin.profile", "My Profile", ("/admin/profile",)),
    MenuPermission("menu.admin.impersonation", "Impersonation", ("/admin/impersonation",), super_admin_only=True),
    MenuPermission("menu.admin.call_recordings", "Call Recordings", ("/admin/call-recordings",), super_admin_only=True),
    MenuPermission("menu.admin.scheduled_tasks", "Scheduled Tasks", ("/admin/scheduled-tasks",), super_admin_only=True),
    MenuPermission("menu.admin.backup_history", "Backup History", ("/admin/backup-jobs",), super_admin_only=True),
    MenuPermission("menu.admin.backup_summary", "Backup Summary", ("/admin/backup-summary",), super_admin_only=True),
    MenuPermission("menu.admin.message_templates", "Message Templates", ("/admin/message-templates",), super_admin_only=True),
    MenuPermission("menu.admin.webhooks", "Webhook Monitor", ("/admin/webhooks",), super_admin_only=True),
    MenuPermission("menu.admin.companies", "Companies", ("/admin/companies",)),
    MenuPermission("menu.admin.company_admin", "Company admin", ("/admin/companies",)),
    MenuPermission("menu.admin.tray_settings", "Tray Settings", ("/admin/tray",), super_admin_only=True),
    MenuPermission("menu.admin.api_keys", "API Keys", ("/admin/api-keys",), super_admin_only=True),
    MenuPermission("menu.admin.modules", "Modules", ("/admin/modules",), super_admin_only=True),
    MenuPermission("menu.admin.feature_packs", "Feature Packs", ("/admin/feature-packs",), super_admin_only=True),
    MenuPermission("menu.admin.roles", "Roles", ("/admin/roles",), super_admin_only=True),
    MenuPermission("menu.admin.change_log", "Change Log", ("/admin/change-log",), super_admin_only=True),
    MenuPermission("menu.admin.audit_trail", "Audit Trail", ("/admin/audit-logs",), super_admin_only=True),
)

MENU_PERMISSION_BY_KEY: Final[MappingProxyType[str, MenuPermission]] = MappingProxyType(
    {permission.key: permission for permission in MENU_PERMISSIONS}
)


def get_menu_permission(key: str) -> MenuPermission:
    """Return the menu permission for *key* or raise ``KeyError``."""

    return MENU_PERMISSION_BY_KEY[key]


def iter_menu_permissions(*, group: str | None = None) -> Iterable[MenuPermission]:
    """Iterate menu permissions, optionally limited to a grouped menu label."""

    for permission in MENU_PERMISSIONS:
        if group is None or permission.group == group:
            yield permission


def permission_for_route(path: str) -> MenuPermission | None:
    """Return the most-specific permission controlling *path*, if any."""

    matches = [permission for permission in MENU_PERMISSIONS if permission.controls_route(path)]
    if not matches:
        return None
    return max(matches, key=lambda permission: max(len(prefix) for prefix in permission.route_prefixes))
