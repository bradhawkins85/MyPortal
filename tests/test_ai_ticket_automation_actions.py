import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services import modules


def _enable_ollama(monkeypatch, response):
    monkeypatch.setattr(
        modules.module_repo,
        "get_module",
        AsyncMock(return_value={"slug": "ollama", "enabled": True, "settings": {}}),
    )
    invoke_ai = AsyncMock(return_value={"status": "succeeded", "response": response})
    monkeypatch.setattr(modules, "_invoke_ollama", invoke_ai)
    return invoke_ai


def _patch_ticket_writes(monkeypatch):
    update = AsyncMock()
    create_reply = AsyncMock(return_value={"id": 900})
    emit = AsyncMock()
    monkeypatch.setattr("app.repositories.tickets.update_ticket", update)
    monkeypatch.setattr("app.repositories.tickets.create_reply", create_reply)
    monkeypatch.setattr("app.services.tickets.emit_ticket_updated_event", emit)
    return update, create_reply, emit


COMPANY_SCOPE = json.dumps(
    {"version": 1, "visibility": "company", "company_ids": [5], "user_ids": [], "required_any": []}
)


@pytest.fixture(autouse=True)
def _rag_enabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.component_availability.rag_available", lambda: True
    )


def _relationship_row(**overrides):
    row = {
        "target_available": 1,
        "target_company_id": 5,
        "permission_scope_json": COMPANY_SCOPE,
        "source_type": "tickets",
        "source_id": "11",
        "title": "Printer jams on duplex",
        "url": None,
        "metadata_json": "{}",
        "relationship_type": "DUPLICATE",
        "confidence": 0.9,
    }
    row.update(overrides)
    return row


def _patch_relationships(monkeypatch, rows):
    monkeypatch.setattr(
        "app.repositories.rag_index.get_document_by_source",
        AsyncMock(return_value={"id": 77}),
    )
    monkeypatch.setattr("app.services.rag_index.embedding_model", lambda: "model")
    load = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        "app.services.rag_relationships.load_evidence_for_document", load
    )
    return load


TICKET = {
    "id": 42,
    "ticket_number": "TKT-42",
    "company_id": 5,
    "subject": "Printer issue",
    "description": "<p>The upstairs printer jams on duplex.</p>",
    "category": None,
    "priority": "normal",
}


def test_ai_classify_sets_validated_category_priority_and_duplicate_note(monkeypatch):
    duplicate = {
        "id": 11,
        "ticket_number": "TKT-11",
        "company_id": 5,
        "subject": "Printer jams on duplex",
        "description": "Same printer jams",
    }
    tickets = {42: TICKET, 11: duplicate}
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket",
        AsyncMock(side_effect=lambda ticket_id: tickets.get(ticket_id)),
    )
    _patch_relationships(monkeypatch, [_relationship_row()])
    invoke_ai = _enable_ollama(
        monkeypatch,
        {
            "response": json.dumps(
                {"category": "printing", "priority": "high", "duplicate_of": 11}
            )
        },
    )
    update, create_reply, emit = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(
        modules._invoke_ai_classify_ticket(
            {}, {"ticket_id": 42, "allowed_categories": "Networking, Printing"}
        )
    )

    prompt = invoke_ai.await_args.args[1]["prompt"]
    assert '["Networking", "Printing"]' in prompt
    assert "<p>" not in prompt
    update.assert_awaited_once_with(42, category="Printing", priority="high")
    note = create_reply.await_args.kwargs
    assert note["is_internal"] is True
    assert 'href="/admin/tickets/11"' in note["body"]
    assert "#TKT-11" in note["body"]
    assert result["updated_fields"] == ["category", "priority"]
    assert result["duplicate_of"] == 11
    emit.assert_awaited_once()


