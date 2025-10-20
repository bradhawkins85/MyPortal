from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.core.database import db

_PERMISSION_SCOPES = {
    "anonymous",
    "user",
    "company",
    "company_admin",
    "super_admin",
}


def _normalise_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalise_article(row: dict[str, Any]) -> dict[str, Any]:
    article = dict(row)
    article_id = article.get("id")
    if article_id is not None:
        article["id"] = int(article_id)
    created_by = article.get("created_by")
    if created_by is not None:
        article["created_by"] = int(created_by)
    article["is_published"] = bool(int(article.get("is_published", 0)))
    for key in ("created_at", "updated_at", "published_at"):
        article[f"{key}_utc"] = _normalise_datetime(article.get(key))
    permission = article.get("permission_scope") or "anonymous"
    if permission not in _PERMISSION_SCOPES:
        permission = "anonymous"
    article["permission_scope"] = permission
    return article


def _prepare_in_clause(ids: Iterable[int]) -> tuple[str, tuple[int, ...]] | tuple[None, tuple[int, ...]]:
    normalised: list[int] = []
    for value in ids:
        try:
            normalised.append(int(value))
        except (TypeError, ValueError):
            continue
    if not normalised:
        return None, tuple()
    unique = sorted(set(normalised))
    placeholders = ", ".join(["%s"] * len(unique))
    return placeholders, tuple(unique)


