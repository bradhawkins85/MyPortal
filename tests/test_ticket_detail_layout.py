"""Static layout checks for the admin ticket detail page.

These tests protect the responsive column placement used by the admin ticket
detail page without needing a running browser or database-backed FastAPI app.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_admin_ticket_reply_stays_in_activity_column():
    """High-resolution layouts should keep replies in the third grid column."""
    template = _read(APP / "templates" / "admin" / "ticket_detail.html")

    assert '<div class="management__column management__column--content">' in template
    assert '<div class="management__column management__column--activity">' in template
    assert template.index('data-ticket-assets-card') < template.index(
        'management__column management__column--activity'
    )
    assert template.index('management__column management__column--activity') < template.index(
        "Add a reply"
    )
    assert template.index("Add a reply") < template.index("Conversation history")


def test_admin_ticket_grid_places_activity_below_assets_until_wide_layout():
    """Medium desktop layouts should place activity below content, not details."""
    css = _read(APP / "static" / "css" / "app.css")

    assert "@media (min-width: 961px) and (max-width: 1499px)" in css
    assert ".management__column--details {\n    grid-column: 1;\n    grid-row: 1 / span 2;" in css
    assert ".management__column--content {\n    grid-column: 2;\n    grid-row: 1;" in css
    assert (
        ".management__column--activity,\n"
        "  .management__column--conversation {\n"
        "    grid-column: 2;\n"
        "    grid-row: 2;"
    ) in css
    assert "@media (min-width: 1500px)" in css
    assert ".management__column--activity,\n  .management__column--conversation {\n    grid-column: 3;" in css
