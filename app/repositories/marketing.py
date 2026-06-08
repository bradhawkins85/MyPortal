from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core.database import db

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    candidate = _SLUG_PATTERN.sub("-", (value or "").strip().lower())
    candidate = re.sub(r"-+", "-", candidate).strip("-")
    return candidate


def _normalise_page(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "slug": str(row.get("slug") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "subtitle": str(row.get("subtitle") or "").strip() or None,
        "intro_text": str(row.get("intro_text") or "").strip() or None,
        "is_published": bool(int(row.get("is_published") or 0)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _normalise_section(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "page_id": int(row["page_id"]),
        "title": str(row.get("title") or "").strip(),
        "anchor_slug": str(row.get("anchor_slug") or "").strip(),
        "content_text": str(row.get("content_text") or "").strip(),
        "sort_order": int(row.get("sort_order") or 0),
    }


async def list_pages() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT p.*, COUNT(l.id) AS lead_count
        FROM marketing_pages AS p
        LEFT JOIN marketing_leads AS l ON l.page_id = p.id
        GROUP BY p.id
        ORDER BY p.title
        """
    )
    pages = [_normalise_page(row) for row in rows]
    for page, row in zip(pages, rows):
        page["lead_count"] = int(row.get("lead_count") or 0)
    return pages


async def get_page_by_slug(slug: str, *, published_only: bool = False) -> dict[str, Any] | None:
    query = "SELECT * FROM marketing_pages WHERE slug = %s"
    if published_only:
        query += " AND is_published = 1"
    row = await db.fetch_one(query, (slug,))
    if not row:
        return None
    page = _normalise_page(row)
    page["sections"] = await list_sections(page["id"])
    return page


async def get_page_by_id(page_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM marketing_pages WHERE id = %s", (page_id,))
    if not row:
        return None
    page = _normalise_page(row)
    page["sections"] = await list_sections(page["id"])
    return page


async def create_page(
    *,
    slug: str,
    title: str,
    subtitle: str | None = None,
    intro_text: str | None = None,
    is_published: bool = False,
) -> dict[str, Any]:
    now = datetime.utcnow()
    await db.execute(
        """
        INSERT INTO marketing_pages (slug, title, subtitle, intro_text, is_published, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (slug, title, subtitle, intro_text, 1 if is_published else 0, now, now),
    )
    created = await get_page_by_slug(slug)
    if not created:
        raise RuntimeError("Failed to create marketing page")
    return created


async def update_page(
    page_id: int,
    *,
    slug: str,
    title: str,
    subtitle: str | None,
    intro_text: str | None,
    is_published: bool = False,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE marketing_pages
        SET slug = %s,
            title = %s,
            subtitle = %s,
            intro_text = %s,
            is_published = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (slug, title, subtitle, intro_text, 1 if is_published else 0, datetime.utcnow(), page_id),
    )
    updated = await get_page_by_id(page_id)
    if not updated:
        raise ValueError("Marketing page not found")
    return updated


async def delete_page(page_id: int) -> None:
    await db.execute("DELETE FROM marketing_pages WHERE id = %s", (page_id,))


async def list_sections(page_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT * FROM marketing_page_sections
        WHERE page_id = %s
        ORDER BY sort_order, id
        """,
        (page_id,),
    )
    return [_normalise_section(row) for row in rows]


async def create_section(
    *,
    page_id: int,
    title: str,
    anchor_slug: str,
    content_text: str,
    sort_order: int = 0,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO marketing_page_sections (page_id, title, anchor_slug, content_text, sort_order)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (page_id, title, anchor_slug, content_text, sort_order),
    )
    row = await db.fetch_one(
        """
        SELECT * FROM marketing_page_sections
        WHERE page_id = %s AND anchor_slug = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (page_id, anchor_slug),
    )
    if not row:
        raise RuntimeError("Failed to create marketing section")
    return _normalise_section(row)


async def delete_section(section_id: int) -> None:
    await db.execute("DELETE FROM marketing_page_sections WHERE id = %s", (section_id,))


async def get_section_by_id(section_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM marketing_page_sections WHERE id = %s",
        (section_id,),
    )
    if not row:
        return None
    return _normalise_section(row)


async def list_leads(limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT l.*, p.slug AS page_slug, p.title AS page_title
        FROM marketing_leads AS l
        LEFT JOIN marketing_pages AS p ON p.id = l.page_id
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT %s
        """,
        (max(1, int(limit)),),
    )
    leads: list[dict[str, Any]] = []
    for row in rows:
        leads.append(
            {
                "id": int(row["id"]),
                "page_id": int(row["page_id"]) if row.get("page_id") is not None else None,
                "page_slug": str(row.get("page_slug") or row.get("slug_snapshot") or "").strip(),
                "page_title": str(row.get("page_title") or row.get("page_title_snapshot") or "").strip(),
                "name": str(row.get("name") or "").strip(),
                "email": str(row.get("email") or "").strip(),
                "phone": str(row.get("phone") or "").strip(),
                "allow_marketing": bool(int(row.get("allow_marketing") or 0)),
                "allow_other_services": bool(int(row.get("allow_other_services") or 0)),
                "ticket_id": int(row["ticket_id"]) if row.get("ticket_id") is not None else None,
                "created_at": row.get("created_at"),
            }
        )
    return leads


async def create_lead(
    *,
    page_id: int,
    slug_snapshot: str,
    page_title_snapshot: str,
    name: str,
    email: str,
    phone: str,
    allow_marketing: bool,
    allow_other_services: bool,
    ticket_id: int | None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO marketing_leads (
            page_id,
            slug_snapshot,
            page_title_snapshot,
            name,
            email,
            phone,
            allow_marketing,
            allow_other_services,
            ticket_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            page_id,
            slug_snapshot,
            page_title_snapshot,
            name,
            email,
            phone,
            1 if allow_marketing else 0,
            1 if allow_other_services else 0,
            ticket_id,
        ),
    )
    row = await db.fetch_one(
        """
        SELECT *
        FROM marketing_leads
        WHERE page_id = %s AND email = %s AND name = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (page_id, email, name),
    )
    if not row:
        raise RuntimeError("Failed to create marketing lead")
    return {
        "id": int(row["id"]),
        "page_id": int(row["page_id"]),
        "slug_snapshot": str(row.get("slug_snapshot") or "").strip(),
        "page_title_snapshot": str(row.get("page_title_snapshot") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "email": str(row.get("email") or "").strip(),
        "phone": str(row.get("phone") or "").strip(),
        "allow_marketing": bool(int(row.get("allow_marketing") or 0)),
        "allow_other_services": bool(int(row.get("allow_other_services") or 0)),
        "ticket_id": int(row["ticket_id"]) if row.get("ticket_id") is not None else None,
        "created_at": row.get("created_at"),
    }
