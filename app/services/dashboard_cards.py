"""Dashboard card registry and per-user dashboard builder.

This module defines a small, extensible registry of "dashboard cards" that
make up the consolidated portal Dashboard. Each card is a self-contained
descriptor: an id, presentation metadata, an async permission check (so we
never expose data the user cannot otherwise access), an async data loader,
and the Jinja partial used to render it.

A user's dashboard layout is stored as a JSON document in the existing
``user_preferences`` table under the key :data:`LAYOUT_PREFERENCE_KEY`. When
a user has no saved layout we synthesise one from the cards they are allowed
to see.

Adding a new card to the catalogue is a single declarative entry plus
(optionally) a tiny Jinja partial under
``app/templates/partials/dashboard_cards/``.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Mapping, Sequence

from fastapi import Request

from app.core.logging import log_error
from app.repositories import assets as asset_repo
from app.repositories import change_log as change_log_repo
from app.repositories import companies as company_repo
from app.repositories import invoices as invoice_repo
from app.repositories import licenses as license_repo
from app.repositories import notifications as notifications_repo
from app.repositories import staff as staff_repo
from app.repositories import tickets as tickets_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import user_preferences as user_preferences_repo
from app.repositories import users as user_repo
from app.repositories import webhook_events as webhook_events_repo
from app.security.session import session_manager
from app.services import company_access, modules

LAYOUT_PREFERENCE_KEY = "dashboard:layout:v1"

# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------

GRID_COLUMNS = 12
MIN_CARD_WIDTH = 2
MAX_CARD_WIDTH = 12
MIN_CARD_HEIGHT = 1
MAX_CARD_HEIGHT = 8
MAX_LAYOUT_CARDS = 32
MAX_GRID_ROWS = 60

# Named sizes mapped to (width, height) in grid cells.
SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "small": (3, 2),
    "medium": (4, 2),
    "wide": (6, 2),
    "large": (6, 3),
    "tall": (4, 4),
    "full": (12, 3),
}


# ---------------------------------------------------------------------------
# Card descriptor / context
# ---------------------------------------------------------------------------

@dataclass
class CardContext:
    """Per-request context passed to permission checks and data loaders."""

    request: Request
    user: Mapping[str, Any]
    is_super_admin: bool
    membership: Mapping[str, Any] | None
    active_company_id: int | None
    available_companies: Sequence[Mapping[str, Any]]
    module_lookup: Mapping[str, Mapping[str, Any]]

    @property
    def user_id(self) -> int | None:
        try:
            value = int(self.user.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def has_membership_permission(self, flag: str) -> bool:
        if self.is_super_admin:
            return True
        return bool((self.membership or {}).get(flag))

    def module_enabled(self, slug: str) -> bool:
        module = (self.module_lookup or {}).get(slug)
        return bool(module and module.get("enabled"))


PermissionCheck = Callable[[CardContext], "bool | Awaitable[bool]"]
DataLoader = Callable[[CardContext], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class CardDescriptor:
    id: str
    title: str
    description: str
    category: str
    template_partial: str
    permission_check: PermissionCheck
    data_loader: DataLoader
    default_size: str = "medium"
    refresh_interval_seconds: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _format_currency(amount: Any) -> str:
    if amount is None:
        amount = Decimal("0")
    if not isinstance(amount, Decimal):
        try:
            amount = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            amount = Decimal("0")
    try:
        quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        quantized = Decimal("0.00")
    return f"${quantized:,.2f}"


async def _safe_call(callable_obj: PermissionCheck, ctx: CardContext) -> bool:
    try:
        result = callable_obj(ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)
    except Exception as exc:  # pragma: no cover - defensive
        log_error("Dashboard card permission check failed", error=str(exc))
        return False


def _size_to_cells(size: str) -> tuple[int, int]:
    return SIZE_PRESETS.get(size, SIZE_PRESETS["medium"])


# ---------------------------------------------------------------------------
# Permission-check primitives (composable)
# ---------------------------------------------------------------------------

def _allow_any_user(ctx: CardContext) -> bool:
    return ctx.user_id is not None


def _require_super_admin(ctx: CardContext) -> bool:
    return ctx.is_super_admin


def _require_active_company(ctx: CardContext) -> bool:
    return ctx.active_company_id is not None


def _require_membership_flag(flag: str) -> PermissionCheck:
    def _check(ctx: CardContext) -> bool:
        if ctx.active_company_id is None:
            return False
        return ctx.has_membership_permission(flag)

    _check.__name__ = f"_require_membership_flag__{flag}"
    return _check


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

async def _load_companies_count(ctx: CardContext) -> Mapping[str, Any]:
    if ctx.is_super_admin:
        try:
            count = await company_repo.count_companies()
        except Exception as exc:
            log_error("Dashboard: companies.count loader failed", error=str(exc))
            count = len(ctx.available_companies)
        label = "Companies"
        description = "Organisations across the portal"
    else:
        count = len(ctx.available_companies)
        label = "My companies"
        description = "Companies you can access"
    return {
        "label": label,
        "value": count,
        "formatted": _format_int(count),
        "description": description,
    }


async def _load_users_count(ctx: CardContext) -> Mapping[str, Any]:
    try:
        count = await user_repo.count_users()
    except Exception as exc:
        log_error("Dashboard: users.count loader failed", error=str(exc))
        count = 0
    return {
        "label": "Portal users",
        "value": count,
        "formatted": _format_int(count),
        "description": "Registered accounts",
    }


async def _load_unread_notifications(ctx: CardContext) -> Mapping[str, Any]:
    count = 0
    if ctx.user_id is not None:
        try:
            count = await notifications_repo.count_notifications(
                user_id=ctx.user_id, read_state="unread"
            )
        except Exception as exc:
            log_error("Dashboard: notifications.unread loader failed", error=str(exc))
    return {
        "label": "Unread alerts",
        "value": count,
        "formatted": _format_int(count),
        "description": "Notifications awaiting review",
        "link": "/notifications",
    }


async def _load_webhook_queue(ctx: CardContext) -> Mapping[str, Any]:
    pending = failed = in_progress = 0
    try:
        pending = await webhook_events_repo.count_events_by_status("pending")
        failed = await webhook_events_repo.count_events_by_status("failed")
        in_progress = await webhook_events_repo.count_events_by_status("in_progress")
    except Exception as exc:
        log_error("Dashboard: webhooks.queue loader failed", error=str(exc))
    notes: list[str] = []
    if failed:
        notes.append(f"{_format_int(failed)} failing")
    if in_progress:
        notes.append(f"{_format_int(in_progress)} in progress")
    if not notes and pending:
        notes.append("Queued for retry")
    if not notes:
        notes.append("All clear")
    return {
        "label": "Webhook queue",
        "value": pending,
        "formatted": _format_int(pending),
        "description": ", ".join(notes),
        "link": "/admin/webhooks",
        "items": [
            {"label": "Pending", "value": pending, "formatted": _format_int(pending)},
            {"label": "In progress", "value": in_progress, "formatted": _format_int(in_progress)},
            {"label": "Failed", "value": failed, "formatted": _format_int(failed)},
        ],
    }


async def _load_my_open_tickets(ctx: CardContext) -> Mapping[str, Any]:
    count = 0
    if ctx.user_id is not None:
        try:
            count = await tickets_repo.count_tickets_for_user(
                ctx.user_id, status=["new", "open", "pending", "in_progress"]
            )
        except Exception as exc:
            log_error("Dashboard: tickets.my_open loader failed", error=str(exc))
    return {
        "label": "My open tickets",
        "value": count,
        "formatted": _format_int(count),
        "description": "Tickets you raised or follow",
        "link": "/tickets",
    }


async def _load_unassigned_tickets(ctx: CardContext) -> Mapping[str, Any]:
    count = 0
    try:
        from app.core.database import db as _db
        row = await _db.fetch_one(
            "SELECT COUNT(*) AS count FROM tickets WHERE assigned_user_id IS NULL "
            "AND status IN ('new','open','pending','in_progress')"
        )
        if row:
            count = int(row.get("count") or 0)
    except Exception as exc:
        log_error("Dashboard: tickets.unassigned loader failed", error=str(exc))
    return {
        "label": "Unassigned tickets",
        "value": count,
        "formatted": _format_int(count),
        "description": "Open tickets without an assigned owner",
        "link": "/admin/tickets",
    }


async def _load_assets_status(ctx: CardContext) -> Mapping[str, Any]:
    if ctx.active_company_id is None:
        return {"items": [], "total": 0, "formatted_total": "0"}
    try:
        assets = await asset_repo.list_company_assets(int(ctx.active_company_id))
    except Exception as exc:
        log_error("Dashboard: assets.status_mix loader failed", error=str(exc))
        assets = []
    counter: Counter[str] = Counter(
        (str(asset.get("status") or "Unspecified").strip() or "Unspecified")
        for asset in assets
    )
    items = [
        {"label": label, "value": value, "formatted": _format_int(value)}
        for label, value in counter.most_common()
    ]
    if not items:
        items.append({"label": "No assets yet", "value": 0, "formatted": "0"})
    return {"items": items, "total": len(assets), "formatted_total": _format_int(len(assets))}


async def _load_license_capacity(ctx: CardContext) -> Mapping[str, Any]:
    if ctx.active_company_id is None:
        return {
            "items": [],
            "total": 0,
            "allocated": 0,
            "available": 0,
            "utilisation": 0,
            "formatted": {"total": "0", "allocated": "0", "available": "0", "utilisation": "0%"},
        }
    try:
        licenses = await license_repo.list_company_licenses(int(ctx.active_company_id))
    except Exception as exc:
        log_error("Dashboard: licenses.capacity loader failed", error=str(exc))
        licenses = []

    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    total = sum(_safe_int(lic.get("count")) for lic in licenses)
    allocated = sum(_safe_int(lic.get("allocated")) for lic in licenses)
    available = max(total - allocated, 0)
    utilisation = round((allocated / total) * 100) if total else 0
    return {
        "items": [
            {"label": "Total seats", "value": total, "formatted": _format_int(total)},
            {"label": "Allocated", "value": allocated, "formatted": _format_int(allocated)},
            {"label": "Available", "value": available, "formatted": _format_int(available)},
            {"label": "Utilisation", "value": utilisation, "formatted": f"{utilisation}%"},
        ],
        "total": total,
        "allocated": allocated,
        "available": available,
        "utilisation": utilisation,
        "formatted": {
            "total": _format_int(total),
            "allocated": _format_int(allocated),
            "available": _format_int(available),
            "utilisation": f"{utilisation}%",
        },
    }


async def _load_invoice_health(ctx: CardContext) -> Mapping[str, Any]:
    if ctx.active_company_id is None:
        return {
            "open_amount": Decimal("0"),
            "open_formatted": _format_currency(Decimal("0")),
            "overdue": 0,
            "overdue_formatted": "0",
            "items": [],
        }
    try:
        invoices = await invoice_repo.list_company_invoices(int(ctx.active_company_id))
    except Exception as exc:
        log_error("Dashboard: invoices.health loader failed", error=str(exc))
        invoices = []
    today = datetime.now(timezone.utc).date()
    open_amount = Decimal("0")
    overdue_count = 0
    status_counter: Counter[str] = Counter()
    for invoice in invoices:
        status = str(invoice.get("status") or "Unspecified").strip() or "Unspecified"
        status_counter[status] += 1
        amount = invoice.get("amount")
        normalised_status = status.lower()
        if isinstance(amount, Decimal) and normalised_status not in {"paid", "void", "cancelled"}:
            open_amount += amount
        due_date = invoice.get("due_date")
        if (
            due_date
            and normalised_status not in {"paid", "void", "cancelled"}
            and due_date < today
        ):
            overdue_count += 1
    items = [
        {"label": label, "value": value, "formatted": _format_int(value)}
        for label, value in status_counter.most_common()
    ]
    return {
        "open_amount": open_amount,
        "open_formatted": _format_currency(open_amount),
        "overdue": overdue_count,
        "overdue_formatted": _format_int(overdue_count),
        "items": items,
    }


async def _load_staff_summary(ctx: CardContext) -> Mapping[str, Any]:
    if ctx.active_company_id is None:
        return {"total": 0, "active": 0, "formatted_total": "0", "formatted_active": "0"}
    try:
        total = await staff_repo.count_staff(int(ctx.active_company_id))
        active = await staff_repo.count_staff(int(ctx.active_company_id), enabled=True)
    except Exception as exc:
        log_error("Dashboard: staff.summary loader failed", error=str(exc))
        total = active = 0
    return {
        "total": total,
        "active": active,
        "formatted_total": _format_int(total),
        "formatted_active": _format_int(active),
    }


async def _load_recent_change_log(ctx: CardContext) -> Mapping[str, Any]:
    entries: list[Mapping[str, Any]] = []
    try:
        entries = await change_log_repo.list_change_log_entries(limit=5)
    except Exception as exc:
        log_error("Dashboard: change_log.recent loader failed", error=str(exc))
        entries = []
    items: list[dict[str, Any]] = []
    for entry in entries:
        occurred = entry.get("occurred_at_utc")
        when = ""
        if isinstance(occurred, datetime):
            when = occurred.strftime("%Y-%m-%d")
        items.append(
            {
                "title": str(entry.get("summary") or "(no summary)"),
                "subtitle": " · ".join(filter(None, [str(entry.get("change_type") or ""), when])),
            }
        )
    if not items:
        items.append({"title": "No recent changes recorded.", "subtitle": ""})
    return {"items": items, "link": "/admin/change-log"}


async def _load_agent_quick_ask(ctx: CardContext) -> Mapping[str, Any]:
    return {"available": ctx.module_enabled("ollama")}


async def _load_quick_actions(ctx: CardContext) -> Mapping[str, Any]:
    actions: list[dict[str, Any]] = [
        {"label": "Create ticket", "href": "/tickets/new"},
        {"label": "Notifications", "href": "/notifications"},
    ]
    if ctx.active_company_id is not None and (
        ctx.is_super_admin or ctx.has_membership_permission("can_manage_staff")
    ):
        actions.append({"label": "Staff", "href": "/staff"})
    if ctx.is_super_admin or ctx.has_membership_permission("can_manage_assets"):
        actions.append({"label": "Assets", "href": "/assets"})
    return {"items": actions}


async def _load_recent_notifications(ctx: CardContext) -> Mapping[str, Any]:
    items: list[dict[str, Any]] = []
    if ctx.user_id is not None:
        try:
            rows = await notifications_repo.list_notifications(
                user_id=ctx.user_id, limit=5
            )
        except Exception as exc:
            log_error("Dashboard: notifications.recent loader failed", error=str(exc))
            rows = []
        for row in rows:
            created = row.get("created_at")
            when = ""
            if isinstance(created, datetime):
                when = created.strftime("%Y-%m-%d %H:%M")
            message = row.get("message")
            if isinstance(message, str):
                message = message.strip()
            event_type = row.get("event_type")
            title_parts = [str(part) for part in (event_type, message) if part]
            title = title_parts[0] if title_parts else "Notification"
            subtitle = " · ".join(filter(None, [str(message) if message and message != title else "", when]))
            items.append(
                {
                    "title": title,
                    "subtitle": subtitle,
                    "read": bool(row.get("read_at")),
                }
            )
    if not items:
        items.append({"title": "No recent notifications.", "subtitle": "", "read": True})
    return {"items": items, "link": "/notifications"}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_CARD_REGISTRY: tuple[CardDescriptor, ...] = (
    CardDescriptor(
        id="overview.companies",
        title="Companies",
        description="Total organisations available to you.",
        category="Overview",
        template_partial="partials/dashboard_cards/counter.html",
        permission_check=_allow_any_user,
        data_loader=_load_companies_count,
        default_size="small",
    ),
    CardDescriptor(
        id="overview.portal_users",
        title="Portal users",
        description="Total registered portal accounts.",
        category="System",
        template_partial="partials/dashboard_cards/counter.html",
        permission_check=_require_super_admin,
        data_loader=_load_users_count,
        default_size="small",
    ),
    CardDescriptor(
        id="overview.unread_notifications",
        title="Unread alerts",
        description="Notifications awaiting your review.",
        category="Overview",
        template_partial="partials/dashboard_cards/counter.html",
        permission_check=_allow_any_user,
        data_loader=_load_unread_notifications,
        default_size="small",
        refresh_interval_seconds=120,
    ),
    CardDescriptor(
        id="overview.webhook_queue",
        title="Webhook queue",
        description="Pending, in-progress and failing webhook deliveries.",
        category="System",
        template_partial="partials/dashboard_cards/status_list.html",
        permission_check=_require_super_admin,
        data_loader=_load_webhook_queue,
        default_size="medium",
        refresh_interval_seconds=120,
    ),
    CardDescriptor(
        id="tickets.my_open",
        title="My open tickets",
        description="Tickets you raised or are watching that are still open.",
        category="Tickets",
        template_partial="partials/dashboard_cards/counter.html",
        permission_check=_allow_any_user,
        data_loader=_load_my_open_tickets,
        default_size="small",
    ),
    CardDescriptor(
        id="tickets.unassigned",
        title="Unassigned tickets",
        description="Open tickets without an assigned owner.",
        category="Tickets",
        template_partial="partials/dashboard_cards/counter.html",
        permission_check=_require_super_admin,
        data_loader=_load_unassigned_tickets,
        default_size="small",
    ),
    CardDescriptor(
        id="assets.status_mix",
        title="Asset distribution",
        description="Breakdown of assets by status for the active company.",
        category="Assets",
        template_partial="partials/dashboard_cards/status_list.html",
        permission_check=_require_membership_flag("can_manage_assets"),
        data_loader=_load_assets_status,
        default_size="medium",
    ),
    CardDescriptor(
        id="licenses.capacity",
        title="License capacity",
        description="Seat allocation and utilisation for the active company.",
        category="Licenses",
        template_partial="partials/dashboard_cards/status_list.html",
        permission_check=_require_membership_flag("can_manage_licenses"),
        data_loader=_load_license_capacity,
        default_size="medium",
    ),
    CardDescriptor(
        id="invoices.health",
        title="Invoice health",
        description="Open balance, overdue count and status mix.",
        category="Finance",
        template_partial="partials/dashboard_cards/invoice_health.html",
        permission_check=_require_membership_flag("can_manage_invoices"),
        data_loader=_load_invoice_health,
        default_size="medium",
    ),
    CardDescriptor(
        id="staff.summary",
        title="Staff summary",
        description="Total and active staff for the active company.",
        category="Staff",
        template_partial="partials/dashboard_cards/staff_summary.html",
        permission_check=_require_membership_flag("can_manage_staff"),
        data_loader=_load_staff_summary,
        default_size="small",
    ),
    CardDescriptor(
        id="changelog.recent",
        title="Recent changes",
        description="Latest entries from the portal change log.",
        category="System",
        template_partial="partials/dashboard_cards/entry_list.html",
        permission_check=_allow_any_user,
        data_loader=_load_recent_change_log,
        default_size="wide",
    ),
    CardDescriptor(
        id="agent.quick_ask",
        title="Agent quick ask",
        description="Ask the MyPortal agent a question.",
        category="Knowledge",
        template_partial="partials/dashboard_cards/agent.html",
        permission_check=lambda ctx: ctx.module_enabled("ollama") and ctx.user_id is not None,
        data_loader=_load_agent_quick_ask,
        default_size="large",
    ),
    CardDescriptor(
        id="quick_actions",
        title="Quick actions",
        description="Shortcuts to common tasks.",
        category="Personal",
        template_partial="partials/dashboard_cards/quick_actions.html",
        permission_check=_allow_any_user,
        data_loader=_load_quick_actions,
        default_size="small",
    ),
    CardDescriptor(
        id="notifications.recent",
        title="My recent notifications",
        description="The five most recent notifications delivered to you.",
        category="Personal",
        template_partial="partials/dashboard_cards/entry_list.html",
        permission_check=_allow_any_user,
        data_loader=_load_recent_notifications,
        default_size="medium",
        refresh_interval_seconds=300,
    ),
)

_REGISTRY_BY_ID: dict[str, CardDescriptor] = {card.id: card for card in _CARD_REGISTRY}


def list_cards() -> tuple[CardDescriptor, ...]:
    """Return every registered card descriptor."""
    return _CARD_REGISTRY


def get_card(card_id: str) -> CardDescriptor | None:
    return _REGISTRY_BY_ID.get(card_id)


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------

async def build_card_context(
    request: Request, user: Mapping[str, Any]
) -> CardContext:
    session = await session_manager.load_session(request)
    available_companies = getattr(request.state, "available_companies", None)
    if available_companies is None:
        available_companies = await company_access.list_accessible_companies(user)
        request.state.available_companies = available_companies

    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None and session:
        active_company_id = session.active_company_id
        request.state.active_company_id = active_company_id
    try:
        active_company_id_int = int(active_company_id) if active_company_id is not None else None
    except (TypeError, ValueError):
        active_company_id_int = None

    membership = getattr(request.state, "active_membership", None)
    if membership is None and active_company_id_int is not None:
        try:
            membership = await user_company_repo.get_user_company(
                int(user["id"]), active_company_id_int
            )
        except Exception:  # pragma: no cover - defensive
            membership = None
        request.state.active_membership = membership

    module_lookup = getattr(request.state, "module_lookup", None)
    if module_lookup is None:
        try:
            module_list = await modules.list_modules()
        except Exception as exc:  # pragma: no cover - defensive
            log_error("Dashboard: failed to load modules", error=str(exc))
            module_list = []
        module_lookup = {
            module.get("slug"): module for module in module_list if module.get("slug")
        }
        request.state.module_lookup = module_lookup

    return CardContext(
        request=request,
        user=user,
        is_super_admin=bool(user.get("is_super_admin")),
        membership=membership,
        active_company_id=active_company_id_int,
        available_companies=available_companies or [],
        module_lookup=module_lookup or {},
    )


# ---------------------------------------------------------------------------
# Layout sanitisation
# ---------------------------------------------------------------------------

def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if result < minimum:
        return minimum
    if result > maximum:
        return maximum
    return result


def sanitise_layout(
    payload: Any, allowed_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """Validate and normalise a layout document.

    * Drops entries with unknown card ids.
    * If ``allowed_ids`` is provided, also drops cards the caller may not see.
    * Coerces and clamps ``x``, ``y``, ``w`` and ``h`` to safe ranges.
    * Caps the total number of cards at :data:`MAX_LAYOUT_CARDS`.
    * Drops duplicate ids (first occurrence wins).
    """

    if not isinstance(payload, list):
        return []
    sanitised: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in payload:
        if len(sanitised) >= MAX_LAYOUT_CARDS:
            break
        if not isinstance(entry, Mapping):
            continue
        raw_id = entry.get("id")
        if not isinstance(raw_id, str):
            continue
        card_id = raw_id.strip()
        if not card_id or card_id in seen:
            continue
        if card_id not in _REGISTRY_BY_ID:
            continue
        if allowed_ids is not None and card_id not in allowed_ids:
            continue
        descriptor = _REGISTRY_BY_ID[card_id]
        default_w, default_h = _size_to_cells(descriptor.default_size)
        w = _coerce_int(entry.get("w"), default=default_w, minimum=MIN_CARD_WIDTH, maximum=MAX_CARD_WIDTH)
        h = _coerce_int(entry.get("h"), default=default_h, minimum=MIN_CARD_HEIGHT, maximum=MAX_CARD_HEIGHT)
        x = _coerce_int(entry.get("x"), default=0, minimum=0, maximum=GRID_COLUMNS - MIN_CARD_WIDTH)
        if x + w > GRID_COLUMNS:
            x = max(0, GRID_COLUMNS - w)
        y = _coerce_int(entry.get("y"), default=0, minimum=0, maximum=MAX_GRID_ROWS)
        sanitised.append({"id": card_id, "x": x, "y": y, "w": w, "h": h})
        seen.add(card_id)
    return sanitised


def default_layout(allowed_ids) -> list[dict[str, Any]]:
    """Return a sensible default layout for the supplied allowed cards.

    Cards are placed left-to-right in a 12-column grid, in registration order
    among the allowed set, with each card given its preferred size.
    """
    layout: list[dict[str, Any]] = []
    cursor_x = 0
    cursor_y = 0
    row_height = 0
    allowed_set = set(allowed_ids)
    for descriptor in _CARD_REGISTRY:
        if descriptor.id not in allowed_set:
            continue
        if len(layout) >= MAX_LAYOUT_CARDS:
            break
        w, h = _size_to_cells(descriptor.default_size)
        if cursor_x + w > GRID_COLUMNS:
            cursor_x = 0
            cursor_y += max(row_height, 1)
            row_height = 0
        layout.append({"id": descriptor.id, "x": cursor_x, "y": cursor_y, "w": w, "h": h})
        cursor_x += w
        row_height = max(row_height, h)
    return layout


# ---------------------------------------------------------------------------
# Layout persistence
# ---------------------------------------------------------------------------

async def load_layout(user_id: int) -> list[dict[str, Any]] | None:
    if user_id <= 0:
        return None
    try:
        raw = await user_preferences_repo.get_preference(user_id, LAYOUT_PREFERENCE_KEY)
    except Exception as exc:  # pragma: no cover - defensive
        log_error("Dashboard: failed to load layout preference", error=str(exc))
        return None
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        raw = raw.get("cards")
    return sanitise_layout(raw)


async def save_layout(user_id: int, payload: Any, *, allowed_ids: set[str]) -> list[dict[str, Any]]:
    if user_id <= 0:
        raise ValueError("Invalid user id")
    sanitised = sanitise_layout(payload, allowed_ids=allowed_ids)
    await user_preferences_repo.set_preference(user_id, LAYOUT_PREFERENCE_KEY, sanitised)
    return sanitised


async def reset_layout(user_id: int) -> None:
    if user_id <= 0:
        return
    try:
        await user_preferences_repo.delete_preference(user_id, LAYOUT_PREFERENCE_KEY)
    except Exception as exc:  # pragma: no cover - defensive
        log_error("Dashboard: failed to reset layout preference", error=str(exc))


# ---------------------------------------------------------------------------
# Permission filtering and dashboard build
# ---------------------------------------------------------------------------

async def list_allowed_cards(ctx: CardContext) -> list[CardDescriptor]:
    """Return the subset of registered cards the current user can view."""
    results: list[CardDescriptor] = []
    for descriptor in _CARD_REGISTRY:
        if await _safe_call(descriptor.permission_check, ctx):
            results.append(descriptor)
    return results


async def build_card_payload(
    descriptor: CardDescriptor, ctx: CardContext
) -> Mapping[str, Any]:
    try:
        payload = await descriptor.data_loader(ctx)
    except Exception as exc:
        log_error(
            "Dashboard: data loader raised",
            card_id=descriptor.id,
            error=str(exc),
        )
        return {"_error": "Failed to load this card."}
    if payload is None:
        return {}
    return dict(payload)


async def build_user_dashboard(
    request: Request, user: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the data the dashboard template needs for this user.

    Steps:
      1. Resolve per-request context.
      2. Determine which cards the user is allowed to see.
      3. Load the user's layout (or generate a default one) and filter it.
      4. Concurrently load each card's payload.
      5. Return a structure consumed by ``dashboard.html``.
    """
    ctx = await build_card_context(request, user)
    allowed = await list_allowed_cards(ctx)
    allowed_ids = {descriptor.id for descriptor in allowed}

    saved_layout = None
    if ctx.user_id is not None:
        saved_layout = await load_layout(ctx.user_id)

    if saved_layout:
        layout = [entry for entry in saved_layout if entry["id"] in allowed_ids]
    else:
        layout = default_layout(allowed_ids)

    descriptor_map = {d.id: d for d in allowed}

    async def _gather_one(entry: dict[str, Any]) -> dict[str, Any]:
        descriptor = descriptor_map[entry["id"]]
        payload = await build_card_payload(descriptor, ctx)
        return {
            "descriptor": _serialise_descriptor(descriptor),
            "position": {k: entry[k] for k in ("x", "y", "w", "h")},
            "payload": payload,
        }

    if layout:
        cards = await asyncio.gather(*[_gather_one(entry) for entry in layout])
    else:
        cards = []

    unread_count = 0
    if ctx.user_id is not None:
        try:
            unread_count = await notifications_repo.count_notifications(
                user_id=ctx.user_id, read_state="unread"
            )
        except Exception:  # pragma: no cover - defensive
            unread_count = 0

    catalogue = [_serialise_descriptor(descriptor) for descriptor in allowed]

    return {
        "cards": cards,
        "catalogue": catalogue,
        "layout": layout,
        "grid_columns": GRID_COLUMNS,
        "unread_notifications": unread_count,
        "ollama_enabled": ctx.module_enabled("ollama"),
        # Backwards compatibility with templates and tests that still expect a
        # "company" key. The new dashboard surfaces this information through
        # individual cards (assets/licenses/invoices), so an empty placeholder
        # is sufficient.
        "company": None,
    }


def _serialise_descriptor(descriptor: CardDescriptor) -> dict[str, Any]:
    width, height = _size_to_cells(descriptor.default_size)
    return {
        "id": descriptor.id,
        "title": descriptor.title,
        "description": descriptor.description,
        "category": descriptor.category,
        "default_size": descriptor.default_size,
        "default_width": width,
        "default_height": height,
        "template_partial": descriptor.template_partial,
        "refresh_interval_seconds": descriptor.refresh_interval_seconds,
    }