@pytest.mark.parametrize(
    "response",
    [
        {"category": "Invented", "priority": None, "duplicate_of": None},
        {"category": None, "priority": "critical", "duplicate_of": None},
        {"category": None, "priority": None, "duplicate_of": 999},
        {"category": None, "priority": None},
        "not json",
    ],
)
def test_ai_classify_rejects_values_outside_allowed_lists(monkeypatch, response):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket", AsyncMock(return_value=TICKET)
    )
    _patch_relationships(monkeypatch, [])
    _enable_ollama(
        monkeypatch,
        {"response": response if isinstance(response, str) else json.dumps(response)},
    )
    update, create_reply, _ = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(
        modules._invoke_ai_classify_ticket(
            {}, {"ticket_id": 42, "allowed_categories": ["Printing"]}
        )
    )

    assert result["status"] == "error"
    assert "invalid classification" in result["error"]
    update.assert_not_awaited()
    create_reply.assert_not_awaited()


def test_ai_classify_keeps_existing_category_and_uses_categories_in_use(monkeypatch):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket",
        AsyncMock(return_value={**TICKET, "category": "Hardware"}),
    )
    categories = AsyncMock(return_value=["Printing"])
    monkeypatch.setattr("app.repositories.tickets.list_ticket_categories", categories)
    invoke_ai = _enable_ollama(
        monkeypatch,
        {"response": '{"category": null, "priority": "low", "duplicate_of": null}'},
    )
    update, create_reply, emit = _patch_ticket_writes(monkeypatch)

    asyncio.run(
        modules._invoke_ai_classify_ticket({}, {"ticket_id": 42, "detect_duplicates": False})
    )

    categories.assert_not_awaited()
    assert "Allowed categories: [] (return null)" in invoke_ai.await_args.args[1]["prompt"]
    update.assert_awaited_once_with(42, priority="low")
    create_reply.assert_not_awaited()
    emit.assert_awaited_once()


def test_ai_link_related_posts_company_scoped_links_as_internal_note(monkeypatch):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket", AsyncMock(return_value=TICKET)
    )
    _patch_relationships(
        monkeypatch,
        [
            _relationship_row(title="<b>Printer</b> jams"),
            _relationship_row(source_id="42"),
            _relationship_row(source_id="12", target_company_id=6),
            _relationship_row(source_id="13", target_available=0),
            _relationship_row(
                source_type="knowledge_base",
                source_id="9",
                metadata_json='{"slug": "printer-duplex"}',
                relationship_type="KNOWN_ISSUE",
                confidence=0.7,
                target_company_id=None,
                permission_scope_json='{"version": 1, "visibility": "authenticated"}',
            ),
            _relationship_row(
                source_type="assets",
                source_id="31",
                permission_scope_json=json.dumps(
                    {"version": 1, "visibility": "company", "company_ids": [5], "required_any": ["can_manage_assets"]}
                ),
            ),
            _relationship_row(
                source_type="knowledge_base",
                source_id="32",
                metadata_json='{"slug": "admins-only"}',
                permission_scope_json=json.dumps(
                    {"version": 1, "visibility": "company_admin", "company_ids": [5]}
                ),
            ),
        ],
    )
    _, create_reply, emit = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(
        modules._invoke_ai_link_related({}, {"context": {"ticket": {"id": 42}}})
    )

    body = create_reply.await_args.kwargs["body"]
    assert create_reply.await_args.kwargs["is_internal"] is True
    assert 'href="/admin/tickets/11"' in body
    assert "&lt;b&gt;Printer&lt;/b&gt; jams" in body
    assert "/knowledge-base/articles/printer-duplex" in body
    assert "/admin/tickets/12" not in body
    assert "/admin/tickets/13" not in body
    assert "/admin/tickets/42" not in body
    assert "/assets/31" not in body
    assert "admins-only" not in body
    assert len(result["related_items"]) == 2
    emit.assert_awaited_once()


def test_ai_link_related_skips_when_no_relationships_are_stored(monkeypatch):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket", AsyncMock(return_value=TICKET)
    )
    monkeypatch.setattr(
        "app.repositories.rag_index.get_document_by_source", AsyncMock(return_value=None)
    )
    monkeypatch.setattr("app.services.rag_index.embedding_model", lambda: "model")
    _, create_reply, _ = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(modules._invoke_ai_link_related({}, {"ticket_id": 42}))

    assert result["status"] == "skipped"
    create_reply.assert_not_awaited()


