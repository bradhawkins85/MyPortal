from __future__ import annotations

from typing import Any, Sequence

from app.core.database import db


async def get_for_company(company_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM service_level_agreements WHERE company_id = %s LIMIT 1",
        (company_id,),
    )


async def upsert_for_company(
    company_id: int, *, name: str, response_minutes: int, resolution_minutes: int, enabled: bool
) -> None:
    existing = await get_for_company(company_id)
    if existing:
        await db.execute(
            """UPDATE service_level_agreements SET name=%s, response_minutes=%s,
               resolution_minutes=%s, enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE company_id=%s""",
            (name, response_minutes, resolution_minutes, enabled, company_id),
        )
    else:
        await db.execute(
            """INSERT INTO service_level_agreements
               (company_id,name,response_minutes,resolution_minutes,enabled) VALUES (%s,%s,%s,%s,%s)""",
            (company_id, name, response_minutes, resolution_minutes, enabled),
        )


async def delete_for_company(company_id: int) -> None:
    await db.execute("DELETE FROM service_level_agreements WHERE company_id=%s", (company_id,))


async def list_ticket_sla_source(ticket_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not ticket_ids:
        return []
    placeholders = ",".join("%s" for _ in ticket_ids)
    return await db.fetch_all(
        f"""SELECT t.id, t.company_id, t.created_at, t.closed_at, t.status,
                   s.id AS sla_id, s.name AS sla_name, s.response_minutes, s.resolution_minutes,
                   MIN(CASE WHEN tr.is_internal=0 THEN tr.created_at END) AS first_response_at
            FROM tickets t
            LEFT JOIN service_level_agreements s ON s.company_id=t.company_id AND s.enabled=1
            LEFT JOIN ticket_replies tr ON tr.ticket_id=t.id
            WHERE t.id IN ({placeholders})
            GROUP BY t.id,t.company_id,t.created_at,t.closed_at,t.status,s.id,s.name,s.response_minutes,s.resolution_minutes""",
        tuple(ticket_ids),
    )


async def list_active_ticket_ids() -> list[int]:
    rows = await db.fetch_all(
        """SELECT t.id FROM tickets t JOIN service_level_agreements s
           ON s.company_id=t.company_id AND s.enabled=1
           WHERE LOWER(COALESCE(t.status,'')) NOT IN ('closed','resolved')""",
        (),
    )
    return [int(row["id"]) for row in rows]


async def claim_event(ticket_id: int, event_type: str) -> bool:
    if db.is_sqlite():
        sql = "INSERT OR IGNORE INTO ticket_sla_events (ticket_id,event_type) VALUES (%s,%s)"
    else:
        sql = "INSERT IGNORE INTO ticket_sla_events (ticket_id,event_type) VALUES (%s,%s)"
    return await db.execute_rowcount(sql, (ticket_id, event_type)) > 0
