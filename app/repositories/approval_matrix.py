from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.core.database import db


async def list_configurations(
    company_id: int,
    *,
    workflow_type: str = "change",
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    active_clause = "" if include_inactive else " AND ac.is_active = 1"
    rows = await db.fetch_all(
        """
        SELECT ac.*
        FROM approval_configurations ac
        WHERE ac.company_id = %s AND ac.workflow_type = %s""" + active_clause + """
        ORDER BY ac.name ASC, ac.id ASC
        """,
        (company_id, workflow_type),
    )
    return [dict(row) for row in rows]


async def get_configuration(configuration_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM approval_configurations WHERE id = %s", (configuration_id,))
    if not row:
        return None
    result = dict(row)
    result["contacts"] = [dict(item) for item in await db.fetch_all(
        """
        SELECT s.id AS staff_id, s.first_name, s.last_name, s.email
        FROM approval_configuration_contacts acc
        JOIN staff s ON s.id = acc.staff_id
        WHERE acc.configuration_id = %s
        ORDER BY acc.sort_order ASC, s.first_name ASC, s.last_name ASC
        """,
        (configuration_id,),
    )]
    result["technicians"] = [dict(item) for item in await db.fetch_all(
        """SELECT u.id AS user_id, u.first_name, u.last_name, u.email
           FROM approval_configuration_technicians act
           JOIN users u ON u.id = act.user_id
           WHERE act.configuration_id = %s ORDER BY act.sort_order, u.email""",
        (configuration_id,),
    )]
    result["technical_roles"] = [dict(item) for item in await db.fetch_all(
        """SELECT r.id AS role_id, r.name
           FROM approval_configuration_technical_roles acr
           JOIN roles r ON r.id = acr.role_id
           WHERE acr.configuration_id = %s ORDER BY r.name""",
        (configuration_id,),
    )]
    result["company_roles"] = [item["role_key"] for item in await db.fetch_all(
        "SELECT role_key FROM approval_configuration_company_roles WHERE configuration_id = %s ORDER BY role_key",
        (configuration_id,),
    )]
    result["job_titles"] = [item["job_title"] for item in await db.fetch_all(
        "SELECT job_title FROM approval_configuration_job_titles WHERE configuration_id = %s ORDER BY job_title",
        (configuration_id,),
    )]
    return result


async def get_configuration_by_guid(approval_guid: str) -> dict[str, Any] | None:
    """Return an approval configuration identified by its public GUID."""
    try:
        canonical_guid = str(UUID(str(approval_guid).strip()))
    except (AttributeError, TypeError, ValueError):
        return None
    row = await db.fetch_one(
        "SELECT id FROM approval_configurations WHERE guid = %s",
        (canonical_guid,),
    )
    return await get_configuration(int(row["id"])) if row else None


async def create_configuration(*, company_id: int, name: str, technician_user_id: int,
                               contact_staff_ids: list[int], description: str | None = None,
                               workflow_type: str = "change",
                               technician_user_ids: list[int] | None = None,
                               technical_role_ids: list[int] | None = None,
                               company_roles: list[str] | None = None,
                               job_titles: list[str] | None = None) -> dict[str, Any]:
    configuration_id = await db.execute_returning_lastrowid(
        """INSERT INTO approval_configurations
           (guid, company_id, name, description, workflow_type, technician_user_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (str(uuid4()), company_id, name, description, workflow_type, technician_user_id),
    )
    for order, staff_id in enumerate(dict.fromkeys(contact_staff_ids)):
        await db.execute(
            "INSERT INTO approval_configuration_contacts (configuration_id, staff_id, sort_order) VALUES (%s, %s, %s)",
            (configuration_id, staff_id, order),
        )
    await _replace_selector_options(
        int(configuration_id), technician_user_ids or [],
        technical_role_ids or [], company_roles or [], job_titles or [],
    )
    return (await get_configuration(int(configuration_id))) or {"id": configuration_id}


async def configuration_name_exists(
    company_id: int,
    name: str,
    *,
    workflow_type: str = "change",
    exclude_configuration_id: int | None = None,
) -> bool:
    query = """SELECT id FROM approval_configurations
               WHERE company_id = %s AND workflow_type = %s AND LOWER(name) = LOWER(%s)"""
    params: list[Any] = [company_id, workflow_type, name]
    if exclude_configuration_id is not None:
        query += " AND id <> %s"
        params.append(exclude_configuration_id)
    return bool(await db.fetch_one(query, tuple(params)))


async def update_configuration(
    *,
    configuration_id: int,
    company_id: int,
    name: str,
    technician_user_id: int,
    contact_staff_ids: list[int],
    description: str | None = None,
    technician_user_ids: list[int] | None = None,
    technical_role_ids: list[int] | None = None,
    company_roles: list[str] | None = None,
    job_titles: list[str] | None = None,
) -> bool:
    owned = await db.fetch_one(
        "SELECT id FROM approval_configurations WHERE id = %s AND company_id = %s",
        (configuration_id, company_id),
    )
    if not owned:
        return False
    await db.execute(
        """UPDATE approval_configurations
           SET name = %s, description = %s, technician_user_id = %s
           WHERE id = %s AND company_id = %s""",
        (name, description, technician_user_id, configuration_id, company_id),
    )
    await db.execute(
        "DELETE FROM approval_configuration_contacts WHERE configuration_id = %s",
        (configuration_id,),
    )
    for order, staff_id in enumerate(dict.fromkeys(contact_staff_ids)):
        await db.execute(
            "INSERT INTO approval_configuration_contacts (configuration_id, staff_id, sort_order) VALUES (%s, %s, %s)",
            (configuration_id, staff_id, order),
        )
    await _replace_selector_options(
        configuration_id, technician_user_ids or [],
        technical_role_ids or [], company_roles or [], job_titles or [],
    )
    return True


async def _replace_selector_options(configuration_id: int, technician_user_ids: list[int],
                                    technical_role_ids: list[int], company_roles: list[str],
                                    job_titles: list[str]) -> None:
    """Replace normalized selectors after the caller has validated their scope."""
    table_names = (
        "approval_configuration_technicians", "approval_configuration_technical_roles",
        "approval_configuration_company_roles", "approval_configuration_job_titles",
    )
    for table in table_names:
        await db.execute(f"DELETE FROM {table} WHERE configuration_id = %s", (configuration_id,))  # nosec B608
    for order, user_id in enumerate(dict.fromkeys(technician_user_ids)):
        await db.execute(
            "INSERT INTO approval_configuration_technicians (configuration_id, user_id, sort_order) VALUES (%s, %s, %s)",
            (configuration_id, user_id, order),
        )
    for role_id in dict.fromkeys(technical_role_ids):
        await db.execute(
            "INSERT INTO approval_configuration_technical_roles (configuration_id, role_id) VALUES (%s, %s)",
            (configuration_id, role_id),
        )
    for role_key in dict.fromkeys(company_roles):
        await db.execute(
            "INSERT INTO approval_configuration_company_roles (configuration_id, role_key) VALUES (%s, %s)",
            (configuration_id, role_key),
        )
    for job_title in dict.fromkeys(job_titles):
        await db.execute(
            "INSERT INTO approval_configuration_job_titles (configuration_id, job_title) VALUES (%s, %s)",
            (configuration_id, job_title),
        )


async def set_configuration_active(configuration_id: int, company_id: int, is_active: bool) -> bool:
    owned = await db.fetch_one(
        "SELECT id FROM approval_configurations WHERE id = %s AND company_id = %s",
        (configuration_id, company_id),
    )
    if not owned:
        return False
    await db.execute(
        "UPDATE approval_configurations SET is_active = %s WHERE id = %s AND company_id = %s",
        (1 if is_active else 0, configuration_id, company_id),
    )
    return True


async def delete_configuration(configuration_id: int, company_id: int) -> bool:
    result = await db.execute(
        "DELETE FROM approval_configurations WHERE id = %s AND company_id = %s",
        (configuration_id, company_id),
    )
    return bool(result)


async def assign_to_ticket(*, ticket_id: int, configuration_id: int,
                           assigned_by_user_id: int | None = None) -> dict[str, Any]:
    configuration = await get_configuration(configuration_id)
    if not configuration:
        raise ValueError("Approval configuration not found")
    ticket = await db.fetch_one(
        "SELECT company_id FROM tickets WHERE id = %s",
        (ticket_id,),
    )
    if not ticket or int(ticket.get("company_id") or 0) != int(configuration["company_id"]):
        raise ValueError("Approval configuration must belong to the ticket company")
    existing = await db.fetch_one(
        "SELECT id FROM ticket_approval_workflows WHERE ticket_id = %s AND workflow_type = %s",
        (ticket_id, configuration["workflow_type"]),
    )
    if existing:
        await db.execute("DELETE FROM ticket_approval_workflows WHERE id = %s", (existing["id"],))
    workflow_id = await db.execute_returning_lastrowid(
        """INSERT INTO ticket_approval_workflows
           (ticket_id, configuration_id, configuration_name, workflow_type, assigned_by_user_id)
           VALUES (%s, %s, %s, %s, %s)""",
        (ticket_id, configuration_id, configuration["name"], configuration["workflow_type"], assigned_by_user_id),
    )
    return (await get_ticket_workflow(ticket_id)) or {}


async def assign_to_ticket_by_guid(*, ticket_id: int, approval_guid: str,
                                   assigned_by_user_id: int | None = None) -> dict[str, Any]:
    """Assign an approval by its stable public identifier."""
    configuration = await get_configuration_by_guid(approval_guid)
    if not configuration:
        raise ValueError("Approval configuration GUID not found")
    return await assign_to_ticket(
        ticket_id=ticket_id,
        configuration_id=int(configuration["id"]),
        assigned_by_user_id=assigned_by_user_id,
    )


async def get_ticket_workflow(ticket_id: int, *, workflow_type: str = "change") -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM ticket_approval_workflows WHERE ticket_id = %s AND workflow_type = %s",
        (ticket_id, workflow_type),
    )
    if not row:
        return None
    result = dict(row)
    decisions = await db.fetch_all(
        "SELECT * FROM ticket_approval_decisions WHERE workflow_id = %s ORDER BY sort_order ASC, id ASC",
        (row["id"],),
    )
    result["decisions"] = [dict(item) for item in decisions]
    result["pending_count"] = sum(1 for item in decisions if item.get("status") == "pending")
    return result


async def set_decision(*, ticket_id: int, decision_id: int, decision_status: str,
                       decided_by_user_id: int) -> dict[str, Any] | None:
    if decision_status not in {"approved", "denied"}:
        raise ValueError("Decision must be approved or denied")
    owned = await db.fetch_one(
        """SELECT d.id FROM ticket_approval_decisions d
           JOIN ticket_approval_workflows w ON w.id = d.workflow_id
           WHERE d.id = %s AND w.ticket_id = %s""",
        (decision_id, ticket_id),
    )
    if not owned:
        return None
    await db.execute(
        """UPDATE ticket_approval_decisions
           SET status = %s, decided_at = UTC_TIMESTAMP(6), decided_by_user_id = %s
           WHERE id = %s""",
        (decision_status, decided_by_user_id, decision_id),
    )
    return await get_ticket_workflow(ticket_id)
