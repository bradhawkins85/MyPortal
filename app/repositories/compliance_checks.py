from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.core.database import db
from app.schemas.compliance_checks import CheckStatus

_DUE_SOON_DAYS = 14


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(value: Any) -> Optional[str]:
    """Format a datetime/date value as an ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return value  # type: ignore[return-value]


def _is_overdue(next_review_at: Any) -> bool:
    if next_review_at is None:
        return False
    if isinstance(next_review_at, datetime):
        dt = next_review_at if next_review_at.tzinfo else next_review_at.replace(tzinfo=timezone.utc)
        return dt < _now_utc()
    return False


def _is_due_soon(next_review_at: Any) -> bool:
    if next_review_at is None:
        return False
    if isinstance(next_review_at, datetime):
        dt = next_review_at if next_review_at.tzinfo else next_review_at.replace(tzinfo=timezone.utc)
        now = _now_utc()
        return now <= dt < now + timedelta(days=_DUE_SOON_DAYS)
    return False


def _build_assignment(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["created_at"] = _fmt(item.get("created_at"))
    item["updated_at"] = _fmt(item.get("updated_at"))
    item["last_checked_at"] = _fmt(item.get("last_checked_at"))
    item["next_review_at"] = _fmt(item.get("next_review_at"))
    item["is_overdue"] = _is_overdue(row.get("next_review_at"))
    item["is_due_soon"] = _is_due_soon(row.get("next_review_at"))

    check_id = item.get("check_id")
    if check_id and "check_title" in item:
        item["check"] = {
            "id": check_id,
            "category_id": item.pop("check_category_id", None),
            "code": item.pop("check_code", None),
            "title": item.pop("check_title", None),
            "description": item.pop("check_description", None),
            "guidance": item.pop("check_guidance", None),
            "default_review_interval_days": item.pop("check_interval", None),
            "default_evidence_required": bool(item.pop("check_evidence_required", False)),
            "is_predefined": bool(item.pop("check_is_predefined", False)),
            "is_active": bool(item.pop("check_is_active", True)),
            "sort_order": item.pop("check_sort_order", 0),
            "created_by": item.pop("check_created_by", None),
            "created_at": None,
            "updated_at": None,
            "category": {
                "id": item.pop("category_id", None),
                "code": item.pop("category_code", None),
                "name": item.pop("category_name", None),
                "description": item.pop("category_description", None),
                "is_system": bool(item.pop("category_is_system", False)),
            },
        }
    return item


def _compute_next_review(last_checked_at: datetime, interval_days: int) -> datetime:
    return last_checked_at + timedelta(days=interval_days)


# =============================================================================
# Categories
# =============================================================================


async def list_categories() -> list[dict[str, Any]]:
    query = """
        SELECT id, code, name, description, is_system, created_at, updated_at
        FROM compliance_check_categories
        ORDER BY name
    """
    rows = await db.fetch_all(query)
    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _fmt(item.get("created_at"))
        item["updated_at"] = _fmt(item.get("updated_at"))
        result.append(item)
    return result


async def get_category(category_id: int) -> Optional[dict[str, Any]]:
    query = """
        SELECT id, code, name, description, is_system, created_at, updated_at
        FROM compliance_check_categories
        WHERE id = %(id)s
    """
    row = await db.fetch_one(query, {"id": category_id})
    if not row:
        return None
    item = dict(row)
    item["created_at"] = _fmt(item.get("created_at"))
    item["updated_at"] = _fmt(item.get("updated_at"))
    return item


async def create_category(*, code: str, name: str, description: Optional[str] = None, is_system: bool = False) -> dict[str, Any]:
    query = """
        INSERT INTO compliance_check_categories (code, name, description, is_system)
        VALUES (%(code)s, %(name)s, %(description)s, %(is_system)s)
    """
    new_id = await db.execute(query, {"code": code, "name": name, "description": description, "is_system": int(is_system)})
    result = await get_category(new_id)
    if not result:
        raise RuntimeError("Failed to retrieve newly created category")
    return result


async def update_category(category_id: int, **kwargs: Any) -> Optional[dict[str, Any]]:
    allowed = {"name", "description"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return await get_category(category_id)
    columns = ", ".join(f"{k} = %({k})s" for k in updates)
    params = {**updates, "id": category_id}
    await db.execute(f"UPDATE compliance_check_categories SET {columns} WHERE id = %(id)s", params)
    return await get_category(category_id)


async def delete_category(category_id: int) -> None:
    await db.execute("DELETE FROM compliance_check_categories WHERE id = %(id)s AND is_system = 0", {"id": category_id})


# =============================================================================
# Compliance checks (library)
# =============================================================================


async def list_checks(
    *,
    category_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    clauses = []
    if category_id is not None:
        clauses.append("cc.category_id = %(category_id)s")
        params["category_id"] = category_id
    if is_active is not None:
        clauses.append("cc.is_active = %(is_active)s")
        params["is_active"] = int(is_active)
    if search:
        clauses.append("(cc.title LIKE %(search)s OR cc.code LIKE %(search)s)")
        params["search"] = f"%{search}%"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    query = f"""
        SELECT
            cc.id, cc.category_id, cc.code, cc.title, cc.description, cc.guidance,
            cc.default_review_interval_days, cc.default_evidence_required,
            cc.is_predefined, cc.is_active, cc.sort_order, cc.created_by,
            cc.created_at, cc.updated_at,
            cat.code AS category_code, cat.name AS category_name,
            cat.description AS category_description, cat.is_system AS category_is_system
        FROM compliance_checks cc
        INNER JOIN compliance_check_categories cat ON cat.id = cc.category_id
        {where}
        ORDER BY cat.name, cc.sort_order, cc.title
    """
    rows = await db.fetch_all(query, params)
    result = []
    for row in rows:
        item = dict(row)
        item["created_at"] = _fmt(item.get("created_at"))
        item["updated_at"] = _fmt(item.get("updated_at"))
        item["category"] = {
            "id": item["category_id"],
            "code": item.pop("category_code"),
            "name": item.pop("category_name"),
            "description": item.pop("category_description"),
            "is_system": bool(item.pop("category_is_system")),
        }
        result.append(item)
    return result


async def get_check(check_id: int) -> Optional[dict[str, Any]]:
    query = """
        SELECT
            cc.id, cc.category_id, cc.code, cc.title, cc.description, cc.guidance,
            cc.default_review_interval_days, cc.default_evidence_required,
            cc.is_predefined, cc.is_active, cc.sort_order, cc.created_by,
            cc.created_at, cc.updated_at,
            cat.code AS category_code, cat.name AS category_name,
            cat.description AS category_description, cat.is_system AS category_is_system
        FROM compliance_checks cc
        INNER JOIN compliance_check_categories cat ON cat.id = cc.category_id
        WHERE cc.id = %(id)s
    """
    row = await db.fetch_one(query, {"id": check_id})
    if not row:
        return None
    item = dict(row)
    item["created_at"] = _fmt(item.get("created_at"))
    item["updated_at"] = _fmt(item.get("updated_at"))
    item["category"] = {
        "id": item["category_id"],
        "code": item.pop("category_code"),
        "name": item.pop("category_name"),
        "description": item.pop("category_description"),
        "is_system": bool(item.pop("category_is_system")),
    }
    return item


async def create_check(
    *,
    category_id: int,
    code: str,
    title: str,
    description: Optional[str] = None,
    guidance: Optional[str] = None,
    default_review_interval_days: int = 365,
    default_evidence_required: bool = False,
    is_predefined: bool = False,
    is_active: bool = True,
    sort_order: int = 0,
    created_by: Optional[int] = None,
) -> dict[str, Any]:
    query = """
        INSERT INTO compliance_checks
          (category_id, code, title, description, guidance,
           default_review_interval_days, default_evidence_required,
           is_predefined, is_active, sort_order, created_by)
        VALUES
          (%(category_id)s, %(code)s, %(title)s, %(description)s, %(guidance)s,
           %(interval)s, %(evidence_required)s,
           %(is_predefined)s, %(is_active)s, %(sort_order)s, %(created_by)s)
    """
    new_id = await db.execute(query, {
        "category_id": category_id,
        "code": code,
        "title": title,
        "description": description,
        "guidance": guidance,
        "interval": default_review_interval_days,
        "evidence_required": int(default_evidence_required),
        "is_predefined": int(is_predefined),
        "is_active": int(is_active),
        "sort_order": sort_order,
        "created_by": created_by,
    })
    result = await get_check(new_id)
    if not result:
        raise RuntimeError("Failed to retrieve newly created check")
    return result


async def update_check(check_id: int, **kwargs: Any) -> Optional[dict[str, Any]]:
    allowed = {"title", "description", "guidance", "default_review_interval_days",
               "default_evidence_required", "is_active", "sort_order"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return await get_check(check_id)
    columns = ", ".join(f"{k} = %({k})s" for k in updates)
    params = {**updates, "id": check_id}
    await db.execute(f"UPDATE compliance_checks SET {columns} WHERE id = %(id)s", params)
    return await get_check(check_id)


async def deactivate_check(check_id: int) -> None:
    await db.execute("UPDATE compliance_checks SET is_active = 0 WHERE id = %(id)s", {"id": check_id})


# =============================================================================
# Assignments
# =============================================================================


_ASSIGNMENT_SELECT = """
    SELECT
        a.id, a.company_id, a.check_id, a.status,
        a.review_interval_days, a.last_checked_at, a.last_checked_by,
        a.next_review_at, a.notes, a.evidence_summary, a.owner_user_id,
        a.archived, a.created_at, a.updated_at,
        cc.category_id,
        cc.code AS check_code, cc.title AS check_title,
        cc.description AS check_description, cc.guidance AS check_guidance,
        cc.default_review_interval_days AS check_interval,
        cc.default_evidence_required AS check_evidence_required,
        cc.is_predefined AS check_is_predefined, cc.is_active AS check_is_active,
        cc.sort_order AS check_sort_order, cc.created_by AS check_created_by,
        cat.code AS category_code, cat.name AS category_name,
        cat.description AS category_description, cat.is_system AS category_is_system
    FROM company_compliance_check_assignments a
    INNER JOIN compliance_checks cc ON cc.id = a.check_id
    INNER JOIN compliance_check_categories cat ON cat.id = cc.category_id
