from __future__ import annotations

from typing import Any, Sequence

from app.core.database import db


async def get_for_company(company_id: int) -> dict[str, Any] | None:
    assignment = await db.fetch_one(
        """SELECT st.* FROM company_sla_templates cst
           JOIN sla_templates st ON st.id=cst.template_id
           WHERE cst.company_id=%s LIMIT 1""",
        (company_id,),
    )
    if assignment:
        assignment["targets"] = await list_targets(int(assignment["id"]))
        assignment["pause_statuses"] = await list_pause_statuses(int(assignment["id"]))
    return assignment


async def list_templates() -> list[dict[str, Any]]:
    templates = await db.fetch_all("SELECT * FROM sla_templates ORDER BY name, id", ())
    for template in templates:
        template["targets"] = await list_targets(int(template["id"]))
        template["pause_statuses"] = await list_pause_statuses(int(template["id"]))
    return templates


async def list_targets(template_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM sla_template_targets WHERE template_id=%s ORDER BY resolution_minutes, priority",
        (template_id,),
    )


async def list_pause_statuses(template_id: int) -> list[str]:
    rows = await db.fetch_all(
        "SELECT status FROM sla_template_pause_statuses WHERE template_id=%s ORDER BY status",
        (template_id,),
    )
    return [str(row["status"]) for row in rows]


async def create_template(*, name: str, description: str, enabled: bool,
                          targets: list[tuple[str, int, int]], pause_statuses: list[str]) -> None:
    template_id = await db.execute_returning_lastrowid(
        "INSERT INTO sla_templates (name,description,enabled) VALUES (%s,%s,%s)",
        (name, description or None, enabled),
    )
    for priority, response, resolution in targets:
        await db.execute(
            """INSERT INTO sla_template_targets
               (template_id,priority,response_minutes,resolution_minutes) VALUES (%s,%s,%s,%s)""",
            (template_id, priority, response, resolution),
        )
    for status in pause_statuses:
        await db.execute(
            "INSERT INTO sla_template_pause_statuses (template_id,status) VALUES (%s,%s)",
            (template_id, status),
        )


async def assign_to_company(company_id: int, template_id: int) -> None:
    await db.execute("DELETE FROM company_sla_templates WHERE company_id=%s", (company_id,))
    await db.execute(
        "INSERT INTO company_sla_templates (company_id,template_id) VALUES (%s,%s)",
        (company_id, template_id),
    )


async def delete_for_company(company_id: int) -> None:
    await db.execute("DELETE FROM company_sla_templates WHERE company_id=%s", (company_id,))


async def list_ticket_sla_source(ticket_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not ticket_ids:
        return []
    placeholders = ",".join("%s" for _ in ticket_ids)
    return await db.fetch_all(
        f"""SELECT t.id, t.company_id, t.created_at, t.closed_at, t.status,
                   CASE WHEN target.id IS NOT NULL THEN s.id END AS sla_id,
                   s.name AS sla_name, target.response_minutes, target.resolution_minutes,
                   pause.status AS sla_pause_status,
                   MIN(CASE WHEN tr.is_internal=0 THEN tr.created_at END) AS first_response_at
            FROM tickets t
            LEFT JOIN company_sla_templates cst ON cst.company_id=t.company_id
            LEFT JOIN sla_templates s ON s.id=cst.template_id AND s.enabled=1
            LEFT JOIN sla_template_targets target
              ON target.template_id=s.id AND LOWER(target.priority)=LOWER(COALESCE(t.priority,'normal'))
            LEFT JOIN sla_template_pause_statuses pause
              ON pause.template_id=s.id AND LOWER(pause.status)=LOWER(COALESCE(t.status,''))
            LEFT JOIN ticket_replies tr ON tr.ticket_id=t.id
            WHERE t.id IN ({placeholders})
            GROUP BY t.id,t.company_id,t.created_at,t.closed_at,t.status,s.id,s.name,target.response_minutes,target.resolution_minutes,pause.status""",
        tuple(ticket_ids),
    )


async def list_active_ticket_ids() -> list[int]:
    rows = await db.fetch_all(
        """SELECT t.id FROM tickets t JOIN company_sla_templates cst ON cst.company_id=t.company_id
           JOIN sla_templates s ON s.id=cst.template_id AND s.enabled=1
           JOIN sla_template_targets target ON target.template_id=s.id
             AND LOWER(target.priority)=LOWER(COALESCE(t.priority,'normal'))
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


async def list_pause_periods(ticket_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not ticket_ids:
        return []
    placeholders = ",".join("%s" for _ in ticket_ids)
    return await db.fetch_all(
        f"""SELECT h.ticket_id,h.status,h.started_at,h.ended_at
            FROM ticket_status_history h
            JOIN tickets t ON t.id=h.ticket_id
            JOIN company_sla_templates cst ON cst.company_id=t.company_id
            JOIN sla_template_pause_statuses pause
              ON pause.template_id=cst.template_id AND LOWER(pause.status)=LOWER(h.status)
            WHERE h.ticket_id IN ({placeholders})
            UNION ALL
            SELECT t.id AS ticket_id,t.status,COALESCE(t.status_changed_at,t.created_at) AS started_at,
                   t.closed_at AS ended_at
            FROM tickets t
            JOIN company_sla_templates cst ON cst.company_id=t.company_id
            JOIN sla_template_pause_statuses pause
              ON pause.template_id=cst.template_id AND LOWER(pause.status)=LOWER(t.status)
            WHERE t.id IN ({placeholders})""",
        (*ticket_ids, *ticket_ids),
    )
