"""Company overview report builder.

This service aggregates data from several repositories into a single
``ReportData`` structure that can be rendered on the web viewer page or
exported to PDF.  Every section is individually togglable per company;
disabled sections are still included in the returned structure but their
``enabled`` flag is ``False`` so templates can skip them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.core.database import db
from app.repositories import asset_custom_fields as asset_custom_fields_repo
from app.repositories import assets as assets_repo
from app.repositories import backup_jobs as backup_jobs_repo
from app.repositories import companies as company_repo
from app.repositories import compliance_checks as compliance_checks_repo
from app.repositories import essential8 as essential8_repo
from app.repositories import issues as issues_repo
from app.repositories import licenses as licenses_repo
from app.repositories import m365_best_practices as m365_bp_repo
from app.repositories import report_sections as report_sections_repo
from app.repositories import shop as shop_repo
from app.repositories import staff as staff_repo
from app.repositories import subscriptions as subscriptions_repo
from app.services.m365 import PACKAGE_MAILBOX_RE


# ---------------------------------------------------------------------------
# Section definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportSection:
    key: str
    label: str
    description: str = ""


#: Canonical list of available report sections.  Templates and settings pages
#: iterate this list in order.  To add a new section, append a ``ReportSection``
#: entry and implement a matching builder in :func:`build_company_report`.
REPORT_SECTIONS: tuple[ReportSection, ...] = (
    ReportSection(
        key="assets",
        label="Assets synced (last 30 days)",
        description="Count of assets that have synced in the last 30 days, split by Servers and Workstations.",
    ),
    ReportSection(
        key="staff",
        label="Active staff",
        description="Count of staff accounts currently enabled for the company.",
    ),
    ReportSection(
        key="m365_best_practices",
        label="M365 best practice summary",
        description="Pass/fail summary from the latest M365 best practice scan.",
    ),
    ReportSection(
        key="top_mailboxes",
        label="Top 5 mailboxes by size",
        description="Top five user and shared mailboxes ranked by total size.",
    ),
    ReportSection(
        key="orders_current_month",
        label="Orders this month",
        description="Orders placed in the current calendar month and their status.",
    ),
    ReportSection(
        key="licenses",
        label="Licenses",
        description="Allocated and total license seats with expiry and contract term.",
    ),
    ReportSection(
        key="subscriptions",
        label="Subscriptions",
        description="Active subscriptions linked to the company.",
    ),
    ReportSection(
        key="essential8",
        label="Essential 8 compliance progress",
        description="Per-maturity-level progress for each Essential 8 control.",
    ),
    ReportSection(
        key="compliance_checks",
        label="Customer compliance checks",
        description="Compliance rate, in-progress, not started, overdue, and due-soon counts.",
    ),
    ReportSection(
        key="tickets_last_month",
        label="Tickets (past month)",
        description="Ticket counts grouped by status for the past 30 days.",
    ),
    ReportSection(
        key="asset_custom_fields",
        label="Asset custom field values",
        description="Counts of each distinct value across the company's assets.",
    ),
    ReportSection(
        key="issues",
        label="Issue tracker issues",
        description="Issues currently assigned to the company.",
    ),
    ReportSection(
        key="backup_jobs",
        label="Backup history",
        description=(
            "Per-day backup status for every job configured for the company. "
            "The detailed report renders a colour-coded grid for the last 30 days."
        ),
    ),
)


SECTION_KEYS: frozenset[str] = frozenset(s.key for s in REPORT_SECTIONS)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class SectionResult:
    key: str
    label: str
    enabled: bool
    data: dict[str, Any] = field(default_factory=dict)
    is_empty: bool = False
    detailed: bool = False
    detail_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReportData:
    company: dict[str, Any]
    generated_at: datetime
    sections: list[SectionResult] = field(default_factory=list)
    auto_hide_empty: bool = True

    def section(self, key: str) -> SectionResult | None:
        for section in self.sections:
            if section.key == key:
                return section
        return None


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


async def _build_assets(company_id: int) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=30)
    total = await assets_repo.count_active_assets(company_id=company_id, since=since)
    servers = await assets_repo.count_active_assets_by_type(
        company_id=company_id, since=since, device_type="server"
    )
    workstations = await assets_repo.count_active_assets_by_type(
        company_id=company_id, since=since, device_type="workstation"
    )
    return {
        "total_synced": int(total),
        "servers": int(servers),
        "workstations": int(workstations),
        "since": since.isoformat(),
    }


async def _build_staff(company_id: int) -> dict[str, Any]:
    total = await staff_repo.count_staff(company_id, enabled=True, exclude_package_staff=True)
    return {"total_active": int(total)}


async def _build_m365_best_practices(company_id: int) -> dict[str, Any]:
    results = await m365_bp_repo.list_results(company_id)
    counts: dict[str, int] = {
        "pass": 0,
        "fail": 0,
        "warn": 0,
        "error": 0,
        "not_applicable": 0,
        "unknown": 0,
        "other": 0,
    }
    for row in results:
        status = str(row.get("status") or "").lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    total = len(results)
    # Exclude N/A and unknown from the pass-rate denominator
    rated_total = total - counts["not_applicable"] - counts["unknown"] - counts["other"]
    passed = counts["pass"]
    pass_percentage = round((passed / rated_total * 100.0), 1) if rated_total else 0.0
    return {
        "total": total,
        "counts": counts,
        "pass_percentage": pass_percentage,
        "last_run_at": _max_datetime(row.get("run_at") for row in results),
    }


async def _build_top_mailboxes(company_id: int) -> dict[str, Any]:
    """Top five user mailboxes and top five shared mailboxes by size."""

    def _to_mailbox_row(row: Any) -> dict[str, Any]:
        primary = int(row.get("storage_used_bytes") or 0)
        raw_archive = row.get("archive_storage_used_bytes")
        archive: int | None = int(raw_archive) if raw_archive is not None else None
        total = primary + (archive or 0)
        return {
            "user_principal_name": row.get("user_principal_name"),
            "display_name": row.get("display_name") or row.get("user_principal_name"),
            "mailbox_type": row.get("mailbox_type") or "UserMailbox",
            "total_bytes": total,
            "primary_bytes": primary,
            "archive_bytes": archive,
        }

    _query = """
        SELECT user_principal_name, display_name, mailbox_type,
               storage_used_bytes, archive_storage_used_bytes
        FROM m365_mailboxes
        WHERE company_id = %s AND mailbox_type = %s
        ORDER BY (COALESCE(storage_used_bytes, 0) + COALESCE(archive_storage_used_bytes, 0)) DESC
    """
    try:
        user_rows = await db.fetch_all(_query, (company_id, "UserMailbox"))
    except Exception:  # pragma: no cover - defensive when table missing
        user_rows = []
    try:
        shared_rows = await db.fetch_all(_query, (company_id, "SharedMailbox"))
    except Exception:  # pragma: no cover - defensive when table missing
        shared_rows = []

    def _is_package(row: Any) -> bool:
        return bool(PACKAGE_MAILBOX_RE.match(row.get("display_name") or ""))

    user_filtered = [r for r in (user_rows or []) if not _is_package(r)][:5]
    shared_filtered = [r for r in (shared_rows or []) if not _is_package(r)][:5]

    return {
        "user_mailboxes": [_to_mailbox_row(r) for r in user_filtered],
        "shared_mailboxes": [_to_mailbox_row(r) for r in shared_filtered],
    }


async def _build_orders_current_month(company_id: int) -> dict[str, Any]:
    orders = await shop_repo.list_order_summaries(company_id)
    today = datetime.now(timezone.utc).date()
    start_of_month = today.replace(day=1)
    filtered: list[dict[str, Any]] = []
    for order in orders or []:
        order_date = _coerce_date(order.get("order_date"))
        if order_date is None or order_date < start_of_month or order_date > today:
            continue
        filtered.append(
            {
                "order_number": order.get("order_number"),
                "order_date": order_date.isoformat(),
                "status": order.get("status") or "unknown",
                "shipping_status": order.get("shipping_status"),
                "po_number": order.get("po_number"),
            }
        )
    return {
        "orders": filtered,
        "total": len(filtered),
        "month_start": start_of_month.isoformat(),
    }


async def _build_licenses(company_id: int) -> dict[str, Any]:
    records = await licenses_repo.list_company_licenses(company_id)
    licenses: list[dict[str, Any]] = []
    for record in records:
        expiry = _coerce_date(record.get("expiry_date"))
        licenses.append(
            {
                "name": record.get("display_name") or record.get("name"),
                "total": int(record.get("count") or 0),
                "allocated": int(record.get("allocated") or 0),
                "expiry_date": expiry.isoformat() if expiry else None,
                "contract_term": record.get("contract_term"),
                "auto_renew": record.get("auto_renew"),
            }
        )
    return {"licenses": licenses, "total": len(licenses)}


async def _build_subscriptions(company_id: int) -> dict[str, Any]:
    records = await subscriptions_repo.list_subscriptions(customer_id=company_id)
    subscriptions: list[dict[str, Any]] = []
    for record in records:
        subscriptions.append(
            {
                "subscription_id": record.get("id") or record.get("subscription_id"),
                "product_name": record.get("product_name"),
                "category_name": record.get("category_name"),
                "quantity": record.get("quantity"),
                "status": record.get("status") or "unknown",
                "start_date": _date_to_iso(record.get("start_date")),
                "end_date": _date_to_iso(record.get("end_date")),
                "commitment_term": record.get("commitment_term"),
            }
        )
    return {"subscriptions": subscriptions, "total": len(subscriptions)}


# Maturity level ordering used throughout the Essential 8 report section.
_ESSENTIAL8_LEVELS: tuple[tuple[str, str], ...] = (
    ("ml1", "Maturity level 1"),
    ("ml2", "Maturity level 2"),
    ("ml3", "Maturity level 3"),
)


async def _build_essential8(company_id: int) -> dict[str, Any]:
    controls = await essential8_repo.list_essential8_controls()
    per_control = await essential8_repo.get_per_maturity_statuses_for_company(company_id)
    level_rows: list[dict[str, Any]] = []
    for level_key, level_label in _ESSENTIAL8_LEVELS:
        total = len(controls)
        compliant = 0
        in_progress = 0
        for control in controls:
            status = per_control.get(control["id"], {}).get(level_key, "not_started")
            if status == "compliant":
                compliant += 1
            elif status == "in_progress":
                in_progress += 1
        not_started = total - compliant - in_progress
        percentage = round((compliant / total * 100.0), 1) if total else 0.0
        # ML2 and ML3 are only included when at least one control has progress.
        has_progress = compliant > 0 or in_progress > 0
        if level_key == "ml1" or has_progress:
            level_rows.append(
                {
                    "level": level_key,
                    "label": level_label,
                    "total": total,
                    "compliant": compliant,
                    "in_progress": in_progress,
                    "not_started": not_started,
                    "percentage": percentage,
                }
            )
    return {"levels": level_rows}


async def _build_compliance_checks(company_id: int) -> dict[str, Any]:
    summary = await compliance_checks_repo.get_assignment_summary(company_id)
    return dict(summary)


# Ticket status bucket the report renders; any status outside this set is
# still counted under "other".
_TICKET_STATUSES: tuple[str, ...] = (
    "open",
    "pending",
    "in_progress",
    "resolved",
    "closed",
)


async def _build_tickets_last_month(company_id: int) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        rows = await db.fetch_all(
            """
            SELECT status, COUNT(*) AS total
            FROM tickets
            WHERE company_id = %s AND created_at >= %s
            GROUP BY status
            """,
            (company_id, since.replace(tzinfo=None)),
        )
    except Exception:  # pragma: no cover - defensive when schema differs
        rows = []
    counts: dict[str, int] = {status: 0 for status in _TICKET_STATUSES}
    counts["other"] = 0
    for row in rows or []:
        status = str(row.get("status") or "").lower()
        total = int(row.get("total") or 0)
        if status in counts:
            counts[status] += total
        else:
            counts["other"] += total
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        "since": since.isoformat(),
    }


async def _build_asset_custom_fields(company_id: int) -> dict[str, Any]:
    definitions = await asset_custom_fields_repo.list_field_definitions()
    fields_out: list[dict[str, Any]] = []
    for definition in definitions:
        field_type = str(definition.get("field_type") or "").lower()
        counts = await _count_custom_field_values(company_id, definition, field_type)
        fields_out.append(
            {
                "name": definition.get("name"),
                "display_name": definition.get("display_name") or definition.get("name"),
                "field_type": field_type,
                "values": counts,
                "total": sum(entry["count"] for entry in counts),
            }
        )
    return {"fields": fields_out}


async def _count_custom_field_values(
    company_id: int,
    definition: Mapping[str, Any],
    field_type: str,
) -> list[dict[str, Any]]:
    """Return ``[{value, count}, ...]`` for values stored against assets.

    We avoid surfacing free-text image URLs (and similarly unique columns) to
    keep the report readable — instead we collapse those to a single
    ``has value`` / ``no value`` split.
    """
    definition_id = definition.get("id")
    if definition_id is None:
        return []
    if field_type == "checkbox":
        rows = await db.fetch_all(
            """
            SELECT v.value_boolean AS value, COUNT(DISTINCT v.asset_id) AS total
            FROM asset_custom_field_values v
            JOIN assets a ON v.asset_id = a.id
            WHERE v.field_definition_id = %s AND a.company_id = %s
            GROUP BY v.value_boolean
            """,
            (definition_id, company_id),
        )
        return [
            {
                "value": "Yes" if (row.get("value") in (1, True, "1")) else "No",
                "count": int(row.get("total") or 0),
            }
            for row in rows or []
        ]
    if field_type in {"text", "url"}:
        rows = await db.fetch_all(
            """
            SELECT v.value_text AS value, COUNT(DISTINCT v.asset_id) AS total
            FROM asset_custom_field_values v
            JOIN assets a ON v.asset_id = a.id
            WHERE v.field_definition_id = %s
              AND a.company_id = %s
              AND v.value_text IS NOT NULL AND v.value_text <> ''
            GROUP BY v.value_text
            ORDER BY total DESC
            LIMIT 20
            """,
            (definition_id, company_id),
        )
        return [
            {"value": row.get("value") or "", "count": int(row.get("total") or 0)}
            for row in rows or []
        ]
    if field_type == "date":
        rows = await db.fetch_all(
            """
            SELECT v.value_date AS value, COUNT(DISTINCT v.asset_id) AS total
            FROM asset_custom_field_values v
            JOIN assets a ON v.asset_id = a.id
            WHERE v.field_definition_id = %s
              AND a.company_id = %s
              AND v.value_date IS NOT NULL
            GROUP BY v.value_date
            ORDER BY value
            """,
            (definition_id, company_id),
        )
        return [
            {
                "value": _date_to_iso(row.get("value")) or "",
                "count": int(row.get("total") or 0),
            }
            for row in rows or []
        ]
    # Fallback: just count set vs not for unsupported field types.
    rows = await db.fetch_all(
        """
        SELECT COUNT(DISTINCT v.asset_id) AS total
        FROM asset_custom_field_values v
        JOIN assets a ON v.asset_id = a.id
        WHERE v.field_definition_id = %s AND a.company_id = %s
        """,
        (definition_id, company_id),
    )
    total = int((rows[0].get("total") if rows else 0) or 0)
    return [{"value": "Has value", "count": total}] if total else []


async def _build_issues(company_id: int) -> dict[str, Any]:
    issues = await issues_repo.list_issues_with_assignments(company_id=company_id)
    rows: list[dict[str, Any]] = []
    for issue in issues:
        assignments = issue.get("assignments") or []
        # list_issues_with_assignments returns all companies when none match;
        # filter to just this company's assignments so we don't leak info.
        company_assignments = [
            a for a in assignments if a.get("company_id") == company_id
        ]
        if not company_assignments:
            continue
        for assignment in company_assignments:
            rows.append(
                {
                    "name": issue.get("name"),
                    "description": issue.get("description"),
                    "status": assignment.get("status") or "unknown",
                    "notes": assignment.get("notes"),
                    "updated_at": _datetime_to_iso(assignment.get("updated_at_utc")),
                }
            )
    rows.sort(key=lambda r: (r.get("status") or "", r.get("name") or ""))
    return {"issues": rows, "total": len(rows)}


async def _build_backup_jobs(company_id: int) -> dict[str, Any]:
    """Backup history summary: counts per status across the past 30 days."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=29)
    jobs = await backup_jobs_repo.list_jobs(company_id=company_id, include_inactive=True)
    job_ids = [int(job["id"]) for job in jobs]
    events = await backup_jobs_repo.list_events_in_range(
        job_ids=job_ids, start_date=start, end_date=end
    )
    counts: dict[str, int] = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
    for event in events:
        value = str(event.get("status") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    total_events = sum(counts.values())
    pass_pct = round((counts.get("pass", 0) / total_events) * 100, 1) if total_events else 0
    return {
        "total_jobs": len(jobs),
        "active_jobs": sum(1 for job in jobs if job.get("is_active")),
        "total_events": total_events,
        "counts": counts,
        "pass_percentage": pass_pct,
        "since": start.isoformat(),
        "until": end.isoformat(),
    }


# Maps section keys to their builder coroutine.
_SECTION_BUILDERS = {
    "assets": _build_assets,
    "staff": _build_staff,
    "m365_best_practices": _build_m365_best_practices,
    "top_mailboxes": _build_top_mailboxes,
    "orders_current_month": _build_orders_current_month,
    "licenses": _build_licenses,
    "subscriptions": _build_subscriptions,
    "essential8": _build_essential8,
    "compliance_checks": _build_compliance_checks,
    "tickets_last_month": _build_tickets_last_month,
    "asset_custom_fields": _build_asset_custom_fields,
    "issues": _build_issues,
    "backup_jobs": _build_backup_jobs,
}


# ---------------------------------------------------------------------------
# Detail builders  (per-section expanded data for the "Detailed" pages)
# ---------------------------------------------------------------------------


async def _build_assets_detail(company_id: int) -> dict[str, Any]:
    """Full list of company assets for the detail page."""
    rows = await assets_repo.list_company_assets(company_id)
    assets: list[dict[str, Any]] = []
    for row in rows:
        last_sync_raw = row.get("last_sync")
        last_sync: str | None = None
        if isinstance(last_sync_raw, datetime):
            last_sync = last_sync_raw.strftime("%Y-%m-%d")
        elif isinstance(last_sync_raw, str):
            last_sync = last_sync_raw[:10] if last_sync_raw else None
        assets.append(
            {
                "name": row.get("name"),
                "type": row.get("type"),
                "os_name": row.get("os_name"),
                "status": row.get("status"),
                "serial_number": row.get("serial_number"),
                "last_sync": last_sync,
                "last_user": row.get("last_user"),
                "form_factor": row.get("form_factor"),
                "warranty_status": row.get("warranty_status"),
                "warranty_end_date": _date_to_iso(row.get("warranty_end_date")),
            }
        )
    return {"assets": assets, "total": len(assets)}


async def _build_staff_detail(company_id: int) -> dict[str, Any]:
    """Full list of enabled staff for the detail page."""
    rows = await staff_repo.list_staff(
        company_id, enabled=True, exclude_package_staff=True, page_size=500
    )
    staff: list[dict[str, Any]] = []
    for row in rows:
        staff.append(
            {
                "name": row.get("name") or (
                    f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
                ),
                "email": row.get("email"),
                "mobile_phone": row.get("mobile_phone"),
                "department": row.get("department"),
                "position": row.get("position") or row.get("job_title"),
                "onboarding_status": row.get("onboarding_status"),
            }
        )
    return {"staff": staff, "total": len(staff)}


async def _build_m365_best_practices_detail(company_id: int) -> dict[str, Any]:
    """Per-check breakdown for the detail page."""
    results = await m365_bp_repo.list_results(company_id)
    checks: list[dict[str, Any]] = []
    for row in results:
        checks.append(
            {
                "check_id": row.get("check_id"),
                "check_name": row.get("check_name"),
                "status": str(row.get("status") or "").lower(),
                "details": row.get("details"),
                "remediation_status": row.get("remediation_status"),
                "run_at": _datetime_to_iso(row.get("run_at")),
            }
        )
    # Sort: fails first, then warns, then pass, then others.
    _order = {"fail": 0, "warn": 1, "error": 2, "pass": 3, "not_applicable": 4}
    checks.sort(key=lambda c: (_order.get(c.get("status") or "", 5), c.get("check_name") or ""))
    return {"checks": checks, "total": len(checks)}


async def _build_top_mailboxes_detail(company_id: int) -> dict[str, Any]:
    """All mailboxes (not just top 5) for the detail page."""

    def _to_mailbox_row(row: Any) -> dict[str, Any]:
        primary = int(row.get("storage_used_bytes") or 0)
        raw_archive = row.get("archive_storage_used_bytes")
        archive: int | None = int(raw_archive) if raw_archive is not None else None
        total = primary + (archive or 0)
        return {
            "user_principal_name": row.get("user_principal_name"),
            "display_name": row.get("display_name") or row.get("user_principal_name"),
            "mailbox_type": row.get("mailbox_type") or "UserMailbox",
            "total_bytes": total,
            "primary_bytes": primary,
            "archive_bytes": archive,
        }

    _query = """
        SELECT user_principal_name, display_name, mailbox_type,
               storage_used_bytes, archive_storage_used_bytes
        FROM m365_mailboxes
        WHERE company_id = %s AND mailbox_type = %s
        ORDER BY (COALESCE(storage_used_bytes, 0) + COALESCE(archive_storage_used_bytes, 0)) DESC
    """
    try:
        user_rows = await db.fetch_all(_query, (company_id, "UserMailbox"))
    except Exception:  # pragma: no cover
        user_rows = []
    try:
        shared_rows = await db.fetch_all(_query, (company_id, "SharedMailbox"))
    except Exception:  # pragma: no cover
        shared_rows = []

    user_filtered = [r for r in (user_rows or []) if not PACKAGE_MAILBOX_RE.match(r.get("display_name") or "")]
    shared_filtered = [r for r in (shared_rows or []) if not PACKAGE_MAILBOX_RE.match(r.get("display_name") or "")]

    return {
        "user_mailboxes": [_to_mailbox_row(r) for r in user_filtered],
        "shared_mailboxes": [_to_mailbox_row(r) for r in shared_filtered],
        "total_user": len(user_filtered),
        "total_shared": len(shared_filtered),
    }


async def _build_orders_detail(company_id: int) -> dict[str, Any]:
    """All orders from the past 3 months for the detail page."""
    orders = await shop_repo.list_order_summaries(company_id)
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=90)
    filtered: list[dict[str, Any]] = []
    for order in orders or []:
        order_date = _coerce_date(order.get("order_date"))
        if order_date is None or order_date < cutoff:
            continue
        filtered.append(
            {
                "order_number": order.get("order_number"),
                "order_date": order_date.isoformat(),
                "status": order.get("status") or "unknown",
                "shipping_status": order.get("shipping_status"),
                "po_number": order.get("po_number"),
            }
        )
    filtered.sort(key=lambda o: o.get("order_date") or "", reverse=True)
    return {
        "orders": filtered,
        "total": len(filtered),
        "since": cutoff.isoformat(),
    }


