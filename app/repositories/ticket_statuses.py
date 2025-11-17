from __future__ import annotations

import re
from typing import Any, Sequence

import aiomysql

from app.core.database import db

DEFAULT_STATUS_DEFINITIONS: list[dict[str, str]] = [
    {"tech_status": "open", "tech_label": "Open", "public_status": "Open"},
    {"tech_status": "in_progress", "tech_label": "In progress", "public_status": "In progress"},
    {"tech_status": "pending", "tech_label": "Pending", "public_status": "Pending"},
    {"tech_status": "resolved", "tech_label": "Resolved", "public_status": "Resolved"},
    {"tech_status": "closed", "tech_label": "Closed", "public_status": "Closed"},
]

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify_status_label(value: str) -> str:
    """Convert a status label into a canonical slug value."""

    if not isinstance(value, str):
        return ""
    normalised = value.strip().lower()
    if not normalised:
        return ""
    slug = _SLUG_PATTERN.sub("_", normalised)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    slug = str(row.get("tech_status") or "").strip().lower()
    label = str(row.get("tech_label") or "").strip()
    public = str(row.get("public_status") or "").strip()
    is_default = bool(row.get("is_default", False))
    return {
        "tech_status": slug,
        "tech_label": label or slug.replace("_", " ").title(),
        "public_status": public or label or slug.replace("_", " ").title(),
        "is_default": is_default,
    }


async def list_statuses() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT tech_status, tech_label, public_status, is_default FROM ticket_statuses ORDER BY tech_label ASC"
    )
    return [_normalise_row(row) for row in rows]


async def ensure_default_statuses() -> list[dict[str, str]]:
    existing = await list_statuses()
    if existing:
        return existing
    async with db.acquire() as conn:
        async with conn.cursor() as cursor:
            await conn.begin()
            try:
                for definition in DEFAULT_STATUS_DEFINITIONS:
                    await cursor.execute(
                        """
                        INSERT INTO ticket_statuses (tech_status, tech_label, public_status, created_at, updated_at)
                        VALUES (%s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
                        ON DUPLICATE KEY UPDATE
                            tech_label = VALUES(tech_label),
                            public_status = VALUES(public_status),
                            updated_at = UTC_TIMESTAMP(6)
                        """,
                        (
                            definition["tech_status"],
                            definition["tech_label"],
                            definition["public_status"],
                        ),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
    return await list_statuses()


async def status_exists(slug: str) -> bool:
    if not slug:
        return False
    row = await db.fetch_one(
        "SELECT 1 FROM ticket_statuses WHERE tech_status = %s",
        (slug,),
    )
    return bool(row)


async def replace_statuses(definitions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not definitions:
        return await list_statuses()

    async with db.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            await conn.begin()
            try:
                await cursor.execute("SELECT tech_status FROM ticket_statuses")
                current_rows = await cursor.fetchall()
                current_slugs = {str(row["tech_status"]).strip().lower() for row in current_rows}

                encountered: set[str] = set()
                default_slug: str | None = None

                # First, find which status should be default
                for definition in definitions:
                    is_default = bool(definition.get("is_default", False))
                    if is_default:
                        slug = str(definition.get("tech_status") or "").strip().lower()
                        if default_slug is not None:
                            raise ValueError("Only one status can be set as default.")
                        default_slug = slug

                # If no default specified, use the first one
                if default_slug is None and definitions:
                    first_def = definitions[0]
                    default_slug = str(first_def.get("tech_status") or "").strip().lower()

                for definition in definitions:
                    slug = str(definition.get("tech_status") or "").strip().lower()
                    label = str(definition.get("tech_label") or "").strip()
                    public_status = str(definition.get("public_status") or "").strip() or label
                    original_slug = str(definition.get("original_slug") or "").strip().lower() or slug
                    is_default = 1 if slug == default_slug else 0

                    if original_slug and original_slug in current_slugs:
                        if slug == original_slug:
                            await cursor.execute(
                                """
                                UPDATE ticket_statuses
                                SET tech_label = %s,
                                    public_status = %s,
                                    is_default = %s,
                                    updated_at = UTC_TIMESTAMP(6)
                                WHERE tech_status = %s
                                """,
                                (label, public_status, is_default, original_slug),
                            )
                        else:
                            if slug in current_slugs and slug != original_slug:
                                raise ValueError("Tech status values must be unique.")
                            if slug in encountered:
                                raise ValueError("Tech status values must be unique.")
                            await cursor.execute(
                                """
                                UPDATE ticket_statuses
                                SET tech_status = %s,
                                    tech_label = %s,
                                    public_status = %s,
                                    is_default = %s,
                                    updated_at = UTC_TIMESTAMP(6)
                                WHERE tech_status = %s
                                """,
                                (slug, label, public_status, is_default, original_slug),
                            )
                            await cursor.execute(
                                "UPDATE tickets SET status = %s WHERE status = %s",
                                (slug, original_slug),
                            )
                            current_slugs.discard(original_slug)
                            current_slugs.add(slug)
                    else:
                        if slug in current_slugs or slug in encountered:
                            raise ValueError("Tech status values must be unique.")
                        await cursor.execute(
                            """
                            INSERT INTO ticket_statuses (tech_status, tech_label, public_status, is_default, created_at, updated_at)
                            VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))
                            """,
                            (slug, label, public_status, is_default),
                        )
                        current_slugs.add(slug)

                    encountered.add(slug)

                slugs_to_remove = current_slugs - encountered
                if slugs_to_remove:
                    placeholders = ", ".join(["%s"] * len(slugs_to_remove))
                    await cursor.execute(
                        f"SELECT status, COUNT(*) AS usage_count FROM tickets WHERE status IN ({placeholders}) GROUP BY status",
                        tuple(slugs_to_remove),
                    )
                    usage_rows = await cursor.fetchall()
                    in_use: dict[str, int] = {}
                    for row in usage_rows:
                        status_value = str(row.get("status") or "").strip().lower()
                        try:
                            count = int(row.get("usage_count") or 0)
                        except (TypeError, ValueError):
                            count = 0
                        if count:
                            in_use[status_value] = count
                    if in_use:
                        raise ValueError(
                            "Cannot remove ticket statuses that are still assigned to tickets: "
                            + ", ".join(sorted(in_use.keys()))
                        )
                    await cursor.execute(
                        f"DELETE FROM ticket_statuses WHERE tech_status IN ({placeholders})",
                        tuple(slugs_to_remove),
                    )

                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    return await list_statuses()


async def get_status_definition(slug: str) -> dict[str, Any] | None:
    if not slug:
        return None
    row = await db.fetch_one(
        "SELECT tech_status, tech_label, public_status, is_default FROM ticket_statuses WHERE tech_status = %s",
        (slug,),
    )
    return _normalise_row(row) if row else None


async def get_default_status() -> dict[str, Any] | None:
    """Get the default status definition."""
    row = await db.fetch_one(
        "SELECT tech_status, tech_label, public_status, is_default FROM ticket_statuses WHERE is_default = 1",
    )
    return _normalise_row(row) if row else None
