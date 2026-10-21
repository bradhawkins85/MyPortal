"""Repository for global site settings (single-row configuration table)."""
from __future__ import annotations

from typing import Any, Mapping

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


async def get_tray_icon_tooltip_name() -> str | None:
    """Return the custom tray icon tooltip display name, or ``None`` if unset."""
    row = await db.fetch_one(
        "SELECT tray_icon_tooltip_name FROM site_settings WHERE id = 1",
        (),
    )
    if row is None:
        return None
    value = (
        row.get("tray_icon_tooltip_name")
        if isinstance(row, Mapping)
        else row["tray_icon_tooltip_name"]
    )
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


async def set_tray_icon_tooltip_name(name: str | None) -> None:
    """Persist (or clear) the custom tray icon tooltip display name."""
    existing = await db.fetch_one(
        "SELECT id FROM site_settings WHERE id = 1",
        (),
    )
    if existing is None:
        await db.execute(
            "INSERT INTO site_settings (id, tray_icon_tooltip_name) VALUES (1, %s)",
            (name,),
        )
    else:
        await db.execute(
            "UPDATE site_settings SET tray_icon_tooltip_name = %s WHERE id = 1",
            (name,),
        )


async def get_next_ticket_number() -> int | None:
    """Return the configured next local ticket number, or ``None`` if unset."""
    row = await db.fetch_one(
        "SELECT next_ticket_number FROM site_settings WHERE id = 1",
        (),
    )
    if row is None:
        return None
    value: Any = row.get("next_ticket_number") if isinstance(row, Mapping) else row["next_ticket_number"]
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def set_next_ticket_number(number: int | None) -> None:
    """Persist the configured next local ticket number."""
    value = int(number) if number is not None else None
    if value is not None and value <= 0:
        raise ValueError("Next ticket number must be a positive integer.")
    existing = await db.fetch_one(
        "SELECT id FROM site_settings WHERE id = 1",
        (),
    )
    if existing is None:
        await db.execute(
            "INSERT INTO site_settings (id, next_ticket_number) VALUES (1, %s)",
            (value,),
        )
    else:
        await db.execute(
            "UPDATE site_settings SET next_ticket_number = %s WHERE id = 1",
            (value,),
        )


__all__ = [
    "get_pdf_cover_image",
    "set_next_ticket_number",
    "set_pdf_cover_image",
    "get_next_ticket_number",
    "get_tray_icon_path",
    "get_tray_icon_tooltip_name",
    "set_tray_icon_path",
    "set_tray_icon_tooltip_name",
]
