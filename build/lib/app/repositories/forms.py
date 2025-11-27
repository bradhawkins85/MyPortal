from __future__ import annotations

from typing import Any, Iterable

import aiomysql

from app.core.database import db


def _normalise_form(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    if "id" in normalised and normalised["id"] is not None:
        normalised["id"] = int(normalised["id"])
    return normalised


async def list_forms() -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM forms ORDER BY name")
    return [_normalise_form(row) for row in rows]


async def list_forms_for_user(user_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT f.*
        FROM forms AS f
        INNER JOIN form_permissions AS fp ON fp.form_id = f.id
        WHERE fp.user_id = %s
        ORDER BY f.name
        """,
        (user_id,),
    )
    return [_normalise_form(row) for row in rows]


async def list_forms_for_company(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT f.*
        FROM forms AS f
        INNER JOIN form_permissions AS fp ON fp.form_id = f.id
        WHERE fp.company_id = %s
        ORDER BY f.name
        """,
        (company_id,),
    )
    return [_normalise_form(row) for row in rows]


async def get_form(form_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM forms WHERE id = %s", (form_id,))
    return _normalise_form(row) if row else None


async def create_form(
    *,
    name: str,
    url: str,
    embed_code: str | None,
    description: str | None,
) -> dict[str, Any]:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                INSERT INTO forms (name, url, embed_code, description)
                VALUES (%s, %s, %s, %s)
                """,
                (name, url, embed_code, description),
            )
            form_id = int(cursor.lastrowid)
    form = await get_form(form_id)
    if not form:
        raise RuntimeError("Failed to create form")
    return form


async def update_form(
    form_id: int,
    *,
    name: str,
    url: str,
    embed_code: str | None,
    description: str | None,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE forms
        SET name = %s, url = %s, embed_code = %s, description = %s
        WHERE id = %s
        """,
        (name, url, embed_code, description, form_id),
    )
    form = await get_form(form_id)
    if not form:
        raise ValueError("Form not found after update")
    return form


async def delete_form(form_id: int) -> None:
    await db.execute("DELETE FROM forms WHERE id = %s", (form_id,))


async def list_form_permissions(form_id: int, company_id: int) -> list[int]:
    rows = await db.fetch_all(
        """
        SELECT user_id
        FROM form_permissions
        WHERE form_id = %s AND company_id = %s
        ORDER BY user_id
        """,
        (form_id, company_id),
    )
    return [int(row["user_id"]) for row in rows]


async def update_form_permissions(
    form_id: int,
    company_id: int,
    user_ids: Iterable[int],
) -> None:
    ids = [int(user_id) for user_id in user_ids]
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM form_permissions WHERE form_id = %s AND company_id = %s",
                (form_id, company_id),
            )
            if not ids:
                return
            values = [(form_id, user_id, company_id) for user_id in ids]
            await cursor.executemany(
                """
                INSERT INTO form_permissions (form_id, user_id, company_id)
                VALUES (%s, %s, %s)
                """,
                values,
            )


async def delete_form_permission(form_id: int, user_id: int, company_id: int) -> None:
    await db.execute(
        """
        DELETE FROM form_permissions
        WHERE form_id = %s AND user_id = %s AND company_id = %s
        """,
        (form_id, user_id, company_id),
    )


async def list_permission_entries() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT fp.form_id, f.name AS form_name, fp.user_id, u.email,
               fp.company_id, c.name AS company_name
        FROM form_permissions AS fp
        INNER JOIN forms AS f ON f.id = fp.form_id
        INNER JOIN users AS u ON u.id = fp.user_id
        INNER JOIN companies AS c ON c.id = fp.company_id
        ORDER BY u.email, c.name, f.name
        """,
    )
    entries: list[dict[str, Any]] = []
    for row in rows:
        normalised = dict(row)
        normalised["form_id"] = int(row.get("form_id", 0))
        normalised["user_id"] = int(row.get("user_id", 0))
        normalised["company_id"] = int(row.get("company_id", 0))
        entries.append(normalised)
    return entries