"""


async def list_assignments(
    company_id: int,
    *,
    status: Optional[CheckStatus] = None,
    category_id: Optional[int] = None,
    overdue_only: bool = False,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"company_id": company_id}
    clauses = ["a.company_id = %(company_id)s"]
    if not include_archived:
        clauses.append("a.archived = 0")
    if status:
        clauses.append("a.status = %(status)s")
        params["status"] = status.value
    if category_id is not None:
        clauses.append("cc.category_id = %(category_id)s")
        params["category_id"] = category_id
    if overdue_only:
        clauses.append("a.next_review_at IS NOT NULL AND a.next_review_at < %(now)s")
        params["now"] = _now_utc()
    where = "WHERE " + " AND ".join(clauses)
    query = f"{_ASSIGNMENT_SELECT} {where} ORDER BY cc.category_id, cc.sort_order, cc.title"
    rows = await db.fetch_all(query, params)
    return [_build_assignment(row) for row in rows]


async def get_assignment(company_id: int, assignment_id: int) -> Optional[dict[str, Any]]:
    query = f"{_ASSIGNMENT_SELECT} WHERE a.id = %(id)s AND a.company_id = %(company_id)s"
    row = await db.fetch_one(query, {"id": assignment_id, "company_id": company_id})
    return _build_assignment(row) if row else None


async def get_assignment_by_check(
    company_id: int, check_id: int, *, include_archived: bool = False
) -> Optional[dict[str, Any]]:
    archived_filter = "" if include_archived else " AND a.archived = 0"
    query = (
        f"{_ASSIGNMENT_SELECT} WHERE a.company_id = %(company_id)s"
        f" AND a.check_id = %(check_id)s{archived_filter}"
    )
    row = await db.fetch_one(query, {"company_id": company_id, "check_id": check_id})
    return _build_assignment(row) if row else None


async def create_assignment(
    *,
    company_id: int,
    check_id: int,
    status: CheckStatus = CheckStatus.NOT_STARTED,
    review_interval_days: Optional[int] = None,
    notes: Optional[str] = None,
    owner_user_id: Optional[int] = None,
) -> dict[str, Any]:
    # If an archived assignment already exists for this company+check, unarchive it
    # instead of trying a duplicate INSERT (which would violate the unique key).
    archived = await get_assignment_by_check(company_id, check_id, include_archived=True)
    if archived and archived.get("archived"):
        updates: dict[str, Any] = {
            "archived": 0,
            "status": status.value,
        }
        if review_interval_days is not None:
            updates["review_interval_days"] = review_interval_days
        if notes is not None:
            updates["notes"] = notes
        if owner_user_id is not None:
            updates["owner_user_id"] = owner_user_id
        columns = ", ".join(f"{k} = %({k})s" for k in updates)
        params: dict[str, Any] = {**updates, "id": archived["id"], "company_id": company_id}
        await db.execute(
            f"UPDATE company_compliance_check_assignments SET {columns}"
            f" WHERE id = %(id)s AND company_id = %(company_id)s",
            params,
        )
        result = await get_assignment(company_id, archived["id"])
        if not result:
            raise RuntimeError("Failed to retrieve reactivated assignment")
        return result

    query = """
        INSERT INTO company_compliance_check_assignments
          (company_id, check_id, status, review_interval_days, notes, owner_user_id)
        VALUES
          (%(company_id)s, %(check_id)s, %(status)s, %(interval)s, %(notes)s, %(owner)s)
    """
    new_id = await db.execute_returning_lastrowid(query, {
        "company_id": company_id,
        "check_id": check_id,
        "status": status.value,
        "interval": review_interval_days,
        "notes": notes,
        "owner": owner_user_id,
    })
    result = await get_assignment(company_id, new_id)
    if not result:
        raise RuntimeError("Failed to retrieve newly created assignment")
    return result


async def bulk_assign_by_category(company_id: int, category_id: int) -> int:
    """Assign all active checks in a category to a company (skip already-assigned)."""
    checks = await list_checks(category_id=category_id, is_active=True)
    created = 0
    for check in checks:
        existing = await get_assignment_by_check(company_id, check["id"], include_archived=True)
        if existing:
            continue
        await create_assignment(company_id=company_id, check_id=check["id"])
        created += 1
    return created


async def update_assignment(
    company_id: int,
    assignment_id: int,
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> Optional[dict[str, Any]]:
    """Update an assignment.  If status or last_checked_at changes, recompute next_review_at."""
    existing = await get_assignment(company_id, assignment_id)
    if not existing:
        return None

    allowed = {"status", "review_interval_days", "last_checked_at", "notes",
               "evidence_summary", "owner_user_id", "archived"}
    updates: dict[str, Any] = {k: v for k, v in kwargs.items() if k in allowed and v is not None}

    old_status = existing.get("status")
    old_last_checked = existing.get("last_checked_at")

    # If status or last_checked_at is being updated, stamp last_checked_at and recompute next_review
    status_changing = "status" in updates and updates["status"] != old_status
    checked_at_provided = "last_checked_at" in updates

    if status_changing or checked_at_provided:
        new_checked_at: datetime
        if checked_at_provided and updates["last_checked_at"] is not None:
            raw = updates["last_checked_at"]
            if isinstance(raw, datetime):
                new_checked_at = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            else:
                new_checked_at = _now_utc()
        else:
            new_checked_at = _now_utc()

        updates["last_checked_at"] = new_checked_at
        updates["last_checked_by"] = user_id

        # Determine effective interval
        interval = updates.get("review_interval_days") or existing.get("review_interval_days")
        if interval is None:
            check_data = await get_check(existing["check_id"])
            interval = (check_data or {}).get("default_review_interval_days", 365)
        updates["next_review_at"] = _compute_next_review(new_checked_at, int(interval))

    if not updates:
        return existing

    columns = ", ".join(f"{k} = %({k})s" for k in updates)
    params = {**updates, "id": assignment_id, "company_id": company_id}
    await db.execute(
        f"UPDATE company_compliance_check_assignments SET {columns} WHERE id = %(id)s AND company_id = %(company_id)s",
        params,
    )

    # Write audit record if status changed
    if status_changing:
        await append_audit(
            assignment_id=assignment_id,
            company_id=company_id,
            user_id=user_id,
            action="status_update",
            old_status=old_status,
            new_status=updates.get("status"),
            old_last_checked_at=old_last_checked,
            new_last_checked_at=_fmt(updates.get("last_checked_at")),
            change_summary=f"Status changed from {old_status} to {updates.get('status')}",
        )

    return await get_assignment(company_id, assignment_id)


async def delete_assignment(company_id: int, assignment_id: int) -> None:
    await db.execute(
        "DELETE FROM company_compliance_check_assignments WHERE id = %(id)s AND company_id = %(company_id)s",
        {"id": assignment_id, "company_id": company_id},
    )


async def get_assignment_summary(company_id: int) -> dict[str, Any]:
    now = _now_utc()
    due_soon_threshold = now + timedelta(days=_DUE_SOON_DAYS)
    query = """
        SELECT
            COUNT(*) AS total,
            SUM(status = 'not_started') AS not_started,
            SUM(status = 'in_progress') AS in_progress,
            SUM(status = 'compliant') AS compliant,
            SUM(status = 'non_compliant') AS non_compliant,
            SUM(status = 'not_applicable') AS not_applicable,
            SUM(next_review_at IS NOT NULL AND next_review_at < %(now)s) AS overdue_count,
            SUM(next_review_at IS NOT NULL AND next_review_at >= %(now)s AND next_review_at < %(due_soon)s) AS due_soon_count
        FROM company_compliance_check_assignments
        WHERE company_id = %(company_id)s AND archived = 0
    """
    row = await db.fetch_one(query, {"company_id": company_id, "now": now, "due_soon": due_soon_threshold})
    data: dict[str, Any] = dict(row) if row else {}
    total = int(data.get("total") or 0)
    compliant = int(data.get("compliant") or 0)
    pct = round(compliant / total * 100, 1) if total > 0 else 0.0
    return {
        "company_id": company_id,
        "total": total,
        "not_started": int(data.get("not_started") or 0),
        "in_progress": int(data.get("in_progress") or 0),
        "compliant": compliant,
        "non_compliant": int(data.get("non_compliant") or 0),
        "not_applicable": int(data.get("not_applicable") or 0),
        "overdue_count": int(data.get("overdue_count") or 0),
        "due_soon_count": int(data.get("due_soon_count") or 0),
        "compliance_percentage": pct,
    }


# =============================================================================
# Evidence
# =============================================================================


async def list_evidence(assignment_id: int) -> list[dict[str, Any]]:
    query = """
        SELECT id, assignment_id, evidence_type, title, content, file_path, uploaded_by, uploaded_at
        FROM company_compliance_check_evidence
        WHERE assignment_id = %(assignment_id)s
        ORDER BY uploaded_at DESC
    """
    rows = await db.fetch_all(query, {"assignment_id": assignment_id})
    return [dict(r) for r in rows]


async def add_evidence(
    *,
    assignment_id: int,
    evidence_type: str,
    title: str,
    content: Optional[str] = None,
    file_path: Optional[str] = None,
    uploaded_by: Optional[int] = None,
) -> dict[str, Any]:
    query = """
        INSERT INTO company_compliance_check_evidence
          (assignment_id, evidence_type, title, content, file_path, uploaded_by)
        VALUES
          (%(assignment_id)s, %(evidence_type)s, %(title)s, %(content)s, %(file_path)s, %(uploaded_by)s)
    """
    new_id = await db.execute(query, {
        "assignment_id": assignment_id,
        "evidence_type": evidence_type,
        "title": title,
        "content": content,
        "file_path": file_path,
        "uploaded_by": uploaded_by,
    })
    row = await db.fetch_one(
        "SELECT id, assignment_id, evidence_type, title, content, file_path, uploaded_by, uploaded_at "
        "FROM company_compliance_check_evidence WHERE id = %(id)s",
        {"id": new_id},
    )
    return dict(row) if row else {}


async def delete_evidence(assignment_id: int, evidence_id: int) -> None:
    await db.execute(
        "DELETE FROM company_compliance_check_evidence WHERE id = %(id)s AND assignment_id = %(assignment_id)s",
        {"id": evidence_id, "assignment_id": assignment_id},
    )


# =============================================================================
# Audit
# =============================================================================


async def append_audit(
    *,
    assignment_id: int,
    company_id: int,
    user_id: Optional[int],
    action: str,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    old_last_checked_at: Any = None,
    new_last_checked_at: Any = None,
    change_summary: Optional[str] = None,
) -> None:
    query = """
        INSERT INTO company_compliance_check_audit
          (assignment_id, company_id, user_id, action,
           old_status, new_status,
           old_last_checked_at, new_last_checked_at, change_summary)
        VALUES
          (%(assignment_id)s, %(company_id)s, %(user_id)s, %(action)s,
           %(old_status)s, %(new_status)s,
           %(old_checked)s, %(new_checked)s, %(summary)s)
    """
    await db.execute(query, {
        "assignment_id": assignment_id,
        "company_id": company_id,
        "user_id": user_id,
        "action": action,
        "old_status": old_status,
        "new_status": new_status,
        "old_checked": _fmt(old_last_checked_at),
        "new_checked": _fmt(new_last_checked_at),
        "summary": change_summary,
    })


async def list_audit(assignment_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    query = """
        SELECT id, assignment_id, company_id, user_id, action,
               old_status, new_status, old_last_checked_at, new_last_checked_at,
               change_summary, created_at
        FROM company_compliance_check_audit
        WHERE assignment_id = %(assignment_id)s
        ORDER BY created_at DESC
        LIMIT %(limit)s
    """
    rows = await db.fetch_all(query, {"assignment_id": assignment_id, "limit": limit})
    return [dict(r) for r in rows]
