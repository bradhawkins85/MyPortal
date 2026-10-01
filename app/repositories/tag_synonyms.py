from __future__ import annotations

"""Persistence for the controlled AI tag vocabulary.

``tag_synonyms`` maps a variant tag slug (``outlook-crashing``) to the
canonical slug it should be merged into (``outlook-crash``). The helpers here
also read existing ticket and knowledge base tags so the most-used tags can be
offered to the tagging prompts as preferred values.
"""

from datetime import datetime, timezone
import json
from typing import Any, Iterable

from app.core.database import db


def _decode_tags(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return []


async def list_synonyms() -> list[dict[str, Any]]:
    """Return every synonym mapping ordered by canonical then variant slug."""

    rows = await db.fetch_all(
        """
        SELECT id, variant_slug, canonical_slug, created_at, created_by
        FROM tag_synonyms
        ORDER BY canonical_slug ASC, variant_slug ASC
        """
    )
    return [dict(row) for row in rows]


async def get_synonym_map() -> dict[str, str]:
    """Return ``{variant_slug: canonical_slug}`` for every mapping."""

    rows = await db.fetch_all("SELECT variant_slug, canonical_slug FROM tag_synonyms")
    return {
        str(row["variant_slug"]): str(row["canonical_slug"])
        for row in rows
        if row.get("variant_slug") and row.get("canonical_slug")
    }


async def get_synonym_by_variant(variant_slug: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT id, variant_slug, canonical_slug, created_at, created_by
        FROM tag_synonyms
        WHERE variant_slug = %s
        """,
        (variant_slug,),
    )
    return dict(row) if row else None


async def add_synonym(
    variant_slug: str,
    canonical_slug: str,
    created_by: int | None = None,
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    synonym_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO tag_synonyms (variant_slug, canonical_slug, created_by, created_at)
        VALUES (%s, %s, %s, %s)
        """,
        (variant_slug, canonical_slug, created_by, created_at),
    )
    return {
        "id": synonym_id,
        "variant_slug": variant_slug,
        "canonical_slug": canonical_slug,
        "created_at": created_at,
        "created_by": created_by,
    }


async def repoint_canonical(old_canonical: str, new_canonical: str) -> int:
    """Point mappings whose target became a variant at the new canonical slug."""

    return await db.execute_rowcount(
        "UPDATE tag_synonyms SET canonical_slug = %s WHERE canonical_slug = %s",
        (new_canonical, old_canonical),
    )


async def delete_synonym(variant_slug: str) -> bool:
    rowcount = await db.execute_rowcount(
        "DELETE FROM tag_synonyms WHERE variant_slug = %s",
        (variant_slug,),
    )
    return rowcount > 0


async def list_recent_ticket_tags(limit: int) -> list[list[str]]:
    """Return the AI tag lists of the most recently created tickets."""

    rows = await db.fetch_all(
        """
        SELECT ai_tags
        FROM tickets
        WHERE ai_tags IS NOT NULL
        ORDER BY id DESC
        LIMIT %s
        """,
        (int(limit),),
    )
    return [_decode_tags(row.get("ai_tags")) for row in rows]


async def list_article_tags() -> list[list[str]]:
    """Return the combined AI and manual tag lists of every article."""

    rows = await db.fetch_all(
        "SELECT ai_tags, manual_ai_tags FROM knowledge_base_articles"
    )
    return [
        [*_decode_tags(row.get("ai_tags")), *_decode_tags(row.get("manual_ai_tags"))]
        for row in rows
    ]


async def list_tickets_with_tag_text(patterns: Iterable[str]) -> list[dict[str, Any]]:
    """Return ``id``/``ai_tags`` for tickets whose stored tags contain any pattern."""

    clauses: list[str] = []
    params: list[Any] = []
    for pattern in patterns:
        clauses.append("LOWER(CAST(ai_tags AS CHAR)) LIKE %s")
        params.append(f"%{pattern}%")
    if not clauses:
        return []
    rows = await db.fetch_all(
        f"SELECT id, ai_tags FROM tickets WHERE ai_tags IS NOT NULL AND ({' OR '.join(clauses)})",
        tuple(params),
    )
    return [{"id": int(row["id"]), "ai_tags": _decode_tags(row.get("ai_tags"))} for row in rows]


async def list_articles_with_tag_text(patterns: Iterable[str]) -> list[dict[str, Any]]:
    """Return tag columns for articles whose AI or manual tags contain any pattern."""

    clauses: list[str] = []
    params: list[Any] = []
    for pattern in patterns:
        clauses.append("LOWER(CAST(ai_tags AS CHAR)) LIKE %s")
        clauses.append("LOWER(CAST(manual_ai_tags AS CHAR)) LIKE %s")
        params.extend([f"%{pattern}%", f"%{pattern}%"])
    if not clauses:
        return []
    rows = await db.fetch_all(
        f"SELECT id, ai_tags, manual_ai_tags FROM knowledge_base_articles WHERE {' OR '.join(clauses)}",
        tuple(params),
    )
    return [
        {
            "id": int(row["id"]),
            "ai_tags": _decode_tags(row.get("ai_tags")),
            "manual_ai_tags": _decode_tags(row.get("manual_ai_tags")),
        }
        for row in rows
    ]


async def set_ticket_tags(ticket_id: int, tags: list[str]) -> None:
    """Rewrite a ticket's AI tags without touching ``updated_at``."""

    await db.execute(
        "UPDATE tickets SET ai_tags = %s WHERE id = %s",
        (json.dumps(tags), ticket_id),
    )


async def set_article_tags(article_id: int, ai_tags: list[str], manual_ai_tags: list[str]) -> None:
    """Rewrite an article's tags without touching ``updated_at``."""

    await db.execute(
        "UPDATE knowledge_base_articles SET ai_tags = %s, manual_ai_tags = %s WHERE id = %s",
        (json.dumps(ai_tags), json.dumps(manual_ai_tags), article_id),
    )
