from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.core.database import db


def _normalise_license(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    if "company_id" in normalised and normalised["company_id"] is not None:
        normalised["company_id"] = int(normalised["company_id"])
    if "count" in normalised and normalised["count"] is not None:
        normalised["count"] = int(normalised["count"])
    if "allocated" in normalised and normalised["allocated"] is not None:
        normalised["allocated"] = int(normalised["allocated"])
    for key in ("expiry_date", "token_expires_at"):
        value = normalised.get(key)
        if isinstance(value, datetime):
            normalised[key] = value.replace(tzinfo=None)
    if "auto_renew" in normalised and normalised["auto_renew"] is not None:
        normalised["auto_renew"] = bool(normalised["auto_renew"])
    return normalised


_ALLOCATED_SUBQUERY = """
    (SELECT COUNT(DISTINCT s.id)
     FROM staff AS s
     WHERE s.id IN (
         SELECT sl2.staff_id FROM staff_licenses AS sl2 WHERE sl2.license_id = l.id
         UNION
         SELECT ogm.staff_id
         FROM group_licenses AS gl
         INNER JOIN office_group_members AS ogm ON ogm.group_id = gl.group_id
         WHERE gl.license_id = l.id
     )
    )
"""


async def list_company_licenses(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        f"""
        SELECT l.*, COALESCE(lsn.friendly_name, a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        LEFT JOIN license_sku_friendly_names AS lsn ON lsn.sku = l.platform
        WHERE l.company_id = %s
          AND COALESCE(lsn.hidden, 0) = 0
        GROUP BY l.id
        ORDER BY display_name, l.name
        """,
        (company_id,),
    )
    return [_normalise_license(row) for row in rows]


async def list_all_licenses() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        f"""
        SELECT l.*, COALESCE(lsn.friendly_name, a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        LEFT JOIN license_sku_friendly_names AS lsn ON lsn.sku = l.platform
        WHERE COALESCE(lsn.hidden, 0) = 0
        GROUP BY l.id
        ORDER BY l.company_id, display_name
        """,
    )
    return [_normalise_license(row) for row in rows]


async def get_license_by_id(license_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"""
        SELECT l.*, COALESCE(lsn.friendly_name, a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        LEFT JOIN license_sku_friendly_names AS lsn ON lsn.sku = l.platform
        WHERE l.id = %s
          AND COALESCE(lsn.hidden, 0) = 0
        GROUP BY l.id
        """,
        (license_id,),
    )
    return _normalise_license(row) if row else None


async def get_license_by_company_and_sku(company_id: int, sku: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"""
        SELECT l.*, COALESCE(lsn.friendly_name, a.name, l.name) AS display_name,
               {_ALLOCATED_SUBQUERY} AS allocated
        FROM licenses AS l
        LEFT JOIN apps AS a ON a.vendor_sku = l.platform
        LEFT JOIN license_sku_friendly_names AS lsn ON lsn.sku = l.platform
        WHERE l.company_id = %s
          AND l.platform = %s
          AND COALESCE(lsn.hidden, 0) = 0
        GROUP BY l.id
        """,
        (company_id, sku),
    )
    return _normalise_license(row) if row else None


async def create_license(
    *,
    company_id: int,
    name: str,
    platform: str,
    count: int,
    expiry_date: datetime | None,
    contract_term: str | None,
    auto_renew: bool | None = None,
) -> dict[str, Any]:
    license_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO licenses (company_id, name, platform, count, expiry_date, contract_term, auto_renew)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            name,
            platform,
            count,
            expiry_date,
            contract_term,
            int(auto_renew) if auto_renew is not None else None,
        ),
    )
    if not license_id:
        raise RuntimeError("Failed to create license")
    row = await db.fetch_one("SELECT * FROM licenses WHERE id = %s", (license_id,))
    if not row:
        raise RuntimeError("Failed to retrieve created license")
    return _normalise_license(row)


