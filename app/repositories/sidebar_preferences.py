from __future__ import annotations

import json
from typing import Any

from app.core.database import db

PROTECTED_MENU_KEYS: set[str] = {"/admin/profile"}
ALLOWED_GROUP_ICONS: set[str] = {
    "folder",
    "shop",
    "briefcase",
    "grid",
    "star",
    "users",
    "support",
    "server",
    "shield",
    "chart",
    "settings",
    "automation",
    "phone",
    "history",
}

# Bumped whenever the shipped default layout changes shape. Saved preferences
# carry the version they were written against so legacy (pre-grouping) rows
# can be upgraded to the grouped default instead of staying a flat list.
SIDEBAR_LAYOUT_VERSION = 1

# The default left menu. Keys are the sidebar hrefs (or ``data-menu-key``
# values). Items a user cannot access are simply absent from their sidebar, and
# groups left with no accessible items are not rendered, so one layout serves
# every role. Menu entries not listed here fall to the bottom, ungrouped.
_DEFAULT_LAYOUT: tuple[tuple[str, ...] | tuple[str, str, str, tuple[str, ...]], ...] = (
    ("/",),
    ("/search",),
    ("/notifications",),
    ("/chat",),
    (
        "__group__:default-support",
        "Support",
        "support",
        (
            "/tickets",
            "/admin/tickets",
            "/admin/issues",
            "/service-status",
            "/knowledge-base",
            "/documentation-search",
            "/myforms",
            "/help",
        ),
    ),
    (
        "__group__:default-commerce",
        "Sales & Billing",
        "shop",
        (
            "/shop",
            "/quotes",
            "/orders",
            "/invoices",
            "/subscriptions",
            "/admin/marketing",
        ),
    ),
    ("/m365",),
    (
        "__group__:default-infrastructure",
        "Assets & Network",
        "server",
        (
            "/assets",
            "/devices",
            "/ipam",
            "/racks",
            "/network-map",
            "/websites",
            "/expirations",
        ),
    ),
    (
        "__group__:default-company",
        "Company",
        "briefcase",
        (
            "/staff",
            "/processes",
            "/shared-credentials",
            "/voice-monitor",
        ),
    ),
    (
        "__group__:default-security",
        "Security & Compliance",
        "shield",
        (
            "/compliance",
            "/compliance-checks",
            "/bcp",
            "/defender",
            "/dmarc",
        ),
    ),
    (
        "__group__:default-reports",
        "Reports",
        "chart",
        (
            "/reports/company-overview",
            "/reporting",
            "/admin/backup-summary",
        ),
    ),
    ("/admin/profile",),
    (
        "__group__:default-admin",
        "Administration",
        "settings",
        (
            "/admin/companies",
            "/admin/users",
            "/admin/roles",
            "/admin/sessions",
            "/admin/impersonation",
            "/admin/approvals",
            "/admin/api-keys",
            "/admin/modules",
            "/admin/feature-packs",
            "/admin/system-updates",
            "/admin/tray/configurations",
        ),
    ),
    (
        "__group__:default-automation",
        "Automation & AI",
        "automation",
        (
            "/admin/scheduled-tasks",
            "/admin/cron-calendar",
            "/admin/webhooks",
            "/admin/message-templates",
            "/admin/rag",
            "/admin/ai-quality",
            "/admin/chat/ai-tag-synonyms",
        ),
    ),
    (
        "__group__:default-calls",
        "Calls & Voice",
        "phone",
        (
            "/admin/calls",
            "/admin/call-recordings",
            "/admin/voice-monitor",
        ),
    ),
    (
        "__group__:default-logs",
        "Logs & History",
        "history",
        (
            "/admin/backup-jobs",
            "/admin/change-log",
            "/admin/audit-logs",
        ),
    ),
)


ROLE_SWITCHER_DISPLAY_MODES = ("full", "icon", "hidden")


def build_default_sidebar_preferences() -> dict[str, Any]:
    """Return a fresh copy of the default, logically grouped left menu."""

    order: list[str] = []
    groups: list[dict[str, Any]] = []
    for entry in _DEFAULT_LAYOUT:
        order.append(entry[0])
        if len(entry) == 4:
            group_id, label, icon, items = entry  # type: ignore[misc]
            groups.append({"id": group_id, "label": label, "icon": icon, "items": list(items)})
    return {
        "version": SIDEBAR_LAYOUT_VERSION,
        "navigation_style": "top",
        "role_switcher_display": "full",
        "order": order,
        "hidden": [],
        "groups": groups,
    }


def default_group_ids() -> list[str]:
    return [group["id"] for group in build_default_sidebar_preferences()["groups"]]


def _normalise_menu_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    return key[:120]