async def _build_licenses_detail(company_id: int) -> dict[str, Any]:
    """License list with per-license staff assignments for the detail page."""
    records = await licenses_repo.list_company_licenses(company_id)
    staff_by_license = await licenses_repo.list_staff_by_license_for_company(company_id)
    licenses: list[dict[str, Any]] = []
    for record in records:
        total = int(record.get("count") or 0)
        allocated = int(record.get("allocated") or 0)
        license_id = record.get("id")
        staff = staff_by_license.get(license_id, [])
        licenses.append(
            {
                "name": record.get("display_name") or record.get("name"),
                "total": total,
                "allocated": allocated,
                "staff": [
                    {
                        "name": (
                            f"{s.get('first_name', '')} {s.get('last_name', '')}".strip()
                            or s.get("email")
                            or "Unknown"
                        ),
                        "email": s.get("email"),
                    }
                    for s in staff
                ],
            }
        )
    return {"licenses": licenses, "total": len(licenses)}


async def _build_subscriptions_detail(company_id: int) -> dict[str, Any]:
    """Full subscription data (same as summary, all available fields) for the detail page."""
    records = await subscriptions_repo.list_subscriptions(customer_id=company_id)
    subscriptions: list[dict[str, Any]] = []
    for record in records:
        subscriptions.append(
            {
                "subscription_id": record.get("id") or record.get("subscription_id"),
                "product_name": record.get("product_name"),
                "category_name": record.get("category_name"),
                "quantity": record.get("quantity"),
                "status": record.get("status") or "unknown",
                "start_date": _date_to_iso(record.get("start_date")),
                "end_date": _date_to_iso(record.get("end_date")),
                "commitment_term": record.get("commitment_term"),
                "billing_cycle": record.get("billing_cycle"),
                "unit_price": record.get("unit_price"),
                "currency": record.get("currency"),
            }
        )
    return {"subscriptions": subscriptions, "total": len(subscriptions)}


