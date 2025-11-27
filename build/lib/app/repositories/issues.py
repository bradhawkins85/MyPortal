from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.core.database import db

IssueRecord = dict[str, Any]
AssignmentRecord = dict[str, Any]


def _ensure_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _normalise_issue(row: Mapping[str, Any] | None) -> IssueRecord | None:
    if not row:
        return None
    record: IssueRecord = dict(row)
    for key in ("issue_id", "id"):
        if key in record and record[key] is not None:
            try:
                record[key] = int(record[key])
            except (TypeError, ValueError):
                record[key] = None
    for key in ("created_by", "updated_by"):
        if key in record and record[key] is not None:
            try:
                record[key] = int(record[key])
            except (TypeError, ValueError):
                record[key] = None
    for key in ("created_at_utc", "updated_at_utc"):
        record[key] = _ensure_aware(record.get(key))
    name = record.get("name")
    if isinstance(name, bytes):
        record["name"] = name.decode("utf-8", errors="ignore")
    slug = record.get("slug")
    if isinstance(slug, bytes):
        record["slug"] = slug.decode("utf-8", errors="ignore")
    description = record.get("description")
    if isinstance(description, bytes):
        record["description"] = description.decode("utf-8", errors="ignore")
    return record


def _normalise_assignment(row: Mapping[str, Any] | None) -> AssignmentRecord | None:
    if not row:
        return None
    record: AssignmentRecord = dict(row)
    for key in ("assignment_id", "issue_id", "company_id", "updated_by"):
        if key in record and record[key] is not None:
            try:
                record[key] = int(record[key])
            except (TypeError, ValueError):
                record[key] = None
    for key in ("created_at_utc", "updated_at_utc"):
        record[key] = _ensure_aware(record.get(key))
    status = record.get("status")
    if isinstance(status, bytes):
        record["status"] = status.decode("utf-8", errors="ignore")
    company_name = record.get("company_name")
    if isinstance(company_name, bytes):
        record["company_name"] = company_name.decode("utf-8", errors="ignore")
    notes = record.get("notes")
    if isinstance(notes, bytes):
        record["notes"] = notes.decode("utf-8", errors="ignore")
    return record


async def list_issues_with_assignments(
    *,
    search: str | None = None,
    status: str | None = None,
    company_id: int | None = None,
) -> list[IssueRecord]:
    where: list[str] = []
    params: list[Any] = []

    if search:
        like = f"%{search.lower()}%"
        where.append("(LOWER(i.name) LIKE %s OR LOWER(i.description) LIKE %s)")
        params.extend([like, like])

    if status:
        where.append("ics.status = %s")
        params.append(status)

    if company_id is not None:
        where.append("ics.company_id = %s")
        params.append(company_id)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    rows = await db.fetch_all(
        f"""
        SELECT
            i.id AS issue_id,
            i.name,
            i.slug,
            i.description,
            i.created_by,
            i.updated_by,
            i.created_at_utc,
            i.updated_at_utc,
            ics.id AS assignment_id,
            ics.status,
            ics.notes,
            ics.updated_by AS assignment_updated_by,
            ics.created_at_utc AS assignment_created_at_utc,
            ics.updated_at_utc AS assignment_updated_at_utc,
            c.id AS company_id,
            c.name AS company_name
        FROM issue_definitions AS i
        LEFT JOIN issue_company_statuses AS ics ON ics.issue_id = i.id
        LEFT JOIN companies AS c ON c.id = ics.company_id
        {where_clause}
        ORDER BY i.name ASC, c.name ASC
        """,
        tuple(params),
    )

    grouped: dict[int, IssueRecord] = {}
    assignments: dict[int, list[AssignmentRecord]] = defaultdict(list)

    for row in rows:
        issue = _normalise_issue(row)
        if not issue:
            continue
        issue_id = issue.get("issue_id")
        if issue_id is None:
            continue
        grouped.setdefault(issue_id, issue)
        assignment = _normalise_assignment(
            {
                "assignment_id": row.get("assignment_id"),
                "issue_id": row.get("issue_id"),
                "company_id": row.get("company_id"),
                "company_name": row.get("company_name"),
                "status": row.get("status"),
                "notes": row.get("notes"),
                "created_at_utc": row.get("assignment_created_at_utc"),
                "updated_at_utc": row.get("assignment_updated_at_utc"),
                "updated_by": row.get("assignment_updated_by"),
            }
        )
        if assignment and assignment.get("company_id") is not None:
            assignments[issue_id].append(assignment)

    if not rows and not grouped and not where:
        # When no assignments exist at all ensure standalone issues are returned
        standalone_rows = await db.fetch_all(
            """
            SELECT id AS issue_id, name, slug, description, created_by, updated_by, created_at_utc, updated_at_utc
            FROM issue_definitions
            ORDER BY name ASC
            """
        )
        for row in standalone_rows:
            issue = _normalise_issue(row)
            if not issue:
                continue
            issue_id = issue.get("issue_id")
            if issue_id is None:
                continue
            grouped.setdefault(issue_id, issue)

    result: list[IssueRecord] = []
    for issue_id, issue in sorted(grouped.items(), key=lambda item: (item[1].get("name") or "").lower()):
        enriched = dict(issue)
        enriched["assignments"] = assignments.get(issue_id, [])
        result.append(enriched)
    return result


