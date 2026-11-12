from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_rules(company_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        """SELECT a.*, r.name AS role_name FROM company_job_title_access AS a
        INNER JOIN roles AS r ON r.id = a.role_id
        WHERE a.company_id = ? ORDER BY a.job_title""", (company_id,),
    )


async def save_rule(*, company_id: int, job_title: str, role_id: int, staff_permission: int) -> None:
    query = (
        "INSERT INTO company_job_title_access (company_id, job_title, role_id, staff_permission) "
        "VALUES (?, ?, ?, ?) "
    )
    if db.is_sqlite():
        query += "ON CONFLICT(company_id, job_title) DO UPDATE SET role_id = excluded.role_id, staff_permission = excluded.staff_permission"
    else:
        query += "ON DUPLICATE KEY UPDATE role_id = VALUES(role_id), staff_permission = VALUES(staff_permission)"
    await db.execute(query, (company_id, job_title.strip().lower(), role_id, staff_permission))


async def delete_rule(company_id: int, job_title: str) -> None:
    await db.execute(
        "DELETE FROM company_job_title_access WHERE company_id = ? AND job_title = ?",
        (company_id, job_title.strip().lower()),
    )
