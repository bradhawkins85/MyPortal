from __future__ import annotations

from typing import Any

from app.core.database import db

IMPORTANCE_LEVELS: tuple[tuple[str, str], ...] = (
    ("critical", "Critical"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
)
IMPORTANCE_KEYS = frozenset(key for key, _label in IMPORTANCE_LEVELS)

_SELECT_APPLICATION = """
    SELECT a.id, a.company_id, a.name, a.type_id, t.name AS type_name, a.version,
           a.business_impact, a.importance, a.champion_staff_id, a.champion_name,
           s.first_name AS champion_first_name, s.last_name AS champion_last_name,
           s.email AS champion_email,
           CASE WHEN a.product_key_encrypted IS NULL OR a.product_key_encrypted = ''
                THEN 0 ELSE 1 END AS has_product_key,
           a.notes, a.created_by, a.created_at, a.updated_at
    FROM applications a
    LEFT JOIN application_types t ON t.id = a.type_id AND t.company_id = a.company_id
    LEFT JOIN staff s ON s.id = a.champion_staff_id AND s.company_id = a.company_id
"""


def _with_champion(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    row = dict(row)
    staff_name = " ".join(
        part for part in (row.get("champion_first_name"), row.get("champion_last_name")) if part
    ).strip()
    row["champion_display"] = staff_name or (row.get("champion_name") or "")
    row["has_product_key"] = bool(row.get("has_product_key"))
    return row


async def list_applications(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        _SELECT_APPLICATION + " WHERE a.company_id = %s ORDER BY a.name, a.id", (company_id,)
    )
    return [_with_champion(row) for row in rows or []]


async def get_application(company_id: int, application_id: int) -> dict[str, Any] | None:
    return _with_champion(await db.fetch_one(
        _SELECT_APPLICATION + " WHERE a.company_id = %s AND a.id = %s",
        (company_id, application_id),
    ))


async def get_product_key_ciphertext(company_id: int, application_id: int) -> str | None:
    row = await db.fetch_one(
        "SELECT product_key_encrypted FROM applications WHERE company_id = %s AND id = %s",
        (company_id, application_id),
    )
    return (row or {}).get("product_key_encrypted") or None


async def create_application(company_id: int, values: dict[str, Any], user_id: int) -> int:
    return await db.execute_returning_lastrowid(
        """INSERT INTO applications
        (company_id, name, type_id, version, business_impact, importance,
         champion_staff_id, champion_name, product_key_encrypted, notes, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (company_id, values["name"], values.get("type_id"), values.get("version"),
         values.get("business_impact"), values["importance"], values.get("champion_staff_id"),
         values.get("champion_name"), values.get("product_key_encrypted"), values.get("notes"),
         user_id),
    )


async def update_application(
    company_id: int, application_id: int, values: dict[str, Any], *, product_key: str | None,
    clear_product_key: bool = False,
) -> None:
    """Update an application; the stored product key changes only when asked.

    ``product_key`` is ciphertext. ``None`` keeps the existing key unless
    ``clear_product_key`` is set, so editing other fields never needs the
    secret to round-trip through the form.
    """
    assignments = [
        "name = %s", "type_id = %s", "version = %s", "business_impact = %s", "importance = %s",
        "champion_staff_id = %s", "champion_name = %s", "notes = %s",
    ]
    params: list[Any] = [
        values["name"], values.get("type_id"), values.get("version"), values.get("business_impact"),
        values["importance"], values.get("champion_staff_id"), values.get("champion_name"),
        values.get("notes"),
    ]
    if product_key is not None:
        assignments.append("product_key_encrypted = %s")
        params.append(product_key)
    elif clear_product_key:
        assignments.append("product_key_encrypted = NULL")
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([application_id, company_id])
    await db.execute(
        f"UPDATE applications SET {', '.join(assignments)} WHERE id = %s AND company_id = %s",
        tuple(params),
    )


async def delete_application(company_id: int, application_id: int) -> bool:
    return bool(await db.execute_rowcount(
        "DELETE FROM applications WHERE id = %s AND company_id = %s", (application_id, company_id)
    ))


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


async def list_types(company_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        """SELECT t.id, t.name, COUNT(a.id) AS application_count
           FROM application_types t
           LEFT JOIN applications a ON a.type_id = t.id AND a.company_id = t.company_id
           WHERE t.company_id = %s
           GROUP BY t.id, t.name
           ORDER BY t.name""",
        (company_id,),
    ) or [])


async def get_type(company_id: int, type_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, name FROM application_types WHERE company_id = %s AND id = %s",
        (company_id, type_id),
    )


async def get_or_create_type(company_id: int, name: str) -> int:
    """Return the id of the company's type called ``name``, creating it if new.

    Matching ignores case so "CRM" and "crm" never become two types.
    """
    existing = await db.fetch_one(
        "SELECT id FROM application_types WHERE company_id = %s AND LOWER(name) = LOWER(%s)",
        (company_id, name),
    )
    if existing:
        return int(existing["id"])
    return await db.execute_returning_lastrowid(
        "INSERT INTO application_types (company_id, name) VALUES (%s, %s)", (company_id, name)
    )


async def delete_type(company_id: int, type_id: int) -> bool:
    return bool(await db.execute_rowcount(
        "DELETE FROM application_types WHERE id = %s AND company_id = %s", (type_id, company_id)
    ))


# ---------------------------------------------------------------------------
# Champions and links
# ---------------------------------------------------------------------------


async def list_champion_choices(company_id: int) -> list[dict[str, Any]]:
    return list(await db.fetch_all(
        """SELECT id, first_name, last_name, email FROM staff
           WHERE company_id = %s AND enabled = 1
           ORDER BY first_name, last_name, id""",
        (company_id,),
    ) or [])


async def staff_belongs_to_company(company_id: int, staff_id: int) -> bool:
    return bool(await db.fetch_one(
        "SELECT id FROM staff WHERE id = %s AND company_id = %s", (staff_id, company_id)
    ))


async def get_links(company_id: int, application_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return linked knowledge base articles and external KB links.

    Article visibility differs per viewer, so callers filter ``articles`` to
    those the viewer may open before rendering them.
    """
    articles = await db.fetch_all(
        """SELECT a.id, a.title, a.slug FROM application_kb_links l
           INNER JOIN knowledge_base_articles a ON a.id = l.article_id
           INNER JOIN applications app ON app.id = l.application_id
           WHERE l.application_id = %s AND app.company_id = %s
           ORDER BY a.title""",
        (application_id, company_id),
    )
    external = await db.fetch_all(
        """SELECT l.id, l.title, l.url FROM application_external_links l
           INNER JOIN applications app ON app.id = l.application_id
           WHERE l.application_id = %s AND app.company_id = %s
           ORDER BY l.position, l.id""",
        (application_id, company_id),
    )
    return {"articles": list(articles or []), "external": list(external or [])}


async def replace_links(
    company_id: int, application_id: int, article_ids: list[int],
    external_links: list[dict[str, str | None]],
) -> None:
    """Replace an application's links. Callers check article access first."""
    if not await db.fetch_one(
        "SELECT id FROM applications WHERE id = %s AND company_id = %s", (application_id, company_id)
    ):
        raise ValueError("Application not found")
    await db.execute("DELETE FROM application_kb_links WHERE application_id = %s", (application_id,))
    await db.execute("DELETE FROM application_external_links WHERE application_id = %s", (application_id,))
    for article_id in dict.fromkeys(article_ids):
        await db.execute(
            "INSERT INTO application_kb_links (application_id, article_id) VALUES (%s, %s)",
            (application_id, article_id),
        )
    for position, link in enumerate(external_links):
        await db.execute(
            "INSERT INTO application_external_links (application_id, title, url, position) "
            "VALUES (%s, %s, %s, %s)",
            (application_id, link.get("title"), link["url"], position),
        )