async def update_license(
    license_id: int,
    *,
    company_id: int,
    name: str,
    platform: str,
    count: int,
    expiry_date: datetime | None,
    contract_term: str | None,
    auto_renew: bool | None = None,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE licenses
        SET company_id = %s, name = %s, platform = %s, count = %s, expiry_date = %s,
            contract_term = %s, auto_renew = %s
        WHERE id = %s
        """,
        (
            company_id,
            name,
            platform,
            count,
            expiry_date,
            contract_term,
            int(auto_renew) if auto_renew is not None else None,
            license_id,
        ),
    )
    updated = await get_license_by_id(license_id)
    if not updated:
        raise ValueError("License not found after update")
    return updated


async def delete_license(license_id: int) -> None:
    await db.execute("DELETE FROM staff_licenses WHERE license_id = %s", (license_id,))
    await db.execute("DELETE FROM licenses WHERE id = %s", (license_id,))


async def list_staff_by_license_for_company(company_id: int) -> dict[int, list[dict[str, Any]]]:
    """Return a mapping of license_id -> list of assigned staff for a company."""
    rows = await db.fetch_all(
        """
        SELECT DISTINCT l.id AS license_id, s.id AS staff_id, s.first_name, s.last_name, s.email
        FROM licenses AS l
        INNER JOIN (
            SELECT sl.license_id, sl.staff_id
            FROM staff_licenses AS sl
            UNION
            SELECT gl.license_id, ogm.staff_id
            FROM group_licenses AS gl
            INNER JOIN office_group_members AS ogm ON ogm.group_id = gl.group_id
        ) AS la ON la.license_id = l.id
        INNER JOIN staff AS s ON s.id = la.staff_id
        WHERE l.company_id = %s
        ORDER BY l.id, s.last_name, s.first_name
        """,
        (company_id,),
    )
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        lid = int(row["license_id"])
        result.setdefault(lid, []).append(
            {
                "id": row["staff_id"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "email": row["email"],
            }
        )
    return result


async def list_staff_for_license(license_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT s.id, s.first_name, s.last_name, s.email
        FROM staff AS s
        WHERE s.id IN (
            SELECT sl.staff_id FROM staff_licenses AS sl WHERE sl.license_id = %s
            UNION
            SELECT ogm.staff_id
            FROM group_licenses AS gl
            INNER JOIN office_group_members AS ogm ON ogm.group_id = gl.group_id
            WHERE gl.license_id = %s
        )
        ORDER BY s.last_name, s.first_name
        """,
        (license_id, license_id),
    )
    return [dict(row) for row in rows]


async def list_groups_for_license(license_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT og.id, og.name
        FROM group_licenses AS gl
        INNER JOIN office_groups AS og ON og.id = gl.group_id
        WHERE gl.license_id = %s
        ORDER BY og.name
        """,
        (license_id,),
    )
    return [dict(row) for row in rows]


async def link_group_to_license(group_id: int, license_id: int) -> None:
    await db.execute(
        """
        INSERT IGNORE INTO group_licenses (group_id, license_id)
        VALUES (%s, %s)
        """,
        (group_id, license_id),
    )


async def unlink_group_from_license(group_id: int, license_id: int) -> None:
    await db.execute(
        "DELETE FROM group_licenses WHERE group_id = %s AND license_id = %s",
        (group_id, license_id),
    )


async def link_staff_to_license(staff_id: int, license_id: int) -> None:
    await db.execute(
        """
        INSERT INTO staff_licenses (staff_id, license_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE staff_id = VALUES(staff_id)
        """,
        (staff_id, license_id),
    )


async def unlink_staff_from_license(staff_id: int, license_id: int) -> None:
    await db.execute(
        "DELETE FROM staff_licenses WHERE staff_id = %s AND license_id = %s",
        (staff_id, license_id),
    )


async def bulk_unlink_staff(license_id: int, staff_ids: Iterable[int]) -> None:
    ids = list(staff_ids)
    if not ids:
        return
    placeholders = ", ".join(["%s"] * len(ids))
    await db.execute(
        f"DELETE FROM staff_licenses WHERE license_id = %s AND staff_id IN ({placeholders})",
        tuple([license_id, *ids]),
    )


async def record_usage_if_changed(license_id: int, count: int, allocated: int) -> bool:
    """Record a usage entry for *license_id* only when count or allocated has changed.

    The initial snapshot for a newly-seen license is always recorded.  Subsequent
    calls only insert a new row when *count* or *allocated* differs from the most
    recently recorded values.

    Returns ``True`` if a new row was written, ``False`` otherwise.
    """
    last_row = await db.fetch_one(
        "SELECT count, allocated FROM license_usage_history"
        " WHERE license_id = %s ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (license_id,),
    )

    if last_row is not None:
        if int(last_row["count"]) == count and int(last_row["allocated"]) == allocated:
            return False

    await db.execute(
        "INSERT INTO license_usage_history (license_id, count, allocated) VALUES (%s, %s, %s)",
        (license_id, count, allocated),
    )
    return True


async def get_usage_history(license_id: int) -> list[dict[str, Any]]:
    """Return all recorded usage entries for *license_id* in ascending order."""
    rows = await db.fetch_all(
        "SELECT id, license_id, count, allocated, recorded_at"
        " FROM license_usage_history"
        " WHERE license_id = %s ORDER BY recorded_at ASC, id ASC",
        (license_id,),
    )
    return [
        {
            "id": row["id"],
            "license_id": row["license_id"],
            "count": int(row["count"]),
            "allocated": int(row["allocated"]),
            "recorded_at": row["recorded_at"],
        }
        for row in rows
    ]
