"""Rating AI reply suggestions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.features.tickets import admin_routes

TICKET = {"id": 4, "company_id": 2, "subject": "Printer"}


@pytest.fixture
def route(monkeypatch):
    calls = []

    async def require(_request):
        return {"id": 8}, None

    async def record_output(**kwargs):
        calls.append(kwargs)
        return 31

    monkeypatch.setattr(admin_routes, "_main", lambda: SimpleNamespace(_require_helpdesk_page=require))
    monkeypatch.setattr(admin_routes.tickets_repo, "get_ticket", AsyncMock(return_value=TICKET))
    monkeypatch.setattr(admin_routes.reply_suggestion_service, "suggest_reply", AsyncMock(return_value={
        "draft": "Replace the toner [KB:printer].", "sources": [], "model": "llama3"}))
    monkeypatch.setattr(admin_routes.quality_repo, "record_output", record_output)
    return calls


def _request():
    return SimpleNamespace(state=SimpleNamespace(available_companies=[]))


@pytest.mark.anyio
async def test_suggestion_is_recorded_for_rating(route):
    response = await admin_routes.admin_suggest_ticket_reply(4, _request())
    body = json.loads(response.body)
    assert body["draft"] == "Replace the toner [KB:printer]."
    assert body["quality_response_id"] == 31
    assert route == [{"feature": "ticket_reply_suggestions", "user_id": 8, "company_id": 2,
                      "subject_type": "ticket", "subject_id": 4, "output_text": "Replace the toner [KB:printer].",
                      "model": "llama3"}]


@pytest.mark.anyio
async def test_recording_failure_still_returns_the_draft(route, monkeypatch):
    monkeypatch.setattr(admin_routes.quality_repo, "record_output", AsyncMock(side_effect=RuntimeError("db")))
    body = json.loads((await admin_routes.admin_suggest_ticket_reply(4, _request())).body)
    assert body["draft"] == "Replace the toner [KB:printer]."
    assert body["quality_response_id"] is None


def test_rating_form_sits_outside_the_reply_form():
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")
    start = template.index("data-ticket-suggest-reply-rating")
    assert 'ai_rating("pending", label="Suggested reply", csrf_token=csrf_token)' in template[start:start + 400]
    # The reply form is the last <form ...> opened before the suggestion panel; it must be closed first.
    panel = template.index("data-ticket-suggest-reply-panel")
    reply_form_start = template.rindex("<form", 0, panel)
    reply_form_end = template.index("</form>", reply_form_start)
    assert reply_form_end < start


def test_script_reveals_the_rating_with_the_returned_id():
    script = Path("app/static/js/ticket_detail.js").read_text(encoding="utf-8")
    assert "[data-ticket-suggest-reply-rating]" in script
    assert "payload.quality_response_id" in script
    assert "window.MyPortalAiRating" in script
