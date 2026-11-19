"""Ticket AI prompts include what MyPortal learned from technicians' ratings."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.modules.setdefault("magic", SimpleNamespace(Magic=lambda *a, **k: None, from_buffer=lambda *a, **k: ""))

from app.services import ai_text_learning
from app.services import ticket_reply_suggestions as replies_service
from app.services import tickets as tickets_service
from app.services.ai_prompt_security import UntrustedRecord, validate_references
from app.services.ai_text_learning import EMPTY, TextLearning

EXAMPLE = UntrustedRecord("rated-example:99", "a previous rated output", {"rating": "helpful", "text": "Good"},
                          ai_text_learning.EXAMPLE_ALLOWED_USE)
LEARNING = TextLearning(guidance=("GUIDANCE-LINE",), records=(EXAMPLE,))
TICKET = {"id": 5, "company_id": 2, "subject": "Printer issue", "description": "Jammed", "status": "resolved",
          "priority": "high"}
REPLIES = [{"id": 1, "author_id": 12, "body": "Replaced the toner.", "is_internal": False, "created_at": None}]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _assert_learned(prompt: str):
    trusted, untrusted = prompt.split("BEGIN_UNTRUSTED_RECORDS", 1)
    assert "GUIDANCE-LINE" in trusted
    assert "rated-example:99" in untrusted


RENDERERS = {
    "summary": lambda learning: tickets_service._render_prompt(TICKET, REPLIES, {}, learning=learning),
    "insights": lambda learning: tickets_service._render_insights_prompt(TICKET, REPLIES, {}, ("printer",),
                                                                         learning=learning),
    "resolution": lambda learning: tickets_service._render_resolution_prompt(TICKET, REPLIES, learning=learning),
    "reply": lambda learning: replies_service.build_reply_prompt(TICKET, [], learning=learning),
}


@pytest.mark.parametrize("name", sorted(RENDERERS))
def test_prompts_include_guidance_and_examples(name):
    _assert_learned(RENDERERS[name](LEARNING))


@pytest.mark.parametrize("name", sorted(RENDERERS))
def test_prompts_without_learning_are_unchanged(name):
    assert "GUIDANCE-LINE" not in RENDERERS[name](EMPTY)
    assert RENDERERS[name](EMPTY) == RENDERERS[name](TextLearning())


def test_insights_prompt_still_ends_with_the_tag_vocabulary():
    prompt = RENDERERS["insights"](LEARNING)
    assert prompt.index('"record_id":"rated-example:99"') < prompt.index('"record_id":"tag-vocabulary"')


def test_examples_are_dropped_before_ticket_content_when_the_prompt_is_too_long():
    long_ticket = {**TICKET, "description": "x" * (tickets_service._MAX_AI_PROMPT_CHARS - 2000)}
    big = UntrustedRecord("rated-example:99", "a previous rated output", {"text": "y" * 3000},
                          ai_text_learning.EXAMPLE_ALLOWED_USE)
    prompt = tickets_service._render_prompt(long_ticket, REPLIES, {},
                                            learning=TextLearning(guidance=("GUIDANCE-LINE",), records=(big,)))
    assert "rated-example:99" not in prompt
    assert "GUIDANCE-LINE" in prompt


@pytest.mark.anyio
async def test_example_citations_are_neutralised(monkeypatch):
    async def rated_examples(feature, company_id, *, exclude_subject_id, rating, limit, since):
        if rating != "up":
            return []
        return [{"output_id": 1, "output_text": "Restart it as in [kb:12] and [tickets:7].", "rating": "up",
                 "reason": None}]

    monkeypatch.setattr(ai_text_learning.quality_repo, "rated_examples", rated_examples)
    monkeypatch.setattr(ai_text_learning.quality_repo, "active_lessons", AsyncMock(return_value=[]))
    learning = await ai_text_learning.context_for("ticket_reply_suggestions", 2, subject_id=5)
    text = learning.records[0].content["text"]
    assert "[kb:12]" not in text and "[tickets:7]" not in text
    validate_references(text, set())  # nothing left that a draft could copy as a citation


@pytest.fixture
def captured_context(monkeypatch):
    calls = []

    async def context_for(feature, company_id, *, subject_id=None):
        calls.append((feature, company_id, subject_id))
        return LEARNING

    monkeypatch.setattr(ai_text_learning, "context_for", context_for)
    return calls


def _patch_ticket(monkeypatch, prompts):
    async def trigger(slug, payload, *, background=True, on_complete=None, **kwargs):
        prompts.append(payload["prompt"])
        return {"status": "queued"}

    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", AsyncMock())
    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", AsyncMock(return_value=TICKET))
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", AsyncMock(return_value=REPLIES))
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", AsyncMock())
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", trigger)
    monkeypatch.setattr(tickets_service.ai_consent, "is_ai_allowed_for_ticket", AsyncMock(return_value=True))
    monkeypatch.setattr(tickets_service, "get_preferred_tags", AsyncMock(return_value=[]))


@pytest.mark.anyio
@pytest.mark.parametrize("refresh,feature", [
    ("refresh_ticket_ai_summary", "ticket_ai_summary"),
    ("refresh_ticket_ai_insights", "ticket_ai_summary"),
    ("refresh_ticket_resolution_steps", "ticket_resolution_steps"),
])
async def test_refresh_paths_use_learning(monkeypatch, captured_context, refresh, feature):
    prompts = []
    _patch_ticket(monkeypatch, prompts)
    await getattr(tickets_service, refresh)(5)
    assert captured_context == [(feature, 2, 5)]
    _assert_learned(prompts[-1])


@pytest.mark.anyio
async def test_generation_survives_learning_errors(monkeypatch):
    prompts = []
    _patch_ticket(monkeypatch, prompts)
    monkeypatch.setattr(ai_text_learning.quality_repo, "active_lessons", AsyncMock(side_effect=RuntimeError("db")))
    await tickets_service.refresh_ticket_ai_summary(5)
    assert prompts and "Printer issue" in prompts[-1]


@pytest.mark.anyio
async def test_reply_suggestions_use_learning(monkeypatch, captured_context):
    prompts = []
    source = replies_service.ReplySource(reference="[KB:printer]", relationship_type="DIRECT_MATCH",
                                         source_type="knowledge_base", title="Printer", url="/kb/3",
                                         resolution="Replace toner")

    async def trigger(slug, payload, *, background=True, **kwargs):
        prompts.append(payload["prompt"])
        return {"status": "succeeded", "response": "Replace the toner [KB:printer]."}

    monkeypatch.setattr(replies_service.ai_consent, "is_ai_allowed_for_ticket", AsyncMock(return_value=True))
    monkeypatch.setattr(replies_service, "collect_reply_sources", AsyncMock(return_value=[source]))
    monkeypatch.setattr(replies_service.modules_service, "trigger_module", trigger)
    monkeypatch.setattr(replies_service.modules_service, "module_result_succeeded", lambda result: True)

    result = await replies_service.suggest_reply(TICKET, user={"id": 1}, memberships=[])
    assert result["draft"] == "Replace the toner [KB:printer]."
    assert captured_context == [("ticket_reply_suggestions", 2, 5)]
    _assert_learned(prompts[-1])
