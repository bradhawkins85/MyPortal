from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.services import m365_spam_purge as service


def test_build_content_match_query_combines_safe_structured_fields():
    query = service.build_content_match_query(
        sender="attacker@example.com",
        subject='Urgent "invoice"',
        received_from=date(2026, 2, 1),
        received_to=date(2026, 2, 4),
        advanced_query=None,
    )
    assert query == (
        '(Received:02/01/2026..02/04/2026) AND '
        '(From:"attacker@example.com") AND (Subject:"Urgent \\"invoice\\"")'
    )


def test_build_content_match_query_requires_meaningful_criteria():
    with pytest.raises(ValueError, match="criterion"):
        service.build_content_match_query(
            sender=None, subject=" ", received_from=None, received_to=None,
            advanced_query=None,
        )


def test_build_content_match_query_rejects_control_characters_in_advanced_query():
    with pytest.raises(ValueError, match="control"):
        service.build_content_match_query(
            sender=None, subject=None, received_from=None, received_to=None,
            advanced_query="From:valid@example.com\nOR Subject:unsafe",
        )


def test_removed_item_count_reads_purview_result_summary():
    assert service._removed_item_count({"Results": "Purge Type: HardDelete; Item count: 1,204"}) == 1204


def test_start_purge_requires_completed_non_empty_search(monkeypatch):
    async def fake_get(_request_id):
        return {"id": 7, "company_id": 2, "search_status": "completed", "matched_items": 0,
                "purge_status": "not_started"}

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    with pytest.raises(ValueError, match="no messages"):
        asyncio.run(service.start_purge(7, 2))


def test_start_purge_is_company_scoped(monkeypatch):
    async def fake_get(_request_id):
        return {"id": 7, "company_id": 99}

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    with pytest.raises(LookupError):
        asyncio.run(service.start_purge(7, 2))


def test_spam_purge_template_has_review_confirmation_and_live_refresh():
    source = open("app/templates/m365/spam_purge.html", encoding="utf-8").read()
    assert 'name="confirmation"' in source
    assert 'pattern="PURGE"' in source
    assert 'action="/m365/spam-purge/{{ item.id }}/retry"' in source
    assert "Retry search" in source
    assert "active_jobs" in source
    assert 'data-utc="{{ item.created_at }}"' in source