def test_ai_request_missing_info_drafts_escaped_internal_question(monkeypatch):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket", AsyncMock(return_value=TICKET)
    )
    monkeypatch.setattr(
        "app.repositories.tickets.list_replies",
        AsyncMock(return_value=[{"id": 3, "body": "<p>It started on Monday</p>"}]),
    )
    invoke_ai = _enable_ollama(
        monkeypatch,
        {
            "response": json.dumps(
                {
                    "missing": ["device", "error_text"],
                    "question": "Which printer is it?\n<script>x</script>",
                }
            )
        },
    )
    _, create_reply, _ = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(
        modules._invoke_ai_request_missing_info({}, {"ticket_id": 42})
    )

    prompt = invoke_ai.await_args.args[1]["prompt"]
    assert "It started on Monday" in prompt
    assert prompt.index("It started on Monday") > prompt.index("BEGIN_UNTRUSTED_RECORDS")
    body = create_reply.await_args.kwargs["body"]
    assert create_reply.await_args.kwargs["is_internal"] is True
    assert "missing: device, error text" in body
    assert "<script>" not in body
    assert "Which printer is it?<br>&lt;script&gt;" in body
    assert result["missing"] == ["device", "error_text"]


@pytest.mark.parametrize(
    "response, status",
    [
        ({"missing": [], "question": ""}, "skipped"),
        ({"missing": ["device"], "question": ""}, "error"),
        ({"missing": ["password"], "question": "What is your password?"}, "error"),
        ({"missing": ["device"], "question": "x" * 1001}, "error"),
    ],
)
def test_ai_request_missing_info_only_posts_validated_drafts(monkeypatch, response, status):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket", AsyncMock(return_value=TICKET)
    )
    monkeypatch.setattr(
        "app.repositories.tickets.list_replies", AsyncMock(return_value=[])
    )
    _enable_ollama(monkeypatch, {"response": json.dumps(response)})
    _, create_reply, _ = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(
        modules._invoke_ai_request_missing_info({}, {"ticket_id": 42})
    )

    assert result["status"] == status
    create_reply.assert_not_awaited()


def test_new_ai_actions_are_always_on_ticket_actions():
    for slug in ("ai-classify-ticket", "ai-link-related", "ai-request-missing-info"):
        assert slug in modules.ALWAYS_ON_TICKET_ACTION_MODULE_SLUGS
        assert modules._get_always_on_ticket_action_module(slug)


def test_ai_link_related_honours_rag_kill_switch(monkeypatch):
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket", AsyncMock(return_value=TICKET)
    )
    monkeypatch.setattr(
        "app.services.component_availability.rag_available", lambda: False
    )
    load = _patch_relationships(monkeypatch, [_relationship_row()])
    _, create_reply, _ = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(modules._invoke_ai_link_related({}, {"ticket_id": 42}))

    assert result["status"] == "skipped"
    load.assert_not_awaited()
    create_reply.assert_not_awaited()


def test_ai_classify_does_not_replace_category_set_during_ai_call(monkeypatch):
    reads = iter([TICKET, {**TICKET, "category": "Set by technician"}])
    monkeypatch.setattr(
        "app.repositories.tickets.get_ticket",
        AsyncMock(side_effect=lambda ticket_id: next(reads)),
    )
    _enable_ollama(
        monkeypatch,
        {"response": '{"category": "Printing", "priority": "normal", "duplicate_of": null}'},
    )
    update, _, _ = _patch_ticket_writes(monkeypatch)

    result = asyncio.run(
        modules._invoke_ai_classify_ticket(
            {}, {"ticket_id": 42, "allowed_categories": "Printing", "detect_duplicates": False}
        )
    )

    update.assert_not_awaited()
    assert result["updated_fields"] == []
