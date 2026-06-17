from typing import Any

import pytest

from app.api.routes import tag_exclusions


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_remove_kb_article_tag_matches_display_tag_by_slug(monkeypatch):
    updates: dict[str, Any] = {}

    async def fake_get_article_by_id(article_id: int):
        assert article_id == 42
        return {
            "id": article_id,
            "ai_tags": ["Printer Offline", "VPN"],
            "excluded_ai_tags": [],
        }

    async def fake_update_article(article_id: int, **kwargs: Any):
        updates.update(kwargs)
        return {"id": article_id, **kwargs}

    monkeypatch.setattr(tag_exclusions.kb_repo, "get_article_by_id", fake_get_article_by_id)
    monkeypatch.setattr(tag_exclusions.kb_repo, "update_article", fake_update_article)

    result = await tag_exclusions.remove_kb_article_tag(
        42,
        tag_exclusions.RemoveTagRequest(tag_slug="printer-offline"),
        current_user={"id": 7},
    )

    assert result == {"success": True, "removed": "printer-offline", "remaining_tags": ["VPN"]}
    assert updates == {"ai_tags": ["VPN"], "excluded_ai_tags": ["printer-offline"]}


@pytest.mark.anyio
async def test_remove_kb_article_tag_deduplicates_existing_exclusion_by_slug(monkeypatch):
    updates: dict[str, Any] = {}

    async def fake_get_article_by_id(article_id: int):
        return {
            "id": article_id,
            "ai_tags": ["Printer Offline", "VPN"],
            "excluded_ai_tags": ["Printer Offline"],
        }

    async def fake_update_article(article_id: int, **kwargs: Any):
        updates.update(kwargs)
        return {"id": article_id, **kwargs}

    monkeypatch.setattr(tag_exclusions.kb_repo, "get_article_by_id", fake_get_article_by_id)
    monkeypatch.setattr(tag_exclusions.kb_repo, "update_article", fake_update_article)

    await tag_exclusions.remove_kb_article_tag(
        42,
        tag_exclusions.RemoveTagRequest(tag_slug="printer-offline"),
        current_user={"id": 7},
    )

    assert updates["ai_tags"] == ["VPN"]
    assert updates["excluded_ai_tags"] == ["Printer Offline"]


@pytest.mark.anyio
async def test_remove_ticket_tag_matches_display_tag_by_slug(monkeypatch):
    updates: dict[str, Any] = {}

    async def fake_get_ticket(ticket_id: int):
        assert ticket_id == 99
        return {"id": ticket_id, "ai_tags": ["Password Reset", "Laptop"]}

    async def fake_update_ticket(ticket_id: int, **kwargs: Any):
        updates.update(kwargs)
        return {"id": ticket_id, **kwargs}

    monkeypatch.setattr(tag_exclusions.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tag_exclusions.tickets_repo, "update_ticket", fake_update_ticket)

    result = await tag_exclusions.remove_ticket_tag(
        99,
        tag_exclusions.RemoveTagRequest(tag_slug="password-reset"),
        current_user={"id": 7},
    )

    assert result == {"success": True, "removed": "password-reset", "remaining_tags": ["Laptop"]}
    assert updates == {"ai_tags": ["Laptop"]}
