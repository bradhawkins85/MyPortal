from __future__ import annotations

from typing import Any, Sequence

import aiomysql

from app.core.database import db

LabourTypeRecord = dict[str, Any]


def _normalise_record(row: dict[str, Any]) -> LabourTypeRecord:
    record = dict(row)
    identifier = record.get("id")
    try:
        record["id"] = int(identifier) if identifier is not None else None
    except (TypeError, ValueError):
        record["id"] = None
    if "is_default" in record:
        record["is_default"] = bool(record.get("is_default"))
    return record


async def list_labour_types() -> list[LabourTypeRecord]:
    rows = await db.fetch_all(
        """
        SELECT id, code, name, rate, is_default, created_at, updated_at
        FROM ticket_labour_types
        ORDER BY name ASC
        """
    )
    return [_normalise_record(row) for row in rows]


async def get_labour_type(labour_type_id: int) -> LabourTypeRecord | None:
    row = await db.fetch_one(
        """
        SELECT id, code, name, rate, is_default, created_at, updated_at
        FROM ticket_labour_types
        WHERE id = %s
        """,
        (labour_type_id,),
    )
    if row:
        return _normalise_record(row)
    return None


async def get_labour_type_by_code(code: str) -> LabourTypeRecord | None:
    row = await db.fetch_one(
        """
        SELECT id, code, name, rate, is_default, created_at, updated_at
        FROM ticket_labour_types
        WHERE LOWER(code) = LOWER(%s)
        """,
        (code,),
    )
    if row:
        return _normalise_record(row)
    return None


async def get_default_labour_type() -> LabourTypeRecord | None:
    """Get the default labour type."""
    row = await db.fetch_one(
        """
        SELECT id, code, name, rate, is_default, created_at, updated_at
        FROM ticket_labour_types
        WHERE is_default = 1
        LIMIT 1
        """,
    )
    if row:
        return _normalise_record(row)
    return None


async def create_labour_type(*, code: str, name: str, rate: float | None = None) -> LabourTypeRecord:
    labour_type_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_labour_types (code, name, rate, created_at, updated_at)
        VALUES (%s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
        """,
        (code, name, rate),
    )
    if labour_type_id:
        record = await get_labour_type(int(labour_type_id))
        if record:
            return record
    return {
        "id": int(labour_type_id) if labour_type_id else None,
        "code": code,
        "name": name,
        "rate": rate,
        "created_at": None,
        "updated_at": None,
    }


async def update_labour_type(
    labour_type_id: int,
    *,
    code: str | None = None,
    name: str | None = None,
    rate: float | None = None,
) -> LabourTypeRecord | None:
    updates: list[str] = []
    params: list[Any] = []
    if code is not None:
        updates.append("code = %s")
        params.append(code)
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if rate is not None:
        updates.append("rate = %s")
        params.append(rate)
    if updates:
        updates.append("updated_at = UTC_TIMESTAMP(6)")
        params.append(labour_type_id)
        await db.execute(
            f"""
            UPDATE ticket_labour_types
            SET {', '.join(updates)}
            WHERE id = %s
            """,
            tuple(params),
        )
    return await get_labour_type(labour_type_id)


async def delete_labour_type(labour_type_id: int) -> None:
    await db.execute(
        "DELETE FROM ticket_labour_types WHERE id = %s",
        (labour_type_id,),
    )


async def replace_labour_types(definitions: Sequence[dict[str, Any]]) -> list[LabourTypeRecord]:
    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await conn.begin()
            try:
                await cursor.execute("SELECT id, code FROM ticket_labour_types")
                existing_rows = await cursor.fetchall()
                existing_by_id: dict[int, dict[str, Any]] = {}
                for row in existing_rows:
                    identifier = row.get("id")
                    try:
                        numeric_id = int(identifier)
                    except (TypeError, ValueError):
                        continue
                    existing_by_id[numeric_id] = row

                seen_codes: set[str] = set()
                retained_ids: set[int] = set()
                has_default = False
                default_id: int | None = None

                # First pass: validate and check for default
                for entry in definitions:
                    raw_code = str(entry.get("code") or "").strip()
                    raw_name = str(entry.get("name") or "").strip()
                    if not raw_code and not raw_name:
                        continue
                    if not raw_code:
                        raise ValueError("Labour type code is required.")
                    if not raw_name:
                        raise ValueError("Labour type name is required.")
                    code_key = raw_code.lower()
                    if code_key in seen_codes:
                        raise ValueError("Labour type codes must be unique.")
                    seen_codes.add(code_key)
                    
                    is_default = bool(entry.get("is_default"))
                    if is_default:
                        if has_default:
                            raise ValueError("Only one labour type can be set as default.")
                        has_default = True

                # Require at least one default labour type
                if not has_default:
                    raise ValueError("At least one labour type must be set as default.")

                # Clear all defaults first
                await cursor.execute("UPDATE ticket_labour_types SET is_default = 0")

                # Second pass: update/insert records
                for entry in definitions:
                    raw_code = str(entry.get("code") or "").strip()
                    raw_name = str(entry.get("name") or "").strip()
                    raw_rate = entry.get("rate")
                    is_default = bool(entry.get("is_default"))
                    if not raw_code and not raw_name:
                        continue

                    identifier = entry.get("id")
                    labour_type_id: int | None = None
                    try:
                        labour_type_id = int(identifier) if identifier is not None else None
                    except (TypeError, ValueError):
                        labour_type_id = None

                    if labour_type_id and labour_type_id in existing_by_id:
                        await cursor.execute(
                            """
                            UPDATE ticket_labour_types
                            SET code = %s,
                                name = %s,
                                rate = %s,
                                is_default = %s,
                                updated_at = UTC_TIMESTAMP(6)
                            WHERE id = %s
                            """,
                            (raw_code, raw_name, raw_rate, 1 if is_default else 0, labour_type_id),
                        )
                        retained_ids.add(labour_type_id)
                    else:
                        await cursor.execute(
                            """
                            INSERT INTO ticket_labour_types (code, name, rate, is_default, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
                            """,
                            (raw_code, raw_name, raw_rate, 1 if is_default else 0),
                        )
                        inserted_id = cursor.lastrowid
                        if inserted_id:
                            retained_ids.add(int(inserted_id))

                to_delete = [
                    existing_id
                    for existing_id in existing_by_id
                    if existing_id not in retained_ids
                ]
                if to_delete:
                    placeholders = ", ".join(["%s"] * len(to_delete))
                    await cursor.execute(
                        f"DELETE FROM ticket_labour_types WHERE id IN ({placeholders})",
                        tuple(to_delete),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
    return await list_labour_types()
