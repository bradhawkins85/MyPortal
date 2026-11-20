"""Rating the AI summary and AI resolution steps on ticket pages."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import ai_text_outputs

SUMMARY = {"id": 7, "company_id": 2, "ai_summary": "Printer fixed.", "ai_summary_status": "succeeded",
           "ai_summary_model": "llama3", "resolution_steps": "<ol><li>Replace toner</li></ol>",
           "resolution_steps_source": "ai_generated", "resolution_steps_model": "llama3"}


@pytest.fixture
def recorded(monkeypatch):
    calls = []

    async def record_output(**kwargs):
        calls.append(kwargs)
        return {"ticket_ai_summary": 11, "ticket_resolution_steps": 12}[kwargs["feature"]]

    async def user_rating(response_id, user_id):
        return "up" if (response_id, user_id) == (11, 5) else None

    monkeypatch.setattr(ai_text_outputs.quality_repo, "record_output", record_output)
    monkeypatch.setattr(ai_text_outputs.quality_repo, "user_rating", user_rating)
    monkeypatch.setattr(ai_text_outputs.ai_consent, "is_ai_allowed_for_ticket", AsyncMock(return_value=True))
    return calls


@pytest.mark.anyio
async def test_succeeded_summary_and_ai_steps_are_rateable(recorded):
    ids = await ai_text_outputs.ticket_rating_ids(SUMMARY, viewer_id=5)
    assert ids == {"summary": 11, "summary_rating": "up", "resolution_steps": 12, "resolution_steps_rating": None}
    assert recorded[0] == {"feature": "ticket_ai_summary", "user_id": None, "company_id": 2,
                           "subject_type": "ticket", "subject_id": 7, "output_text": "Printer fixed.",
                           "model": "llama3"}
    assert recorded[1]["feature"] == "ticket_resolution_steps"
    assert recorded[1]["output_text"] == "<ol><li>Replace toner</li></ol>"


@pytest.mark.anyio
@pytest.mark.parametrize("overrides", [
    {"ai_summary_status": "error"},
    {"ai_summary": "  "},
    {"ai_summary_status": "queued"},
])
async def test_summary_not_rateable_unless_succeeded_with_text(recorded, overrides):
    ids = await ai_text_outputs.ticket_rating_ids({**SUMMARY, **overrides}, viewer_id=5)
    assert ids["summary"] is None


@pytest.mark.anyio
async def test_manually_edited_steps_are_not_ai_output(recorded):
    ids = await ai_text_outputs.ticket_rating_ids({**SUMMARY, "resolution_steps_source": "manually_edited"},
                                                  viewer_id=5)
    assert ids["resolution_steps"] is None
    assert [call["feature"] for call in recorded] == ["ticket_ai_summary"]


@pytest.mark.anyio
async def test_errors_hide_the_controls(monkeypatch):
    monkeypatch.setattr(ai_text_outputs.quality_repo, "record_output", AsyncMock(side_effect=RuntimeError("db")))
    assert await ai_text_outputs.ticket_rating_ids(SUMMARY, viewer_id=5) == {
        "summary": None, "summary_rating": None, "resolution_steps": None, "resolution_steps_rating": None}


def test_admin_ticket_page_shows_rating_controls():
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")
    assert '{% from "macros/ai_rating.html" import ai_rating %}' in template
    assert "ai_rating(ai_rating_ids.summary, ai_rating_ids.summary_rating" in template
    assert 'label="AI summary"' in template
    assert "ai_rating(ai_rating_ids.resolution_steps, ai_rating_ids.resolution_steps_rating" in template
    assert 'label="AI resolution steps"' in template


def test_portal_ticket_page_shows_rating_for_ai_steps():
    template = Path("app/templates/tickets/detail.html").read_text(encoding="utf-8")
    assert "ai_rating(ai_rating_ids.resolution_steps, ai_rating_ids.resolution_steps_rating" in template


def test_both_ticket_pages_pass_rating_ids():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert source.count('"ai_rating_ids": await ai_text_outputs.ticket_rating_ids(') == 2


@pytest.mark.anyio
async def test_opted_out_tickets_are_not_recorded(recorded, monkeypatch):
    monkeypatch.setattr(ai_text_outputs.ai_consent, "is_ai_allowed_for_ticket", AsyncMock(return_value=False))
    ids = await ai_text_outputs.ticket_rating_ids(SUMMARY, viewer_id=5)
    assert ids["summary"] is None and ids["resolution_steps"] is None
    assert recorded == []