async def get_issue_by_id(issue_id: int) -> IssueRecord | None:
    row = await db.fetch_one(
        "SELECT id AS issue_id, name, slug, description, created_by, updated_by, created_at_utc, updated_at_utc FROM issue_definitions WHERE id = %s",
        (issue_id,),
    )
    issue = _normalise_issue(row)
    if not issue:
        return None
    assignment_rows = await db.fetch_all(
        """
        SELECT
            id AS assignment_id,
            issue_id,
            company_id,
            status,
            notes,
            updated_by,
            created_at_utc,
            updated_at_utc
        FROM issue_company_statuses
        WHERE issue_id = %s
        ORDER BY updated_at_utc DESC
        """,
        (issue_id,),
    )
    issue["assignments"] = [assignment for assignment in (_normalise_assignment(row) for row in assignment_rows) if assignment]
    return issue


async def get_issue_by_name(name: str) -> IssueRecord | None:
    row = await db.fetch_one(
        """
        SELECT id AS issue_id, name, slug, description, created_by, updated_by, created_at_utc, updated_at_utc
        FROM issue_definitions
        WHERE LOWER(name) = LOWER(%s)
        LIMIT 1
        """,
        (name,),
    )
    if not row:
        return None
    issue = _normalise_issue(row)
    if not issue or issue.get("issue_id") is None:
        return issue
    assignments = await db.fetch_all(
        """
        SELECT
            ics.id AS assignment_id,
            ics.issue_id,
            ics.company_id,
            ics.status,
            ics.notes,
            ics.updated_by,
            ics.created_at_utc,
            ics.updated_at_utc,
            c.name AS company_name
        FROM issue_company_statuses AS ics
        INNER JOIN companies AS c ON c.id = ics.company_id
        WHERE ics.issue_id = %s
        ORDER BY c.name ASC
        """,
        (issue["issue_id"],),
    )
    issue["assignments"] = [assignment for assignment in (_normalise_assignment(row) for row in assignments) if assignment]
    return issue


async def get_issue_by_slug(slug: str) -> IssueRecord | None:
    """Get an issue by its slug."""
    row = await db.fetch_one(
        """
        SELECT id AS issue_id, name, slug, description, created_by, updated_by, created_at_utc, updated_at_utc
        FROM issue_definitions
        WHERE LOWER(slug) = LOWER(%s)
        LIMIT 1
        """,
        (slug,),
    )
    if not row:
        return None
    issue = _normalise_issue(row)
    if not issue or issue.get("issue_id") is None:
        return issue
    assignments = await db.fetch_all(
        """
        SELECT
            ics.id AS assignment_id,
            ics.issue_id,
            ics.company_id,
            ics.status,
            ics.notes,
            ics.updated_by,
            ics.created_at_utc,
            ics.updated_at_utc,
            c.name AS company_name
        FROM issue_company_statuses AS ics
        INNER JOIN companies AS c ON c.id = ics.company_id
        WHERE ics.issue_id = %s
        ORDER BY c.name ASC
        """,
        (issue["issue_id"],),
    )
    issue["assignments"] = [assignment for assignment in (_normalise_assignment(row) for row in assignments) if assignment]
    return issue


async def create_issue(*, name: str, description: str | None, created_by: int | None, slug: str | None = None) -> IssueRecord:
    issue_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO issue_definitions (name, slug, description, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (name, slug, description, created_by, created_by),
    )
    issue = await get_issue_by_id(issue_id)
    if issue:
        return issue
    fallback: IssueRecord = {
        "issue_id": issue_id,
        "name": name,
        "slug": slug,
        "description": description,
        "created_by": created_by,
        "updated_by": created_by,
        "created_at_utc": None,
        "updated_at_utc": None,
        "assignments": [],
    }
    return fallback


async def update_issue(
    issue_id: int,
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    updated_by: int | None = None,
) -> IssueRecord:
    updates: list[str] = []
    params: list[Any] = []
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if slug is not None:
        updates.append("slug = %s")
        params.append(slug)
    if description is not None:
        updates.append("description = %s")
        params.append(description)
    if updated_by is not None:
        updates.append("updated_by = %s")
        params.append(updated_by)
    if updates:
        params.append(issue_id)
        set_clause = ", ".join(updates)
        await db.execute(
            f"UPDATE issue_definitions SET {set_clause}, updated_at_utc = CURRENT_TIMESTAMP(6) WHERE id = %s",
            tuple(params),
        )
    issue = await get_issue_by_id(issue_id)
    if not issue:
        raise ValueError("Issue not found")
    return issue