def _coerce_preferences(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "version": SIDEBAR_LAYOUT_VERSION,
            "navigation_style": "top",
            "role_switcher_display": "full",
            "order": [],
            "hidden": [],
            "groups": [],
        }

    navigation_style = "sidebar" if payload.get("navigation_style") == "sidebar" else "top"
    # Only super admins see the role switcher: a full selector, a compact icon
    # drop-down, or hidden entirely.
    role_switcher_display = payload.get("role_switcher_display")
    if role_switcher_display not in ROLE_SWITCHER_DISPLAY_MODES:
        role_switcher_display = "full"

    order_values = payload.get("order") if isinstance(payload.get("order"), list) else []
    hidden_values = payload.get("hidden") if isinstance(payload.get("hidden"), list) else []

    order: list[str] = []
    hidden: list[str] = []
    seen_order: set[str] = set()
    seen_hidden: set[str] = set()

    for entry in order_values:
        key = _normalise_menu_key(entry)
        if key and key not in seen_order:
            seen_order.add(key)
            order.append(key)

    for entry in hidden_values:
        key = _normalise_menu_key(entry)
        if key and key not in seen_hidden:
            seen_hidden.add(key)
            hidden.append(key)

    hidden = [key for key in hidden if key not in PROTECTED_MENU_KEYS]

    groups: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    assigned_items: set[str] = set()
    raw_groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        group_id = _normalise_menu_key(raw_group.get("id"))
        if not group_id.startswith("__group__:") or group_id in seen_groups:
            continue
        label = str(raw_group.get("label") or "").strip()[:60]
        if not label:
            continue
        icon = str(raw_group.get("icon") or "folder").strip().lower()
        if icon not in ALLOWED_GROUP_ICONS:
            icon = "folder"
        items: list[str] = []
        raw_items = raw_group.get("items") if isinstance(raw_group.get("items"), list) else []
        for raw_item in raw_items:
            item = _normalise_menu_key(raw_item)
            if (
                item
                and item not in assigned_items
                and item not in PROTECTED_MENU_KEYS
                and not item.startswith("__group__:")
                and not item.startswith("__divider__:")
                and not item.startswith("__spacer__:")
            ):
                items.append(item)
                assigned_items.add(item)
        seen_groups.add(group_id)
        groups.append({"id": group_id, "label": label, "icon": icon, "items": items})

    # Group markers are useful in the order even if a client omitted them.
    for group in groups:
        if group["id"] not in seen_order:
            order.append(group["id"])

    return {
        "version": SIDEBAR_LAYOUT_VERSION,
        "navigation_style": navigation_style,
        "role_switcher_display": role_switcher_display,
        "order": order,
        "hidden": hidden,
        "groups": groups,
    }


def resolve_stored_preferences(payload: Any) -> dict[str, Any]:
    """Turn a stored preferences document into the layout the sidebar uses.

    Users who never customised the menu get the grouped default. Rows saved
    before grouping existed (no ``version`` and no groups) were only ever a
    flat order plus hidden items, so they are upgraded to the default layout
    while keeping the links the user chose to hide.
    """

    if not isinstance(payload, dict) or not payload:
        return build_default_sidebar_preferences()
    coerced = _coerce_preferences(payload)
    if "version" not in payload and not coerced["groups"]:
        defaults = build_default_sidebar_preferences()
        defaults["hidden"] = coerced["hidden"]
        return defaults
    return coerced


async def get_user_sidebar_preferences(user_id: int) -> dict[str, Any]:
    row = await db.fetch_one(
        """
        SELECT preferences_json
        FROM user_sidebar_preferences
        WHERE user_id = %s
        """,
        (user_id,),
    )
    if not row:
        return build_default_sidebar_preferences()

    raw_preferences = row.get("preferences_json")
    parsed: Any
    if isinstance(raw_preferences, str):
        try:
            parsed = json.loads(raw_preferences)
        except json.JSONDecodeError:
            parsed = {}
    else:
        parsed = raw_preferences

    return resolve_stored_preferences(parsed)


async def reset_user_sidebar_preferences(user_id: int) -> dict[str, Any]:
    """Drop the user's saved layout so they follow the shipped default again.

    Deleting (rather than saving a copy of the default) means later changes to
    the default layout reach this user too.
    """

    await db.execute(
        "DELETE FROM user_sidebar_preferences WHERE user_id = %s",
        (user_id,),
    )
    return build_default_sidebar_preferences()


async def upsert_user_sidebar_preferences(
    user_id: int,
    preferences: dict[str, Any],
) -> dict[str, Any]:
    safe_preferences = _coerce_preferences(preferences)
    payload = json.dumps(safe_preferences)

    await db.execute(
        """
        INSERT INTO user_sidebar_preferences (user_id, preferences_json)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            preferences_json = VALUES(preferences_json),
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, payload),
    )

    return safe_preferences