async def _attach_relations(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    article_ids = [row.get("id") for row in rows if row.get("id") is not None]
    placeholders, params = _prepare_in_clause(article_ids)
    user_map: dict[int, list[int]] = defaultdict(list)
    member_company_map: dict[int, list[int]] = defaultdict(list)
    admin_company_map: dict[int, list[int]] = defaultdict(list)
    section_map: dict[int, list[dict[str, Any]]] = defaultdict(list)

    if placeholders:
        user_rows = await db.fetch_all(
            f"SELECT article_id, user_id FROM knowledge_base_article_users WHERE article_id IN ({placeholders})",
            params,
        )
        for relation in user_rows:
            try:
                article_id = int(relation.get("article_id"))
                user_id = int(relation.get("user_id"))
            except (TypeError, ValueError):
                continue
            user_map[article_id].append(user_id)

        company_rows = await db.fetch_all(
            f"SELECT article_id, company_id, require_admin FROM knowledge_base_article_companies WHERE article_id IN ({placeholders})",
            params,
        )
        for relation in company_rows:
            try:
                article_id = int(relation.get("article_id"))
                company_id = int(relation.get("company_id"))
                require_admin = bool(int(relation.get("require_admin", 0)))
            except (TypeError, ValueError):
                continue
            if require_admin:
                admin_company_map[article_id].append(company_id)
            else:
                member_company_map[article_id].append(company_id)

        section_rows = await db.fetch_all(
            f"""
            SELECT id, article_id, position, heading, content
            FROM knowledge_base_sections
            WHERE article_id IN ({placeholders})
            ORDER BY position ASC, id ASC
            """,
            params,
        )
        for row in section_rows:
            try:
                article_id = int(row.get("article_id"))
            except (TypeError, ValueError):
                continue
            heading = row.get("heading")
            section_map[article_id].append(
                {
                    "id": row.get("id"),
                    "heading": heading if isinstance(heading, str) else None,
                    "content": row.get("content") or "",
                    "position": row.get("position"),
                }
            )

    enriched: list[dict[str, Any]] = []
    for row in rows:
        article = _normalise_article(row)
        article_id = article.get("id")
        if isinstance(article_id, int):
            article["allowed_user_ids"] = sorted(set(user_map.get(article_id, [])))
            article["company_ids"] = sorted(set(member_company_map.get(article_id, [])))
            article["company_admin_ids"] = sorted(set(admin_company_map.get(article_id, [])))
            ordered_sections = section_map.get(article_id, [])
            article["sections"] = ordered_sections
        else:
            article["allowed_user_ids"] = []
            article["company_ids"] = []
            article["company_admin_ids"] = []
            article["sections"] = []
        enriched.append(article)
    return enriched


async def list_articles(*, include_unpublished: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM knowledge_base_articles"
    params: tuple[Any, ...] = ()
    if not include_unpublished:
        sql += " WHERE is_published = 1"
    sql += " ORDER BY updated_at DESC, id DESC"
    rows = await db.fetch_all(sql, params if params else None)
    return await _attach_relations(rows)


async def get_article_by_id(article_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM knowledge_base_articles WHERE id = %s",
        (article_id,),
    )
    if not row:
        return None
    enriched = await _attach_relations([row])
    return enriched[0] if enriched else None


async def get_article_by_slug(slug: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM knowledge_base_articles WHERE slug = %s",
        (slug,),
    )
    if not row:
        return None
    enriched = await _attach_relations([row])
    return enriched[0] if enriched else None


async def create_article(
    *,
    slug: str,
    title: str,
    summary: str | None,
    content: str,
    permission_scope: str,
    is_published: bool,
    published_at: datetime | None,
    created_by: int | None,
) -> dict[str, Any]:
    if permission_scope not in _PERMISSION_SCOPES:
        permission_scope = "anonymous"
    article_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO knowledge_base_articles (
            slug, title, summary, content, permission_scope, is_published, published_at, created_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            slug,
            title,
            summary,
            content,
            permission_scope,
            1 if is_published else 0,
            published_at,
            created_by,
        ),
    )
    created = await get_article_by_id(article_id)
    if not created:
        raise RuntimeError("Failed to create knowledge base article")
    return created


async def update_article(article_id: int, **updates: Any) -> dict[str, Any]:
    if not updates:
        article = await get_article_by_id(article_id)
        if not article:
            raise ValueError("Article not found")
        return article

    columns: list[str] = []
    params: list[Any] = []
    for column, value in updates.items():
        if column == "permission_scope" and value not in _PERMISSION_SCOPES:
            continue
        if column == "is_published":
            columns.append("is_published = %s")
            params.append(1 if value else 0)
        else:
            columns.append(f"{column} = %s")
            params.append(value)
    if not columns:
        article = await get_article_by_id(article_id)
        if not article:
            raise ValueError("Article not found")
        return article
    params.append(article_id)
    sql = f"UPDATE knowledge_base_articles SET {', '.join(columns)} WHERE id = %s"
    await db.execute(sql, tuple(params))
    updated = await get_article_by_id(article_id)
    if not updated:
        raise ValueError("Article not found after update")
    return updated


async def delete_article(article_id: int) -> None:
    await db.execute("DELETE FROM knowledge_base_articles WHERE id = %s", (article_id,))


async def replace_article_users(article_id: int, user_ids: Iterable[int]) -> None:
    await db.execute(
        "DELETE FROM knowledge_base_article_users WHERE article_id = %s",
        (article_id,),
    )
    seen: set[int] = set()
    for user_id in user_ids:
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            continue
        if user_id_int in seen:
            continue
        seen.add(user_id_int)
        await db.execute(
            "INSERT INTO knowledge_base_article_users (article_id, user_id) VALUES (%s, %s)",
            (article_id, user_id_int),
        )


async def replace_article_companies(
    article_id: int,
    company_ids: Iterable[int],
    *,
    require_admin: bool,
) -> None:
    await db.execute(
        "DELETE FROM knowledge_base_article_companies WHERE article_id = %s AND require_admin = %s",
        (article_id, 1 if require_admin else 0),
    )
    seen: set[int] = set()
    for company_id in company_ids:
        try:
            company_id_int = int(company_id)
        except (TypeError, ValueError):
            continue
        if company_id_int in seen:
            continue
        seen.add(company_id_int)
        await db.execute(
            """
            INSERT INTO knowledge_base_article_companies (article_id, company_id, require_admin)
            VALUES (%s, %s, %s)
            """,
            (article_id, company_id_int, 1 if require_admin else 0),
        )


async def replace_article_sections(
    article_id: int, sections: Sequence[Mapping[str, Any]]
) -> None:
    await db.execute(
        "DELETE FROM knowledge_base_sections WHERE article_id = %s",
        (article_id,),
    )
    for index, section in enumerate(sections, start=1):
        heading = section.get("heading")
        if heading is not None and not isinstance(heading, str):
            heading = str(heading)
        content = section.get("content") or ""
        position = section.get("position")
        try:
            position_int = int(position) if position is not None else index
        except (TypeError, ValueError):
            position_int = index
        await db.execute(
            """
            INSERT INTO knowledge_base_sections (article_id, position, heading, content)
            VALUES (%s, %s, %s, %s)
            """,
            (article_id, position_int, heading, content),
        )

