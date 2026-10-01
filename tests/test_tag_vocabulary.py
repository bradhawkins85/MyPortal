"""Tests for the controlled AI tag vocabulary (synonyms and preferred tags)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import tag_synonyms as tag_synonyms_routes
from app.repositories import knowledge_base as kb_repo
from app.services import knowledge_base as kb_service
from app.services import tagging
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_vocabulary_cache():
    tagging.clear_tag_vocabulary_cache()
    yield
    tagging.clear_tag_vocabulary_cache()


SYNONYMS = {"outlook-crashing": "outlook-crash", "ms-outlook": "outlook"}


def test_apply_tag_synonyms_merges_variants_and_deduplicates():
    tags = ["outlook-crashing", "outlook-crash", "ms-outlook", "vpn"]
    assert tagging.apply_tag_synonyms(tags, SYNONYMS) == ["outlook-crash", "outlook", "vpn"]


def test_canonicalise_slug_follows_chains_and_stops_on_cycles():
    assert tagging.canonicalise_slug("a", {"a": "b", "b": "c"}) == "c"
    assert tagging.canonicalise_slug("a", {"a": "b", "b": "a"}) == "b"
    assert tagging.canonicalise_slug("vpn", SYNONYMS) == "vpn"


def test_apply_tag_text_synonyms_keeps_display_form():
    tags = ["Outlook Crashing", "outlook crash", "network printer"]
    assert tagging.apply_tag_text_synonyms(tags, SYNONYMS) == ["outlook crash", "network printer"]


def test_rank_preferred_tags_counts_documents_and_applies_synonyms():
    tag_lists = [
        ["outlook-crash", "vpn"],
        ["outlook-crashing", "outlook-crash"],  # counted once after merging
        ["Outlook Crash", "printer"],
        ["vpn", "normal"],
        ["printer-offline"],
    ]
    ranked = tagging.rank_preferred_tags(tag_lists, SYNONYMS, {"normal"})
    assert ranked == ["outlook-crash", "vpn"]


def test_ticket_tag_prompt_includes_preferred_tags():
    ticket = {"id": 4, "subject": "Outlook keeps crashing", "description": "Crashes on start."}
    prompt = tickets_service._render_tags_prompt(ticket, [], {}, ["outlook-crash", "vpn"])
    assert '"preferred_tags":["outlook-crash","vpn"]' in prompt
    assert "tag-vocabulary record" in prompt

    without = tickets_service._render_tags_prompt(ticket, [], {})
    assert "preferred_tags" not in without


def test_insights_prompt_includes_preferred_tags():
    ticket = {"id": 4, "subject": "Outlook keeps crashing", "description": "Crashes on start."}
    prompt = tickets_service._render_insights_prompt(ticket, [], {}, ["outlook-crash"])
    assert '"preferred_tags":["outlook-crash"]' in prompt
    assert "tag-vocabulary record" in prompt
    assert "preferred_tags" not in tickets_service._render_insights_prompt(ticket, [], {})


def test_kb_tag_prompt_includes_preferred_tags_as_text():
    prompt = kb_service._render_ai_tag_prompt("Outlook", None, [], "Fix crashes", ["outlook-crash"])
    assert '"preferred_tags":["outlook crash"]' in prompt


@pytest.mark.anyio
async def test_extract_ticket_tags_merges_synonyms(monkeypatch):
    async def fake_excluded():
        return set()

    async def fake_synonyms():
        return SYNONYMS

    monkeypatch.setattr(tickets_service, "get_all_excluded_tags", fake_excluded)
    monkeypatch.setattr(tickets_service, "get_tag_synonym_map", fake_synonyms)

    tags = await tickets_service._extract_tags(
        '{"tags": ["outlook-crashing", "ms-outlook", "outlook-crash"]}', {"subject": "x"}, []
    )
    assert tags == ["outlook-crash", "outlook"]


@pytest.mark.anyio
async def test_relevant_articles_match_slug_and_display_forms_with_synonyms():
    articles = [
        {"id": 1, "ai_tags": ["Outlook Crash"], "excluded_ai_tags": [], "updated_at_utc": None},
        {"id": 2, "ai_tags": ["vpn"], "excluded_ai_tags": [], "updated_at_utc": None},
    ]
    with patch.object(kb_repo, "list_articles", new_callable=AsyncMock) as mock_list, patch.object(
        tagging, "get_tag_synonym_map", new_callable=AsyncMock
    ) as mock_synonyms:
        mock_list.return_value = articles
        mock_synonyms.return_value = SYNONYMS
        results = await kb_repo.find_relevant_articles_for_ticket(["outlook-crashing"])
    assert [article["id"] for article in results] == [1]


def test_replace_slug_tags_rewrites_and_deduplicates():
    assert tagging._replace_slug_tags(["Outlook Crashing", "vpn"], "outlook-crashing", "outlook crash") == [
        "outlook crash",
        "vpn",
    ]
    assert tagging._replace_slug_tags(["outlook-crashing", "outlook-crash"], "outlook-crashing", "outlook-crash") == [
        "outlook-crash"
    ]
    assert tagging._replace_slug_tags(["vpn"], "outlook-crashing", "outlook-crash") is None


@pytest.mark.anyio
async def test_merge_existing_tags_rewrites_tickets_and_articles(monkeypatch):
    ticket_writes: list[tuple[int, list[str]]] = []
    article_writes: list[tuple[int, list[str], list[str]]] = []
    enqueued: list[tuple[str, int]] = []
    repo = tagging.tag_synonyms_repo

    async def fake_tickets(slug_text, display_text):
        assert (slug_text, display_text) == ("outlook-crashing", "outlook crashing")
        return [
            {"id": 1, "ai_tags": ["outlook-crashing", "vpn"]},
            {"id": 2, "ai_tags": ["outlook-crashing-badly"]},  # LIKE false positive
        ]

    async def fake_articles(slug_text, display_text):
        return [{"id": 9, "ai_tags": ["outlook crashing"], "manual_ai_tags": ["email"]}]

    async def fake_set_ticket(ticket_id, tags):
        ticket_writes.append((ticket_id, tags))

    async def fake_set_article(article_id, ai_tags, manual):
        article_writes.append((article_id, ai_tags, manual))

    async def fake_enqueue(source_type, source_id, **kwargs):
        enqueued.append((source_type, source_id))

    monkeypatch.setattr(repo, "list_tickets_with_tag_text", fake_tickets)
    monkeypatch.setattr(repo, "list_articles_with_tag_text", fake_articles)
    monkeypatch.setattr(repo, "set_ticket_tags", fake_set_ticket)
    monkeypatch.setattr(repo, "set_article_tags", fake_set_article)
    monkeypatch.setattr("app.services.rag_outbox.enqueue", fake_enqueue)

    result = await tagging.merge_existing_tags("outlook-crashing", "outlook-crash")

    assert result == {"tickets_updated": 1, "articles_updated": 1}
    assert ticket_writes == [(1, ["outlook-crash", "vpn"])]
    assert article_writes == [(9, ["outlook crash"], ["email"])]
    assert enqueued == [("tickets", 1), ("knowledge_base", 9)]


@pytest.mark.anyio
async def test_create_synonym_route_resolves_chain_and_repoints(monkeypatch):
    repo = tag_synonyms_routes.tag_synonyms_repo
    calls: dict[str, Any] = {}

    async def fake_map():
        return {"outlook-crashed": "outlook-crash"}

    async def fake_add(variant, canonical, created_by):
        calls["add"] = (variant, canonical, created_by)
        return {"id": 3, "variant_slug": variant, "canonical_slug": canonical}

    async def fake_repoint(old, new):
        calls["repoint"] = (old, new)
        return 0

    monkeypatch.setattr(repo, "get_synonym_map", fake_map)
    monkeypatch.setattr(repo, "add_synonym", fake_add)
    monkeypatch.setattr(repo, "repoint_canonical", fake_repoint)

    result = await tag_synonyms_routes.create_tag_synonym(
        tag_synonyms_routes.TagSynonymCreate(
            variant="Outlook Crashing", canonical="outlook-crashed", apply_to_existing=False
        ),
        current_user={"id": 5},
    )

    assert calls["add"] == ("outlook-crashing", "outlook-crash", 5)
    assert calls["repoint"] == ("outlook-crashing", "outlook-crash")
    assert result["tickets_updated"] == 0


@pytest.mark.anyio
async def test_create_synonym_route_rejects_cycles_and_duplicates(monkeypatch):
    repo = tag_synonyms_routes.tag_synonyms_repo

    async def fake_map():
        return {"outlook-crashing": "outlook-crash"}

    monkeypatch.setattr(repo, "get_synonym_map", fake_map)

    with pytest.raises(HTTPException) as cycle:
        await tag_synonyms_routes.create_tag_synonym(
            tag_synonyms_routes.TagSynonymCreate(variant="outlook-crash", canonical="outlook-crashing"),
            current_user={"id": 5},
        )
    assert cycle.value.status_code == 400

    with pytest.raises(HTTPException) as duplicate:
        await tag_synonyms_routes.create_tag_synonym(
            tag_synonyms_routes.TagSynonymCreate(variant="outlook-crashing", canonical="crash"),
            current_user={"id": 5},
        )
    assert duplicate.value.status_code == 409


@pytest.mark.anyio
async def test_synonym_map_is_cached_until_cleared(monkeypatch):
    calls = 0

    async def fake_map():
        nonlocal calls
        calls += 1
        return dict(SYNONYMS)

    monkeypatch.setattr(tagging.db, "is_connected", lambda: True)
    monkeypatch.setattr(tagging.tag_synonyms_repo, "get_synonym_map", fake_map)

    assert await tagging.get_tag_synonym_map() == SYNONYMS
    assert await tagging.get_tag_synonym_map() == SYNONYMS
    assert calls == 1
    tagging.clear_tag_vocabulary_cache()
    await tagging.get_tag_synonym_map()
    assert calls == 2