async def _build_essential8_detail(company_id: int) -> dict[str, Any]:
    """Per-control breakdown with maturity-level statuses for the detail page."""
    controls = await essential8_repo.list_essential8_controls()
    per_control = await essential8_repo.get_per_maturity_statuses_for_company(company_id)
    rows: list[dict[str, Any]] = []
    for control in controls:
        control_id = control.get("id")
        statuses = per_control.get(control_id, {}) if control_id is not None else {}
        rows.append(
            {
                "id": control_id,
                "name": control.get("name"),
                "description": control.get("description"),
                "ml1": statuses.get("ml1", "not_started"),
                "ml2": statuses.get("ml2", "not_started"),
                "ml3": statuses.get("ml3", "not_started"),
            }
        )
    return {"controls": rows, "total": len(rows)}


async def _build_compliance_checks_detail(company_id: int) -> dict[str, Any]:
    """Full list of compliance check assignments for the detail page."""
    assignments = await compliance_checks_repo.list_assignments(company_id)
    rows: list[dict[str, Any]] = []
    for a in assignments:
        rows.append(
            {
                "check_title": a.get("check_title"),
                "category_name": a.get("category_name"),
                "status": a.get("status") or "unknown",
                "next_review_at": _datetime_to_iso(a.get("next_review_at")),
                "last_checked_at": _datetime_to_iso(a.get("last_checked_at")),
                "notes": a.get("notes"),
                "evidence_summary": a.get("evidence_summary"),
            }
        )
    rows.sort(key=lambda r: (r.get("category_name") or "", r.get("check_title") or ""))
    return {"assignments": rows, "total": len(rows)}


