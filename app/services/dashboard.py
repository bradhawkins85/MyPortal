"""Server-rendered, opinionated dashboard for the portal home page.

This module replaces the previous customisable card grid (``dashboard_cards``)
with a small fixed set of role-aware sections. Every section is computed
server-side, gated by an explicit permission check, and reuses the existing
repositories — there is no new data plumbing.

The page renders only the sections that came back from
:func:`build_dashboard`; sections the user is not allowed to see are simply
omitted from the returned mapping.

Adding a new section is two small pieces of code:

1. Append an ``if``-guarded coroutine call inside :func:`build_dashboard` that
   populates an entry on the returned ``sections`` mapping.
2. Render that entry in ``app/templates/dashboard.html``.

That's it — no registry, no JavaScript, no API endpoint.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from fastapi import Request

from app.core.logging import log_error
from app.repositories import change_log as change_log_repo
from app.repositories import invoices as invoice_repo
from app.repositories import licenses as license_repo
from app.repositories import notifications as notifications_repo
from app.repositories import tickets as tickets_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import webhook_events as webhook_events_repo
from app.security.session import session_manager
from app.services import company_access

# Open ticket statuses considered "needs attention" across the portal.
_OPEN_TICKET_STATUSES: tuple[str, ...] = ("new", "open", "pending", "in_progress")

# How many recent activity entries to surface.
_RECENT_ACTIVITY_LIMIT = 5

# Invoice statuses that count as "still open" for the attention list.
_OPEN_INVOICE_STATUSES = {"draft", "sent", "overdue", "partial", "outstanding"}
_CLOSED_INVOICE_STATUSES = {"paid", "void", "cancelled"}


# ---------------------------------------------------------------------------
# Context resolution
# ---------------------------------------------------------------------------

class _DashboardContext:
    """Per-request data shared across section builders."""

    __slots__ = (
        "request",
        "user",
        "user_id",
        "is_super_admin",
        "active_company_id",
        "active_company",
        "available_companies",
        "membership",
    )

    def __init__(
        self,
        *,
        request: Request,
        user: Mapping[str, Any],
        user_id: int | None,
        is_super_admin: bool,
        active_company_id: int | None,
        active_company: Mapping[str, Any] | None,
        available_companies: list[Mapping[str, Any]],
        membership: Mapping[str, Any] | None,
    ) -> None:
        self.request = request
        self.user = user
        self.user_id = user_id
        self.is_super_admin = is_super_admin
        self.active_company_id = active_company_id
        self.active_company = active_company
        self.available_companies = available_companies
        self.membership = membership

    def has_permission(self, flag: str) -> bool:
        if self.is_super_admin:
            return True
        return bool((self.membership or {}).get(flag))


async def _build_context(request: Request, user: Mapping[str, Any]) -> _DashboardContext:
    try:
        user_id = int(user.get("id")) if user.get("id") is not None else None
    except (TypeError, ValueError):
        user_id = None

    available_companies = getattr(request.state, "available_companies", None)
    if available_companies is None:
        try:
            available_companies = await company_access.list_accessible_companies(user)
        except Exception as exc:  # pragma: no cover - defensive
            log_error("Dashboard: failed to list accessible companies", error=str(exc))
            available_companies = []
        request.state.available_companies = available_companies

    active_company_id = getattr(request.state, "active_company_id", None)
    if active_company_id is None:
        try:
            session = await session_manager.load_session(request)
        except Exception:  # pragma: no cover - defensive
            session = None
        if session is not None:
            active_company_id = session.active_company_id
            request.state.active_company_id = active_company_id
    try:
        active_company_id_int = int(active_company_id) if active_company_id is not None else None
    except (TypeError, ValueError):
        active_company_id_int = None

    active_company = None
    if active_company_id_int is not None:
        for company in available_companies or []:
            if company.get("company_id") == active_company_id_int:
                active_company = company
                break

    membership = getattr(request.state, "active_membership", None)
    if membership is None and active_company_id_int is not None and user_id is not None:
        try:
            membership = await user_company_repo.get_user_company(user_id, active_company_id_int)
        except Exception:  # pragma: no cover - defensive
            membership = None
        request.state.active_membership = membership

    return _DashboardContext(
        request=request,
        user=user,
        user_id=user_id,
        is_super_admin=bool(user.get("is_super_admin")),
        active_company_id=active_company_id_int,
        active_company=active_company,
        available_companies=list(available_companies or []),
        membership=membership,
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _greeting_for(now: datetime) -> str:
    hour = now.hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _user_display_name(user: Mapping[str, Any]) -> str:
    for key in ("display_name", "full_name", "name"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    email = user.get("email")
    if isinstance(email, str) and email:
        local, _, _domain = email.partition("@")
        return local or email
    return "there"


def _greeting_section(ctx: _DashboardContext) -> dict[str, Any]:
    role = "Super admin" if ctx.is_super_admin else "Member"
    company_name = None
    if ctx.active_company:
        name = ctx.active_company.get("company_name")
        if isinstance(name, str) and name.strip():
            company_name = name.strip()
    return {
        "greeting": _greeting_for(datetime.now()),
        "name": _user_display_name(ctx.user),
        "role": role,
        "company_name": company_name,
        "can_switch_company": len(ctx.available_companies) > 1,
        "switch_url": "/companies",
    }


async def _attention_section(ctx: _DashboardContext) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    # 1. My open tickets
    if ctx.user_id is not None:
        try:
            my_open = await tickets_repo.count_tickets_for_user(
                ctx.user_id, status=list(_OPEN_TICKET_STATUSES)
            )
        except Exception as exc:
            log_error("Dashboard: my open tickets lookup failed", error=str(exc))
            my_open = 0
        if my_open:
            items.append(
                {
                    "key": "tickets.my_open",
                    "label": "Open tickets you raised or watch",
                    "count": my_open,
                    "severity": "info",
                    "href": "/tickets",
                }
            )

    # 2. Unassigned tickets in the queue (super admin only)
    if ctx.is_super_admin:
        try:
            unassigned = await tickets_repo.count_tickets(
                status="open", assigned_user_id=None
            )
        except Exception as exc:
            log_error("Dashboard: unassigned tickets lookup failed", error=str(exc))
            unassigned = 0
        if unassigned:
            items.append(
                {
                    "key": "tickets.unassigned",
                    "label": "Unassigned tickets",
                    "count": unassigned,
                    "severity": "warning",
                    "href": "/admin/tickets?assigned=unassigned",
                }
            )

    # 3. Overdue / outstanding invoices for the active company
    if ctx.active_company_id is not None and ctx.has_permission("can_manage_invoices"):
        try:
            invoices = await invoice_repo.list_company_invoices(int(ctx.active_company_id))
        except Exception as exc:
            log_error("Dashboard: invoice lookup failed", error=str(exc))
            invoices = []
        today = datetime.now(timezone.utc).date()
        overdue = 0
        outstanding = 0
        outstanding_amount = Decimal("0")
        for invoice in invoices or []:
            status = str(invoice.get("status") or "").strip().lower()
            if status in _CLOSED_INVOICE_STATUSES:
                continue
            outstanding += 1
            amount = invoice.get("amount")
            if isinstance(amount, Decimal):
                outstanding_amount += amount
            due_date = invoice.get("due_date")
            if due_date and due_date < today:
                overdue += 1
        if overdue:
            items.append(
                {
                    "key": "invoices.overdue",
                    "label": "Overdue invoices",
                    "count": overdue,
                    "severity": "danger",
                    "href": "/invoices",
                }
            )
        elif outstanding:
            items.append(
                {
                    "key": "invoices.outstanding",
                    "label": "Outstanding invoices",
                    "count": outstanding,
                    "severity": "info",
                    "href": "/invoices",
                    "detail": _format_currency(outstanding_amount),
                }
            )

    # 4. Failed webhooks (super admin only)
    if ctx.is_super_admin:
        try:
            failed = await webhook_events_repo.count_events_by_status("failed")
        except Exception as exc:
            log_error("Dashboard: webhook failure lookup failed", error=str(exc))
            failed = 0
        if failed:
            items.append(
                {
                    "key": "webhooks.failed",
                    "label": "Failed webhooks",
                    "count": failed,
                    "severity": "danger",
                    "href": "/admin/webhooks",
                }
            )

    # 5. Licenses with no available seats
    if ctx.active_company_id is not None and ctx.has_permission("can_manage_licenses"):
        try:
            licenses = await license_repo.list_company_licenses(int(ctx.active_company_id))
        except Exception as exc:
            log_error("Dashboard: license lookup failed", error=str(exc))
            licenses = []
        exhausted = 0
        for licence in licenses or []:
            try:
                total = int(licence.get("count") or 0)
                allocated = int(licence.get("allocated") or 0)
            except (TypeError, ValueError):
                continue
            if total > 0 and allocated >= total:
                exhausted += 1
        if exhausted:
            items.append(
                {
                    "key": "licenses.exhausted",
                    "label": "Licenses with no available seats",
                    "count": exhausted,
                    "severity": "warning",
                    "href": "/licenses",
                }
            )

    return {"items": items, "all_clear": not items}


def _quick_actions_section(ctx: _DashboardContext) -> dict[str, Any]:
    actions: list[dict[str, Any]] = [
        {"label": "New ticket", "href": "/tickets/new", "variant": "primary"},
        {"label": "Open tickets", "href": "/tickets"},
        {"label": "Notifications", "href": "/notifications"},
    ]
    if ctx.has_permission("can_manage_assets"):
        actions.append({"label": "Assets", "href": "/assets"})
    if ctx.has_permission("can_manage_staff"):
        actions.append({"label": "Staff", "href": "/staff"})
    if len(ctx.available_companies) > 1:
        actions.append({"label": "Switch company", "href": "/companies"})
    return {"actions": actions}


async def _recent_activity_section(ctx: _DashboardContext) -> dict[str, Any]:
    notifications: list[dict[str, Any]] = []
    if ctx.user_id is not None:
        try:
            rows = await notifications_repo.list_notifications(
                user_id=ctx.user_id, limit=_RECENT_ACTIVITY_LIMIT
            )
        except Exception as exc:
            log_error("Dashboard: notifications lookup failed", error=str(exc))
            rows = []
        for row in rows or []:
            notifications.append(
                {
                    "title": _notification_title(row),
                    "subtitle": _notification_subtitle(row),
                    "occurred_at": row.get("created_at"),
                    "read": bool(row.get("read_at")),
                }
            )

    changes: list[dict[str, Any]] = []
    try:
        change_rows = await change_log_repo.list_change_log_entries(limit=_RECENT_ACTIVITY_LIMIT)
    except Exception as exc:
        log_error("Dashboard: change log lookup failed", error=str(exc))
        change_rows = []
    for row in change_rows or []:
        summary = row.get("summary") or "(no summary)"
        change_type = row.get("change_type") or ""
        changes.append(
            {
                "title": str(summary),
                "subtitle": str(change_type) if change_type else "",
                "occurred_at": row.get("occurred_at_utc"),
            }
        )

    return {
        "notifications": notifications,
        "changes": changes,
        "notifications_link": "/notifications",
        "changes_link": "/admin/change-log" if ctx.is_super_admin else None,
    }


async def _system_health_section(ctx: _DashboardContext) -> dict[str, Any] | None:
    if not ctx.is_super_admin:
        return None
    counts: Counter[str] = Counter()
    for state in ("pending", "in_progress", "failed"):
        try:
            counts[state] = await webhook_events_repo.count_events_by_status(state)
        except Exception as exc:
            log_error("Dashboard: webhook count failed", error=str(exc), state=state)
            counts[state] = 0
    return {
        "webhook_pending": counts.get("pending", 0),
        "webhook_in_progress": counts.get("in_progress", 0),
        "webhook_failed": counts.get("failed", 0),
        "webhooks_url": "/admin/webhooks",
        "service_status_url": "/service-status",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notification_title(row: Mapping[str, Any]) -> str:
    event_type = row.get("event_type")
    if isinstance(event_type, str) and event_type.strip():
        return event_type.strip()
    message = row.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return "Notification"


def _notification_subtitle(row: Mapping[str, Any]) -> str:
    message = row.get("message")
    if isinstance(message, str):
        message = message.strip()
    title = _notification_title(row)
    if message and message != title:
        return message
    return ""


def _format_currency(amount: Decimal) -> str:
    try:
        quantized = amount.quantize(Decimal("0.01"))
    except Exception:  # pragma: no cover - defensive
        return f"${amount}"
    return f"${quantized:,}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def build_dashboard(request: Request, user: Mapping[str, Any]) -> dict[str, Any]:
    """Build the data payload for the home page dashboard.

    The returned mapping is consumed directly by ``app/templates/dashboard.html``
    and contains:

    * ``greeting`` — always present (greeting line + active company banner).
    * ``attention`` — always present; ``items`` may be empty (renders the
      "all clear" state).
    * ``quick_actions`` — always present.
    * ``recent_activity`` — always present.
    * ``system_health`` — present only for super admins.
    * ``unread_notifications`` — convenience count surfaced for the layout
      (kept for backwards compatibility with callers that previously consumed
      ``_build_consolidated_overview``).
    """
    ctx = await _build_context(request, user)

    attention = await _attention_section(ctx)
    recent_activity = await _recent_activity_section(ctx)
    system_health = await _system_health_section(ctx)
    quick_actions = _quick_actions_section(ctx)
    greeting = _greeting_section(ctx)

    unread = 0
    if ctx.user_id is not None:
        try:
            unread = await notifications_repo.count_notifications(
                user_id=ctx.user_id, read_state="unread"
            )
        except Exception as exc:  # pragma: no cover - defensive
            log_error("Dashboard: unread notification count failed", error=str(exc))
            unread = 0

    payload: dict[str, Any] = {
        "greeting": greeting,
        "attention": attention,
        "quick_actions": quick_actions,
        "recent_activity": recent_activity,
        "unread_notifications": unread,
    }
    if system_health is not None:
        payload["system_health"] = system_health
    return payload
