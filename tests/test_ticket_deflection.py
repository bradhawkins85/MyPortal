from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.features.tickets import portal_routes
from app.services import ticket_deflection


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


USER = {"id": 7, "company_id": 3}


def _candidate(source_type: str, source_id: Any) -> dict[str, Any]:
    return {"source_type": source_type, "source_id": source_id, "excerpt": "secret note"}


def _patch(monkeypatch, *, candidates, articles=None, tickets=None, watchers=()):
    retrieve = AsyncMock(return_value=candidates)
    monkeypatch.setattr(ticket_deflection.rag_retrieval, "retrieve_candidates", retrieve)
    articles = articles or {}
    tickets = tickets or {}
    monkeypatch.setattr(
        ticket_deflection.kb_repo, "get_article_by_id", AsyncMock(side_effect=lambda i: articles.get(i))
    )
    monkeypatch.setattr(
        ticket_deflection.tickets_repo, "get_ticket", AsyncMock(side_effect=lambda i: tickets.get(i))
    )
    monkeypatch.setattr(
        ticket_deflection.tickets_repo,
        "is_ticket_watcher",
        AsyncMock(side_effect=lambda ticket_id, user_id: ticket_id in watchers),
    )
    return retrieve


def test_build_query_strips_markup():
    query = ticket_deflection.build_query("Printer", "<p>Jams &amp; <b>smudges</b></p>")
    assert query == "Printer Jams & smudges"


@pytest.mark.anyio
async def test_short_drafts_do_not_retrieve(monkeypatch):
    retrieve = _patch(monkeypatch, candidates=[])
    assert await ticket_deflection.suggest_for_new_ticket("VPN", "", USER) == []
    retrieve.assert_not_awaited()


@pytest.mark.anyio
async def test_retrieves_kb_and_tickets_without_reranking(monkeypatch):
    retrieve = _patch(monkeypatch, candidates=[])
    await ticket_deflection.suggest_for_new_ticket(
        "VPN keeps disconnecting", "since this morning", USER, active_company_id=3
    )
    kwargs = retrieve.await_args.kwargs
    assert tuple(kwargs["source_filters"]) == ("knowledge_base", "tickets")
    assert kwargs["rerank"] is False
    assert kwargs["active_company_id"] == 3


@pytest.mark.anyio
async def test_returns_published_articles_and_visible_resolved_tickets(monkeypatch):
    _patch(
        monkeypatch,
        candidates=[
            _candidate("knowledge_base", "10"),
            _candidate("tickets", "20"),  # open: skipped
            _candidate("tickets", "21"),  # other company, not watcher: skipped
            _candidate("tickets", "22"),  # requester
            _candidate("tickets", "23"),  # watcher
            _candidate("knowledge_base", "11"),  # beyond limit
        ],
        articles={
            10: {"id": 10, "slug": "vpn-reset", "title": "Reset VPN", "summary": "<p>Steps</p>", "is_published": True},
            11: {"id": 11, "slug": "other", "title": "Other", "is_published": True},
        },
        tickets={
            20: {"id": 20, "subject": "Open", "status": "open", "requester_id": 7, "company_id": 3},
            21: {"id": 21, "subject": "Hidden", "status": "resolved", "requester_id": 9, "company_id": 4},
            22: {"id": 22, "subject": "My VPN", "status": "Resolved", "requester_id": 7, "company_id": 3},
            23: {"id": 23, "subject": "Watched VPN", "status": "closed", "requester_id": 9, "company_id": 4},
        },
        watchers={23},
    )
    suggestions = await ticket_deflection.suggest_for_new_ticket(
        "VPN keeps disconnecting", "", USER, company_ticket_ids={3}
    )
    assert [(s["type"], s["id"]) for s in suggestions] == [
        ("knowledge_base", 10),
        ("ticket", 22),
        ("ticket", 23),
    ]
    assert suggestions[0]["url"] == "/knowledge-base/articles/vpn-reset"
    assert suggestions[0]["summary"] == "Steps"
    assert suggestions[1]["url"] == "/tickets/22"
    assert all("secret" not in s["summary"] for s in suggestions)


@pytest.mark.anyio
async def test_company_ticket_access_allows_company_tickets(monkeypatch):
    _patch(
        monkeypatch,
        candidates=[_candidate("tickets", "21")],
        tickets={21: {"id": 21, "subject": "Team VPN", "status": "resolved", "requester_id": 9, "company_id": 4}},
    )
    hidden = await ticket_deflection.suggest_for_new_ticket("VPN keeps disconnecting", "", USER)
    shown = await ticket_deflection.suggest_for_new_ticket(
        "VPN keeps disconnecting", "", USER, company_ticket_ids={4}
    )
    assert hidden == []
    assert [s["id"] for s in shown] == [21]


def _json_request(path: str, payload: Any) -> Request:
    body = json.dumps(payload).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b""}
    return Request(scope, receive)


@pytest.mark.anyio
async def test_suggestions_route_passes_portal_scope(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_require_menu_page_access", AsyncMock(return_value=(USER, None)))
    monkeypatch.setattr(main, "_has_admin_technician_access", AsyncMock(return_value=False))
    monkeypatch.setattr(main, "_has_menu_page_access", AsyncMock(return_value=False))
    suggest = AsyncMock(return_value=[{"type": "ticket", "id": 1}])
    monkeypatch.setattr(portal_routes.ticket_deflection, "suggest_for_new_ticket", suggest)

    response = await portal_routes.portal_ticket_suggestions(
        _json_request("/tickets/suggestions", {"subject": "VPN", "description": "drops"})
    )

    assert json.loads(response.body) == {"suggestions": [{"type": "ticket", "id": 1}]}
    assert suggest.await_args.args[:2] == ("VPN", "drops")
    assert suggest.await_args.kwargs["full_ticket_access"] is False
    assert suggest.await_args.kwargs["company_ticket_ids"] == set()


@pytest.mark.anyio
async def test_feedback_route_records_audit(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_require_menu_page_access", AsyncMock(return_value=(USER, None)))
    record = AsyncMock()
    monkeypatch.setattr(portal_routes.audit_service, "record", record)

    bad = await portal_routes.portal_ticket_suggestion_feedback(
        _json_request("/tickets/suggestions/feedback", {"type": "chats", "id": 1})
    )
    ok = await portal_routes.portal_ticket_suggestion_feedback(
        _json_request("/tickets/suggestions/feedback", {"type": "knowledge_base", "id": 10, "helpful": True})
    )

    assert bad.status_code == 400
    assert ok.status_code == 200
    record.assert_awaited_once()
    assert record.await_args.kwargs["entity_id"] == 10
    assert record.await_args.kwargs["metadata"] == {"helpful": True}