async def _build_tickets_detail(company_id: int) -> dict[str, Any]:
    """Full ticket list (individual tickets) from the past 30 days for the detail page."""
    since = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        rows = await db.fetch_all(
            """
            SELECT id, subject, status, priority, category, created_at, updated_at
            FROM tickets
            WHERE company_id = %s AND created_at >= %s
            ORDER BY created_at DESC
            """,
            (company_id, since.replace(tzinfo=None)),
        )
    except Exception:  # pragma: no cover
        rows = []
    tickets: list[dict[str, Any]] = []
    for row in rows or []:
        created = row.get("created_at")
        updated = row.get("updated_at")
        tickets.append(
            {
                "id": row.get("id"),
                "subject": row.get("subject"),
                "status": str(row.get("status") or "").lower(),
                "priority": row.get("priority"),
                "category": row.get("category"),
                "created_at": created.isoformat() if isinstance(created, datetime) else str(created or ""),
                "updated_at": updated.isoformat() if isinstance(updated, datetime) else str(updated or ""),
            }
        )
    return {"tickets": tickets, "total": len(tickets), "since": since.isoformat()}


async def _build_asset_custom_fields_detail(company_id: int) -> dict[str, Any]:
    """Asset custom fields with unlimited value rows for the detail page."""
    definitions = await asset_custom_fields_repo.list_field_definitions()
    fields_out: list[dict[str, Any]] = []
    for definition in definitions:
        field_type = str(definition.get("field_type") or "").lower()
        definition_id = definition.get("id")
        if definition_id is None:
            continue
        if field_type in {"text", "url"}:
            # Unlimited rows (summary caps at 20).
            rows = await db.fetch_all(
                """
                SELECT v.value_text AS value, COUNT(DISTINCT v.asset_id) AS total
                FROM asset_custom_field_values v
                JOIN assets a ON v.asset_id = a.id
                WHERE v.field_definition_id = %s
                  AND a.company_id = %s
                  AND v.value_text IS NOT NULL AND v.value_text <> ''
                GROUP BY v.value_text
                ORDER BY total DESC
                """,
                (definition_id, company_id),
            )
            counts = [
                {"value": row.get("value") or "", "count": int(row.get("total") or 0)}
                for row in rows or []
            ]
        else:
            counts = await _count_custom_field_values(company_id, definition, field_type)
        fields_out.append(
            {
                "name": definition.get("name"),
                "display_name": definition.get("display_name") or definition.get("name"),
                "field_type": field_type,
                "values": counts,
                "total": sum(entry["count"] for entry in counts),
            }
        )
    return {"fields": fields_out}


