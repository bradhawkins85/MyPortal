from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from app.repositories import companies as company_repo
from app.repositories import issues as issues_repo


STATUS_OPTIONS: list[tuple[str, str]] = [
    ("new", "New"),
    ("investigating", "Investigating"),
    ("in_progress", "In progress"),
    ("monitoring", "Monitoring"),
    ("resolved", "Resolved"),
    ("closed", "Closed"),
]

DEFAULT_STATUS = "new"
ISSUE_TRACKER_PERMISSION_KEY = "issue_tracker.access"
_STATUS_LOOKUP = {value: label for value, label in STATUS_OPTIONS}


def normalise_status(value: str | None) -> str:
    if not value:
        return DEFAULT_STATUS
    cleaned = value.strip().lower().replace(" ", "_")
    if cleaned not in _STATUS_LOOKUP:
        raise ValueError("Invalid issue status")
    return cleaned


def get_status_label(value: str | None) -> str:
    if not value:
        return _STATUS_LOOKUP[DEFAULT_STATUS]
    key = value.strip().lower()
    return _STATUS_LOOKUP.get(key, value)


@dataclass
class IssueAssignment:
    assignment_id: int | None
    issue_id: int
    company_id: int
    company_name: str | None
    status: str
    status_label: str
    updated_at: datetime | None
    updated_at_iso: str | None


@dataclass
class IssueOverview:
    issue_id: int
    name: str
    slug: str | None
    description: str | None
    created_at: datetime | None
    created_at_iso: str | None
    updated_at: datetime | None
    updated_at_iso: str | None
    assignments: list[IssueAssignment]


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _build_assignment(issue_id: int, payload: dict[str, Any]) -> IssueAssignment:
    assignment_id = _coerce_int(payload.get("assignment_id"))
    company_id = _coerce_int(payload.get("company_id"))
    company_name = payload.get("company_name")
    if isinstance(company_name, str):
        company_name = company_name.strip() or None
    try:
        status_value = normalise_status(str(payload.get("status") or DEFAULT_STATUS))
    except ValueError:
        status_value = DEFAULT_STATUS
    updated_at = _coerce_datetime(payload.get("updated_at_utc"))
    updated_iso = updated_at.isoformat() if updated_at else None
    return IssueAssignment(
        assignment_id=assignment_id,
        issue_id=issue_id,
        company_id=company_id,
        company_name=company_name,
        status=status_value,
        status_label=get_status_label(status_value),
        updated_at=updated_at,
        updated_at_iso=updated_iso,
    )


def _build_overview(record: dict[str, Any]) -> IssueOverview | None:
    issue_id = _coerce_int(record.get("issue_id"))
    name = (record.get("name") or "").strip()
    if issue_id is None or not name:
        return None
    created_at = _coerce_datetime(record.get("created_at_utc"))
    updated_at = _coerce_datetime(record.get("updated_at_utc"))
    assignments_payload = record.get("assignments") or []
    assignments: list[IssueAssignment] = []
    for assignment in assignments_payload:
        if not isinstance(assignment, dict):
            continue
        assignments.append(_build_assignment(issue_id, assignment))
    created_iso = created_at.isoformat() if created_at else None
    updated_iso = updated_at.isoformat() if updated_at else None
    slug = record.get("slug")
    if isinstance(slug, str):
        slug = slug.strip() or None
    description = record.get("description")
    if isinstance(description, str):
        description = description.strip() or None
    else:
        description = None if description is None else str(description)
    return IssueOverview(
        issue_id=issue_id,
        name=name,
        slug=slug,
        description=description,
        created_at=created_at,
        created_at_iso=created_iso,
        updated_at=updated_at,
        updated_at_iso=updated_iso,
        assignments=assignments,
    )


async def build_issue_overview(
    *,
    search: str | None = None,
    status: str | None = None,
    company_id: int | None = None,
) -> list[IssueOverview]:
    status_filter = normalise_status(status) if status else None
    search_term = search.strip() if search else ""
    rows = await issues_repo.list_issues_with_assignments(
        search=search_term.lower() if search_term else None,
        status=status_filter,
        company_id=company_id,
    )
    overview: list[IssueOverview] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = _build_overview(row)
        if record:
            overview.append(record)
    return overview


async def ensure_companies_exist(company_ids: Iterable[int]) -> None:
    for company_id in company_ids:
        record = await company_repo.get_company_by_id(company_id)
        if not record:
            raise ValueError(f"Company {company_id} not found")


async def ensure_issue_name_available(name: str, *, exclude_issue_id: int | None = None) -> None:
    existing = await issues_repo.get_issue_by_name(name)
    if not existing:
        return
    if exclude_issue_id is not None and existing.get("issue_id") == exclude_issue_id:
        return
    raise ValueError("Issue name already exists")


async def resolve_company_by_name(name: str) -> dict[str, Any]:
    company = await company_repo.get_company_by_name(name)
    if not company:
        raise ValueError("Company not found")
    return company


async def get_issue_overview(issue_id: int) -> IssueOverview | None:
    record = await issues_repo.get_issue_by_id(issue_id)
    if not record:
        return None
    return _build_overview(record)


async def get_issue_overview_by_name(name: str) -> IssueOverview | None:
    record = await issues_repo.get_issue_by_name(name)
    if not record:
        return None
    return _build_overview(record)


async def upsert_issue_status_by_name(
    *,
    issue_name: str,
    company_name: str,
    status: str,
    updated_by: int | None = None,
) -> IssueAssignment:
    issue = await issues_repo.get_issue_by_name(issue_name)
    if not issue or issue.get("issue_id") is None:
        raise ValueError("Issue not found")
    company = await resolve_company_by_name(company_name)
    status_value = normalise_status(status)
    assignment = await issues_repo.assign_issue_to_company(
        issue_id=int(issue["issue_id"]),
        company_id=int(company["id"]),
        status=status_value,
        updated_by=updated_by,
    )
    updated_at = assignment.get("updated_at_utc")
    updated_iso = updated_at.astimezone(timezone.utc).isoformat() if isinstance(updated_at, datetime) else None
    return IssueAssignment(
        assignment_id=assignment.get("assignment_id"),
        issue_id=int(issue["issue_id"]),
        company_id=int(company["id"]),
        company_name=company.get("name"),
        status=status_value,
        status_label=get_status_label(status_value),
        updated_at=updated_at if isinstance(updated_at, datetime) else None,
        updated_at_iso=updated_iso,
    )
