import pytest

from app.repositories import rag_relationships as rag_relationships_repo
from app.services.rag_relationships import (
    _relationship_response_payload,
    parse_relationship_response,
)


def test_parse_relationship_response_stores_positive_match():
    parsed = parse_relationship_response(
        '{"relationship":"DIRECT_MATCH","confidence":0.94,"score":0.93,"reason":"same fix","supporting_excerpt":"replace CMOS"}',
        min_score=0.55,
    )

    assert parsed["relationship_type"] == "DIRECT_MATCH"
    assert parsed["match_status"] == "MATCH"
    assert parsed["relevance_score"] == 0.93
    assert parsed["confidence"] == 0.94


def test_parse_relationship_response_stores_negative_no_match():
    parsed = parse_relationship_response(
        {"relationship": "RELATED", "confidence": 0.5, "score": 0.2},
        min_score=0.55,
    )

    assert parsed["relationship_type"] == "NOT_RELEVANT"
    assert parsed["match_status"] == "NO_MATCH"


def test_parse_relationship_response_accepts_fenced_json():
    parsed = parse_relationship_response(
        '```json\n{"relationship":"RELATED","confidence":0.8,"score":0.75}\n```',
        min_score=0.55,
    )

    assert parsed["relationship_type"] == "RELATED"
    assert parsed["match_status"] == "MATCH"


def test_relationship_response_payload_reads_module_response_key():
    raw = _relationship_response_payload(
        {
            "status": "succeeded",
            "response": {
                "response": '{"relationship":"DUPLICATE","confidence":0.9,"score":0.88}'
            },
        }
    )

    parsed = parse_relationship_response(raw, min_score=0.55)

    assert parsed["relationship_type"] == "DUPLICATE"
    assert parsed["match_status"] == "MATCH"


def test_parse_relationship_response_empty_error_is_actionable():
    try:
        parse_relationship_response("", min_score=0.55)
    except ValueError as exc:
        assert "empty response" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError(
            "Expected empty relationship responses to fail with a clear message"
        )


def test_relationship_prompt_prefers_ticket_as_document_a_for_mixed_pair():
    from app.services.rag_relationships import _prompt

    asset = {
        "source_type": "assets",
        "source_id": 42,
        "title": "Laptop Asset",
        "content": "Device inventory details",
    }
    ticket = {
        "source_type": "tickets",
        "source_id": 1001,
        "title": "Laptop will not boot",
        "content": "Customer reports startup failure",
    }

    prompt = _prompt(asset, ticket)

    document_a = prompt.split("----------------------------", 1)[0]
    document_b = prompt.split("----------------------------", 1)[1]
    assert "Document A\ntickets #1001" in document_a
    assert "Document B\nassets #42" in document_b


def test_relationship_prompt_keeps_non_ticket_order():
    from app.services.rag_relationships import _prompt

    asset = {
        "source_type": "assets",
        "source_id": 42,
        "title": "Laptop Asset",
        "content": "",
    }
    article = {
        "source_type": "knowledge_base",
        "source_id": 9,
        "title": "Boot guide",
        "content": "",
    }

    prompt = _prompt(asset, article)

    document_a = prompt.split("----------------------------", 1)[0]
    assert "Document A\nassets #42" in document_a


def test_relationship_queue_priority_prefers_ticket_pairs():
    from app.services.rag_relationships import _relationship_queue_priority

    ticket = {"source_type": "tickets"}
    asset = {"source_type": "assets"}
    article = {"source_type": "knowledge_base"}

    assert _relationship_queue_priority(asset, ticket) > _relationship_queue_priority(
        asset, article
    )
    assert _relationship_queue_priority(ticket, article) > _relationship_queue_priority(
        asset, article
    )


@pytest.mark.anyio
async def test_matching_paused_quotes_reserved_key_column(monkeypatch):
    executed: list[tuple[str, tuple]] = []

    async def fake_fetch_one(query, params=()):
        executed.append((query, params))
        return {"value": "1"}

    monkeypatch.setattr(rag_relationships_repo.db, "fetch_one", fake_fetch_one)

    assert await rag_relationships_repo.matching_paused() is True
    query, params = executed[0]
    assert "WHERE `key` = 'paused'" in query
    assert "WHERE key = 'paused'" not in query
    assert params == ()


@pytest.mark.anyio
async def test_set_matching_paused_quotes_reserved_key_column_for_sqlite(monkeypatch):
    executed: list[tuple[str, tuple]] = []

    def fake_is_sqlite():
        return True

    async def fake_execute(query, params=()):
        executed.append((query, params))

    monkeypatch.setattr(rag_relationships_repo.db, "is_sqlite", fake_is_sqlite)
    monkeypatch.setattr(rag_relationships_repo.db, "execute", fake_execute)

    await rag_relationships_repo.set_matching_paused(True)
    query, params = executed[0]
    assert "INSERT INTO rag_matching_state (`key`, value, updated_at)" in query
    assert "ON CONFLICT(`key`)" in query
    assert params == ("1",)