async def _build_issues_detail(company_id: int) -> dict[str, Any]:
    """Full issue list with description body for the detail page (same dataset as summary)."""
    return await _build_issues(company_id)


async def _build_backup_jobs_detail(company_id: int) -> dict[str, Any]:
    """Per-day backup history grid for the detail page (last 30 days)."""
    # Imported lazily to avoid a circular dependency between reports and
    # backup_jobs services (they both consume the same repository).
    from app.services import backup_jobs as backup_jobs_service

    end = datetime.now(timezone.utc).date()
    grid = await backup_jobs_service.build_history_grid(
        company_id=company_id, days=30, end_date=end, include_inactive=True
    )
    rows: list[dict[str, Any]] = []
    for row in grid["rows"]:
        job = row["job"]
        rows.append(
            {
                "name": job.get("name"),
                "is_active": bool(job.get("is_active", True)),
                "events": [
                    {
                        "date": cell["date"].isoformat(),
                        "status": cell["status"],
                        "label": cell["label"],
                        "variant": cell["variant"],
                        "pdf_color": cell["pdf_color"],
                        "message": cell.get("message"),
                    }
                    for cell in row["events"]
                ],
            }
        )
    return {
        "dates": [d.isoformat() for d in grid["dates"]],
        "rows": rows,
        "start_date": grid["start_date"].isoformat(),
        "end_date": grid["end_date"].isoformat(),
        "total_jobs": len(rows),
    }


