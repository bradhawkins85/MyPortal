"""Configurable HTML-style company report layout and query renderer."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.repositories import company_report_layouts as layout_repo
from app.repositories import reporting as reporting_repo
from app.services import reporting as reporting_service

MAX_COLUMNS = 12
MAX_ROWS = 50
ALLOWED_AGGREGATES = {"value", "count", "sum", "average", "minimum", "maximum"}
ALLOWED_OPERATORS = {"gte", "gt", "lte", "lt", "eq"}
ALLOWED_COLOURS = {"neutral", "info", "success", "warning", "danger"}


def default_layout() -> list[dict[str, Any]]:
    """Return a fresh default approximating the previous overview sections."""
    slugs = [
        ("Assets synced", "report-assets-synced-last-30-days"),
        ("Active staff", "report-active-staff"),
        ("Orders this month", "report-orders-this-month"),
        ("M365 best practice summary", "report-m365-best-practice-summary"),
        ("Top mailboxes by size", "report-top-mailboxes-by-size"),
        ("Licenses", "report-licenses"),
        ("Subscriptions", "report-subscriptions"),
        ("Essential 8 compliance", "report-essential-8-compliance-progress"),
        ("Customer compliance checks", "report-customer-compliance-checks"),
        ("Tickets (past month)", "report-tickets-past-month"),
        ("Backup history", "report-backup-history"),
    ]
    rows = [{"title": "At a glance", "columns": [
        {"slug": slug, "title": title, "display": "stat", "aggregate": "count", "thresholds": []}
        for title, slug in slugs[:3]
    ]}]
    rows.extend(
        {"title": title, "columns": [{"slug": slug, "title": title, "display": "table", "aggregate": "value", "thresholds": []}]}
        for title, slug in slugs[3:]
    )
    return rows


def _text(value: Any, limit: int = 255) -> str:
    return str(value or "").strip()[:limit]


def normalise_layout(value: Any, valid_slugs: set[str]) -> list[dict[str, Any]]:
    """Validate browser input and discard unknown reports/options."""
    if not isinstance(value, list):
        raise ValueError("Report layout must be a list of rows.")
    rows: list[dict[str, Any]] = []
    for raw_row in value[:MAX_ROWS]:
        if not isinstance(raw_row, dict):
            continue
        columns: list[dict[str, Any]] = []
        for raw in (raw_row.get("columns") or [])[:MAX_COLUMNS]:
            if not isinstance(raw, dict):
                continue
            slug = _text(raw.get("slug"), 120)
            if slug not in valid_slugs:
                continue
            display = "stat" if raw.get("display") == "stat" else "table"
            aggregate = _text(raw.get("aggregate"), 16).lower()
            if aggregate not in ALLOWED_AGGREGATES:
                aggregate = "value"
            thresholds = []
            for threshold in (raw.get("thresholds") or [])[:8]:
                if not isinstance(threshold, dict):
                    continue
                operator = _text(threshold.get("operator"), 4).lower()
                colour = _text(threshold.get("colour"), 12).lower()
                try:
                    number = float(threshold.get("value"))
                except (TypeError, ValueError):
                    continue
                if operator in ALLOWED_OPERATORS and colour in ALLOWED_COLOURS:
                    thresholds.append({"operator": operator, "value": number, "colour": colour})
            columns.append({
                "slug": slug, "title": _text(raw.get("title"), 120), "display": display,
                "value_column": _text(raw.get("value_column"), 120), "aggregate": aggregate,
                "filter_column": _text(raw.get("filter_column"), 120),
                "filter_value": _text(raw.get("filter_value"), 255), "suffix": _text(raw.get("suffix"), 24),
                "thresholds": thresholds,
            })
        if columns:
            rows.append({"title": _text(raw_row.get("title"), 160), "columns": columns})
    if not rows:
        raise ValueError("Add at least one row containing a reporting slug.")
    return rows


def _stat_value(config: dict[str, Any], result: dict[str, Any]) -> Any:
    rows = result.get("rows") or []
    filter_column, filter_value = config.get("filter_column"), config.get("filter_value")
    if filter_column:
        rows = [r for r in rows if str(r.get(filter_column, "")).lower() == str(filter_value).lower()]
    aggregate = config.get("aggregate") or "value"
    if aggregate == "count":
        return len(rows)
    column = config.get("value_column") or (result.get("columns") or [None])[0]
    values = [row.get(column) for row in rows if column in row and row.get(column) is not None]
    if not values:
        return 0 if aggregate != "value" else "—"
    if aggregate == "value":
        return values[0]
    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError):
        return "—"
    computed = {"sum": sum, "minimum": min, "maximum": max}.get(aggregate)
    value = (sum(numbers) / len(numbers)) if aggregate == "average" else computed(numbers)
    return int(value) if value == int(value) else round(value, 2)


def _variant(value: Any, thresholds: list[dict[str, Any]]) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "neutral"
    comparisons = {"gte": lambda a, b: a >= b, "gt": lambda a, b: a > b,
                   "lte": lambda a, b: a <= b, "lt": lambda a, b: a < b, "eq": lambda a, b: a == b}
    for threshold in thresholds:
        if comparisons[threshold["operator"]](number, threshold["value"]):
            return threshold["colour"]
    return "neutral"


@dataclass
class LayoutReport:
    company: dict[str, Any]
    generated_at: datetime
    rows: list[dict[str, Any]] = field(default_factory=list)


async def available_queries() -> list[dict[str, Any]]:
    return await reporting_repo.list_queries()


async def get_layout(company_id: int) -> list[dict[str, Any]]:
    return await layout_repo.get_layout(company_id) or default_layout()


async def save_layout(company_id: int, value: Any) -> list[dict[str, Any]]:
    queries = await available_queries()
    rows = normalise_layout(value, {str(q.get("slug")) for q in queries})
    await layout_repo.save_layout(company_id, rows)
    return rows


async def build(company_id: int, company: dict[str, Any]) -> LayoutReport:
    layout, queries = await get_layout(company_id), await available_queries()
    by_slug = {str(query.get("slug")): query for query in queries}
    cache: dict[str, dict[str, Any]] = {}
    rendered_rows = []
    for row in layout:
        rendered_columns = []
        for config in row.get("columns", []):
            slug = config.get("slug")
            query = by_slug.get(slug)
            error = None
            if not query:
                result, error = {"columns": [], "rows": [], "row_count": 0}, "Reporting slug not found."
            else:
                try:
                    if slug not in cache:
                        cache[slug] = await reporting_service.run_query_with_context(query["sql_query"], company_id=company_id, max_rows=500)
                    result = cache[slug]
                except Exception as exc:  # defensive: one cell must not break the report
                    result, error = {"columns": [], "rows": [], "row_count": 0}, str(exc)
            cell = dict(config)
            cell.update({"name": query.get("name") if query else slug, "result": result, "error": error})
            if config.get("display") == "stat":
                value = _stat_value(config, result)
                cell.update({"value": value, "variant": _variant(value, config.get("thresholds") or [])})
            rendered_columns.append(cell)
        if rendered_columns:
            rendered_rows.append({"title": row.get("title"), "columns": rendered_columns})
    return LayoutReport(company=company, generated_at=datetime.now(timezone.utc), rows=rendered_rows)