async def delete_issue(issue_id: int) -> None:
    await db.execute("DELETE FROM issue_definitions WHERE id = %s", (issue_id,))


async def assign_issue_to_company(
    *,
    issue_id: int,
    company_id: int,
    status: str,
    updated_by: int | None = None,
    notes: str | None = None,
) -> AssignmentRecord:
    await db.execute(
        """
        INSERT INTO issue_company_statuses (issue_id, company_id, status, notes, updated_by)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            status = VALUES(status),
            notes = VALUES(notes),
            updated_by = VALUES(updated_by),
            updated_at_utc = CURRENT_TIMESTAMP(6)
        """,
        (issue_id, company_id, status, notes, updated_by),
    )
    row = await db.fetch_one(
        """
        SELECT id AS assignment_id, issue_id, company_id, status, notes, updated_by, created_at_utc, updated_at_utc
        FROM issue_company_statuses
        WHERE issue_id = %s AND company_id = %s
        """,
        (issue_id, company_id),
    )
    assignment = _normalise_assignment(row)
    if assignment:
        return assignment
    return {
        "assignment_id": None,
        "issue_id": issue_id,
        "company_id": company_id,
        "status": status,
        "notes": notes,
        "updated_by": updated_by,
        "created_at_utc": None,
        "updated_at_utc": None,
    }


async def update_assignment_status(
    assignment_id: int,
    *,
    status: str,
    updated_by: int | None = None,
) -> AssignmentRecord:
    await db.execute(
        """
        UPDATE issue_company_statuses
        SET status = %s,
            updated_by = %s,
            updated_at_utc = CURRENT_TIMESTAMP(6)
        WHERE id = %s
        """,
        (status, updated_by, assignment_id),
    )
    row = await db.fetch_one(
        """
        SELECT id AS assignment_id, issue_id, company_id, status, notes, updated_by, created_at_utc, updated_at_utc
        FROM issue_company_statuses
        WHERE id = %s
        """,
        (assignment_id,),
    )
    assignment = _normalise_assignment(row)
    if not assignment:
        raise ValueError("Assignment not found")
    return assignment


async def delete_assignment(assignment_id: int) -> None:
    await db.execute("DELETE FROM issue_company_statuses WHERE id = %s", (assignment_id,))


async def list_assignments_for_issue(issue_id: int) -> list[AssignmentRecord]:
    rows = await db.fetch_all(
        """
        SELECT id AS assignment_id, issue_id, company_id, status, notes, updated_by, created_at_utc, updated_at_utc
        FROM issue_company_statuses
        WHERE issue_id = %s
        ORDER BY updated_at_utc DESC
        """,
        (issue_id,),
    )
    return [assignment for assignment in (_normalise_assignment(row) for row in rows) if assignment]


async def count_assets_by_issue_slug(*, issue_slug: str, company_id: int | None = None) -> int:
    """Count assets that have the specified issue assigned to their company.
    
    Args:
        issue_slug: The slug of the issue to count assets for
        company_id: Optional company ID to filter by
        
    Returns:
        Count of assets with the issue
    """
    query = """
        SELECT COUNT(DISTINCT a.id) as count
        FROM assets a
        JOIN issue_company_statuses ics ON ics.company_id = a.company_id
        JOIN issue_definitions i ON i.id = ics.issue_id
        WHERE LOWER(i.slug) = LOWER(%s)
    """
    params = [issue_slug]
    
    if company_id is not None:
        query += " AND a.company_id = %s"
        params.append(company_id)
    
    row = await db.fetch_one(query, tuple(params))
    if not row:
        return 0
    
    return int(row.get("count", 0))


async def list_assets_by_issue_slug(*, issue_slug: str, company_id: int | None = None) -> list[str]:
    """List asset names that have the specified issue assigned to their company.
    
    Args:
        issue_slug: The slug of the issue to list assets for
        company_id: Optional company ID to filter by
        
    Returns:
        List of asset names with the issue
    """
    query = """
        SELECT DISTINCT a.name
        FROM assets a
        JOIN issue_company_statuses ics ON ics.company_id = a.company_id
        JOIN issue_definitions i ON i.id = ics.issue_id
        WHERE LOWER(i.slug) = LOWER(%s)
    """
    params = [issue_slug]
    
    if company_id is not None:
        query += " AND a.company_id = %s"
        params.append(company_id)
    
    query += " ORDER BY a.name ASC"
    
    rows = await db.fetch_all(query, tuple(params))
    if not rows:
        return []
    
    return [row.get("name") for row in rows if row.get("name")]