# Maps section keys to their detail builder coroutine.
_DETAIL_BUILDERS: dict[str, Any] = {
    "assets": _build_assets_detail,
    "staff": _build_staff_detail,
    "m365_best_practices": _build_m365_best_practices_detail,
    "top_mailboxes": _build_top_mailboxes_detail,
    "orders_current_month": _build_orders_detail,
    "licenses": _build_licenses_detail,
    "subscriptions": _build_subscriptions_detail,
    "essential8": _build_essential8_detail,
    "compliance_checks": _build_compliance_checks_detail,
    "tickets_last_month": _build_tickets_detail,
    "asset_custom_fields": _build_asset_custom_fields_detail,
    "issues": _build_issues_detail,
    "backup_jobs": _build_backup_jobs_detail,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_section_visibility(company_id: int) -> dict[str, bool]:
    """Return an ``{section_key: enabled}`` map for every section.

    Unset sections default to enabled so that new report sections appear
    automatically without needing a data migration.
    """
    stored = await report_sections_repo.get_section_preferences(company_id)
    return {section.key: stored.get(section.key, True) for section in REPORT_SECTIONS}


async def save_section_visibility(
    company_id: int,
    preferences: Mapping[str, Any],
) -> dict[str, bool]:
    """Persist visibility preferences, returning the canonical map afterwards."""
    cleaned: dict[str, bool] = {}
    for section in REPORT_SECTIONS:
        raw = preferences.get(section.key)
        cleaned[section.key] = _coerce_bool(raw, default=True)
    await report_sections_repo.set_section_preferences(
        company_id, cleaned, valid_keys=SECTION_KEYS
    )
    return cleaned


async def get_section_detail_visibility(company_id: int) -> dict[str, bool]:
    """Return a ``{section_key: detailed}`` map for every section.

    Unset sections default to ``False`` (no detail page).
    """
    stored = await report_sections_repo.get_detail_preferences(company_id)
    return {section.key: stored.get(section.key, False) for section in REPORT_SECTIONS}


async def save_section_detail_visibility(
    company_id: int,
    preferences: Mapping[str, Any],
) -> dict[str, bool]:
    """Persist detail-page preferences, returning the canonical map afterwards."""
    cleaned: dict[str, bool] = {}
    for section in REPORT_SECTIONS:
        raw = preferences.get(section.key)
        cleaned[section.key] = _coerce_bool(raw, default=False)
    await report_sections_repo.set_detail_preferences(
        company_id, cleaned, valid_keys=SECTION_KEYS
    )
    return cleaned


async def get_company_report_settings(company_id: int) -> dict[str, Any]:
    """Return report-level settings (auto_hide_empty, section_order) for a company."""
    return await report_sections_repo.get_company_report_settings(company_id)


async def save_company_report_settings(
    company_id: int,
    auto_hide_empty: bool,
    section_order: list[str] | None,
) -> None:
    """Persist report-level settings for a company."""
    # Normalise section_order: only keep valid section keys, discard unknown ones.
    if section_order is not None:
        valid_order = [k for k in section_order if k in SECTION_KEYS]
        # Append any canonical keys that were missing from the supplied order.
        supplied = set(valid_order)
        for s in REPORT_SECTIONS:
            if s.key not in supplied:
                valid_order.append(s.key)
        section_order = valid_order
    await report_sections_repo.save_company_report_settings(
        company_id, auto_hide_empty, section_order
    )


async def build_company_report(company_id: int) -> ReportData:
    """Build the full report for a single company."""
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")

    visibility = await get_section_visibility(company_id)
    detail_visibility = await get_section_detail_visibility(company_id)
    report_settings = await get_company_report_settings(company_id)
    auto_hide_empty: bool = report_settings.get("auto_hide_empty", True)
    section_order: list[str] | None = report_settings.get("section_order")

    # Build ordered list of section definitions.
    if section_order:
        key_to_def = {s.key: s for s in REPORT_SECTIONS}
        ordered_defs = [key_to_def[k] for k in section_order if k in key_to_def]
    else:
        ordered_defs = list(REPORT_SECTIONS)

    sections: list[SectionResult] = []
    for section_def in ordered_defs:
        enabled = visibility.get(section_def.key, True)
        data: dict[str, Any] = {}
        if enabled:
            builder = _SECTION_BUILDERS.get(section_def.key)
            if builder is not None:
                try:
                    data = await builder(company_id)
                except Exception as exc:  # pragma: no cover - defensive
                    data = {"error": str(exc)}
        is_empty = _section_is_empty(section_def.key, data)
        # When auto-hide is active, treat empty-but-enabled sections as hidden.
        if enabled and auto_hide_empty and is_empty:
            enabled = False
        # Detail page: only populated when the section is enabled and detailed flag is set.
        detailed = enabled and detail_visibility.get(section_def.key, False)
        detail_data: dict[str, Any] = {}
        if detailed:
            detail_builder = _DETAIL_BUILDERS.get(section_def.key)
            if detail_builder is not None:
                try:
                    detail_data = await detail_builder(company_id)
                except Exception as exc:  # pragma: no cover - defensive
                    detail_data = {"error": f"Failed to load detail data for {section_def.key}: {exc}"}
        sections.append(
            SectionResult(
                key=section_def.key,
                label=section_def.label,
                enabled=enabled,
                data=data,
                is_empty=is_empty,
                detailed=detailed,
                detail_data=detail_data,
            )
        )
    return ReportData(
        company=company,
        generated_at=datetime.now(timezone.utc),
        sections=sections,
        auto_hide_empty=auto_hide_empty,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section_is_empty(key: str, data: dict[str, Any]) -> bool:
    """Return True when a section has no meaningful content to display."""
    if not data or "error" in data:
        return True
    if key == "assets":
        return int(data.get("total_synced") or 0) == 0
    if key == "staff":
        return int(data.get("total_active") or 0) == 0
    if key == "m365_best_practices":
        return int(data.get("total") or 0) == 0
    if key == "top_mailboxes":
        return not (data.get("user_mailboxes") or data.get("shared_mailboxes"))
    if key == "orders_current_month":
        return int(data.get("total") or 0) == 0
    if key == "licenses":
        return int(data.get("total") or 0) == 0
    if key == "subscriptions":
        return int(data.get("total") or 0) == 0
    if key == "essential8":
        levels = data.get("levels") or []
        return len(levels) == 0 or all(int(lvl.get("total") or 0) == 0 for lvl in levels)
    if key == "compliance_checks":
        return int(data.get("total") or 0) == 0
    if key == "tickets_last_month":
        return int(data.get("total") or 0) == 0
    if key == "asset_custom_fields":
        fields = data.get("fields") or []
        return len(fields) == 0
    if key == "issues":
        return int(data.get("total") or 0) == 0
    if key == "backup_jobs":
        return int(data.get("total_jobs") or 0) == 0
    return False



def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _date_to_iso(value: Any) -> str | None:
    coerced = _coerce_date(value)
    return coerced.isoformat() if coerced else None


def _datetime_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _max_datetime(values: Iterable[Any]) -> str | None:
    best: datetime | None = None
    for candidate in values:
        dt: datetime | None = None
        if isinstance(candidate, datetime):
            dt = candidate
        elif isinstance(candidate, date):
            dt = datetime.combine(candidate, time.min)
        elif isinstance(candidate, str):
            try:
                dt = datetime.fromisoformat(candidate)
            except ValueError:
                dt = None
        if dt is None:
            continue
        if best is None or dt > best:
            best = dt
    return best.isoformat() if best else None


__all__ = [
    "REPORT_SECTIONS",
    "SECTION_KEYS",
    "ReportData",
    "ReportSection",
    "SectionResult",
    "build_company_report",
    "get_company_report_settings",
    "get_section_visibility",
    "save_company_report_settings",
    "save_section_visibility",
]
