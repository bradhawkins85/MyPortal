"""Repository for global site settings (single-row configuration table)."""
from __future__ import annotations

from typing import Mapping

from app.core.database import db


async def get_pdf_cover_image() -> str | None:
    """Return the stored path for the PDF cover image, or ``None`` if unset."""
    row = await db.fetch_one(
        "SELECT pdf_cover_image FROM site_settings WHERE id = 1",
        (),
    )
    if row is None:
        return None
    value = row.get("pdf_cover_image") if isinstance(row, Mapping) else row["pdf_cover_image"]
    return str(value) if value else None


async def set_pdf_cover_image(path: str | None) -> None:
    """Persist (or clear) the PDF cover image path in site_settings.

    Uses a delete-then-insert pattern for MySQL/SQLite compatibility.
    """
    existing = await db.fetch_one(
        "SELECT id FROM site_settings WHERE id = 1",
        (),
    )
    if existing is None:
        await db.execute(
            "INSERT INTO site_settings (id, pdf_cover_image) VALUES (1, %s)",
            (path,),
        )
    else:
        await db.execute(
            "UPDATE site_settings SET pdf_cover_image = %s WHERE id = 1",
            (path,),
        )


async def get_tray_icon_path() -> str | None:
    """Return the stored path for the custom tray icon, or ``None`` if unset."""
    row = await db.fetch_one(
        "SELECT tray_icon_path FROM site_settings WHERE id = 1",
        (),
    )
    if row is None:
        return None
    value = row.get("tray_icon_path") if isinstance(row, Mapping) else row["tray_icon_path"]
    return str(value) if value else None


async def set_tray_icon_path(path: str | None) -> None:
    """Persist (or clear) the custom tray icon path in site_settings.

    Uses a delete-then-insert pattern for MySQL/SQLite compatibility.
    """
    existing = await db.fetch_one(
        "SELECT id FROM site_settings WHERE id = 1",
        (),
    )
    if existing is None:
        await db.execute(
            "INSERT INTO site_settings (id, tray_icon_path) VALUES (1, %s)",
            (path,),
        )
    else:
        await db.execute(
            "UPDATE site_settings SET tray_icon_path = %s WHERE id = 1",
            (path,),
        )


__all__ = [
    "get_pdf_cover_image",
    "set_pdf_cover_image",
    "get_tray_icon_path",
    "set_tray_icon_path",
]
