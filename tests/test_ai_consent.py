from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import ai_consent, ai_opt_out


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _opt_out(monkeypatch, opted_out: set[int]) -> None:
    async def fake_list(user_ids):
        return {int(user_id) for user_id in user_ids if int(user_id) in opted_out}

    monkeypatch.setattr(ai_consent.user_repo, "list_ai_opted_out_user_ids", fake_list)


# ── Helper ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_is_ai_allowed_for_user(monkeypatch):
    _opt_out(monkeypatch, {7})

    assert await ai_consent.is_ai_allowed_for_user(7) is False
    assert await ai_consent.is_ai_allowed_for_user(8) is True
    # Content without an identifiable portal user is not blocked.
    assert await ai_consent.is_ai_allowed_for_user(None) is True
    assert await ai_consent.filter_ai_allowed([7, 8, "9", None, "x"]) == {8, 9}


@pytest.mark.anyio
async def test_lookup_failure_fails_closed(monkeypatch):
    async def broken(user_ids):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ai_consent.user_repo, "list_ai_opted_out_user_ids", broken)

    assert await ai_consent.is_ai_allowed_for_user(5) is False


@pytest.mark.anyio
async def test_ticket_check_covers_requester_staff(monkeypatch):
    _opt_out(monkeypatch, set())

    async def fake_staff(staff_id):
        return {"id": staff_id, "email": "Opted@Example.test"}

    async def fake_email(email):
        return str(email).lower() == "opted@example.test"

    import app.repositories.staff as staff_repo

    monkeypatch.setattr(staff_repo, "get_staff_by_id", fake_staff)
    monkeypatch.setattr(ai_consent.user_repo, "ai_opt_out_exists_for_email", fake_email)

    assert await ai_consent.is_ai_allowed_for_ticket({"requester_id": 3}) is True
    assert await ai_consent.is_ai_allowed_for_ticket({"requester_staff_id": 11}) is False


@pytest.mark.anyio
async def test_filter_replies_for_ai_drops_opted_out_authors(monkeypatch):
    _opt_out(monkeypatch, {7})
    replies = [
        {"id": 1, "author_id": 7, "body": "private"},
        {"id": 2, "author_id": 12, "body": "tech reply"},
        {"id": 3, "author_id": None, "body": "email import"},
    ]

    kept = await ai_consent.filter_replies_for_ai(replies)

    assert [reply["id"] for reply in kept] == [2, 3]


def test_opt_out_updates_records_timestamp_once():
    turned_on = ai_opt_out.opt_out_updates(True, {"ai_opt_out": 0})
    assert turned_on["ai_opt_out"] == 1 and turned_on["ai_opt_out_at"] is not None
    assert ai_opt_out.opt_out_updates(True, {"ai_opt_out": 1, "ai_opt_out_at": "2026-01-01"}) == {"ai_opt_out": 1}
    assert ai_opt_out.opt_out_updates(False, {"ai_opt_out": 1}) == {"ai_opt_out": 0, "ai_opt_out_at": None}


@pytest.mark.anyio
async def test_set_user_ai_opt_out_saves_and_purges(monkeypatch):
    updates: list[dict[str, Any]] = []
    purged: list[int] = []

    async def fake_update(user_id, **fields):
        updates.append(fields)
        return {"id": user_id, **fields}

    async def fake_purge(user_id):
        purged.append(user_id)
        return {}

    monkeypatch.setattr(ai_opt_out.user_repo, "update_user", fake_update)
    monkeypatch.setattr(ai_opt_out, "purge_user_from_rag_index", fake_purge)

    result = await ai_opt_out.set_user_ai_opt_out({"id": 4, "ai_opt_out": 0}, True)

    assert result["ai_opt_out"] == 1
    assert purged == [4]
    # Saving the same value again is a no-op.
    assert await ai_opt_out.set_user_ai_opt_out({"id": 4, "ai_opt_out": 1}, True) is None
    # Opting back in does not purge anything.
    assert (await ai_opt_out.set_user_ai_opt_out({"id": 4, "ai_opt_out": 1}, False))["ai_opt_out"] == 0
    assert purged == [4]
    assert len(updates) == 2


@pytest.mark.anyio
async def test_user_update_api_audits_opt_out(monkeypatch):
    from app.api.routes import users as users_route
    from app.schemas.users import UserUpdate

    audits: list[dict[str, Any]] = []
    stored = {"id": 4, "email": "user@example.test", "ai_opt_out": 0, "ai_opt_out_at": None}

    async def fake_get(user_id):
        return dict(stored)

    async def fake_update(user_id, **fields):
        stored.update(fields)
        return dict(stored)

    async def fake_record(**kwargs):
        audits.append(kwargs)

    async def fake_purge(user_id):
        return {}

    monkeypatch.setattr(users_route.user_repo, "get_user_by_id", fake_get)
    monkeypatch.setattr(users_route.user_repo, "update_user", fake_update)
    monkeypatch.setattr(users_route.audit_service, "record", fake_record)
    monkeypatch.setattr(ai_opt_out, "purge_user_from_rag_index", fake_purge)

    result = await users_route.update_user(
        4, UserUpdate(ai_opt_out=True), request=None, current_user={"id": 4}
    )

    assert result["ai_opt_out"] == 1 and result["ai_opt_out_at"] is not None
    assert [entry["action"] for entry in audits] == ["user.ai_opt_out"]
    assert audits[0]["metadata"] == {"changed_by_self": True, "source": "profile"}


@pytest.mark.anyio
async def test_purge_removes_tickets_comments_and_chats(monkeypatch):
    deleted: dict[str, list[str]] = {}
    enqueued: list[tuple[str, int]] = []

    async def fake_requested(user_id):
        return [10]

    async def fake_replies(user_id):
        return [
            {"ticket_id": 10, "reply_id": 100, "author_id": 5, "requester_id": 4},
            {"ticket_id": 20, "reply_id": 200, "author_id": 4, "requester_id": 9},
        ]

    async def fake_chats(user_id):
        return [30]

    async def fake_delete(source_type, source_ids):
        deleted[source_type] = list(source_ids)
        return len(source_ids)

    async def fake_enqueue(source_type, source_id, **kwargs):
        enqueued.append((source_type, source_id))

    from app.repositories import rag_index as rag_repo
    from app.services import rag_outbox

    monkeypatch.setattr(ai_opt_out.ai_consent_repo, "list_requested_ticket_ids", fake_requested)
    monkeypatch.setattr(ai_opt_out.ai_consent_repo, "list_ticket_replies_involving_user", fake_replies)
    monkeypatch.setattr(ai_opt_out.ai_consent_repo, "list_created_chat_room_ids", fake_chats)
    monkeypatch.setattr(rag_repo, "delete_documents_for_sources", fake_delete)
    monkeypatch.setattr(rag_outbox, "enqueue", fake_enqueue)

    counts = await ai_opt_out.purge_user_from_rag_index(4)

    assert deleted == {
        "tickets": ["10"],
        "ticket_comments": ["10:100", "20:200"],
        "chats": ["30"],
    }
    # Their reply on someone else's ticket is stripped by re-indexing it.
    assert enqueued == [("tickets", 20)]
    assert counts["reindexed"] == 1


# ── Ticket AI pipeline ──────────────────────────────────────────────────────


def _install_ticket_fakes(monkeypatch, *, requester_id: int, prompts: list[str], updates: list[dict[str, Any]]):
    from app.services import tickets as tickets_service

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Printer jam", "description": "Paper jam", "status": "open", "requester_id": requester_id}

    async def fake_list_replies(ticket_id, include_internal=True):
        return [
            {"id": 1, "author_id": 7, "body": "SECRET-FROM-OPTED-OUT", "is_internal": False},
            {"id": 2, "author_id": 12, "body": "Replaced the fuser unit.", "is_internal": True},
        ]

    async def fake_get_user(user_id):
        return {"id": user_id, "email": f"user{user_id}@example.test"}

    async def fake_trigger(slug, payload, *, background=True, on_complete=None):
        prompts.append(payload["prompt"])
        result = {"status": "succeeded", "model": "llama3", "response": '{"summary": "ok", "tags": []}'}
        if on_complete:
            await on_complete(result)
        return result

    async def fake_update(ticket_id, **fields):
        updates.append(fields)

    async def fake_emit(*args, **kwargs):
        return None

    async def fake_tags():
        return []

    async def fake_excluded():
        return set()

    monkeypatch.setattr(tickets_service.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_service.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(tickets_service.tickets_repo, "update_ticket", fake_update)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service.modules_service, "trigger_module", fake_trigger)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit)
    monkeypatch.setattr("app.services.tickets.get_preferred_tags", fake_tags)
    monkeypatch.setattr("app.services.tickets.get_all_excluded_tags", fake_excluded)
    return tickets_service


@pytest.mark.anyio
async def test_ticket_ai_insights_skipped_for_opted_out_requester(monkeypatch):
    _opt_out(monkeypatch, {7})
    prompts: list[str] = []
    updates: list[dict[str, Any]] = []
    tickets_service = _install_ticket_fakes(monkeypatch, requester_id=7, prompts=prompts, updates=updates)

    await tickets_service.refresh_ticket_ai_insights(1)
    await tickets_service.refresh_ticket_ai_summary(1)
    await tickets_service.refresh_ticket_ai_tags(1)

    assert prompts == []
    assert updates and all(
        update.get("ai_summary_status", "skipped") == "skipped"
        and update.get("ai_tags_status", "skipped") == "skipped"
        for update in updates
    )


@pytest.mark.anyio
async def test_ticket_ai_prompt_excludes_opted_out_reply_authors(monkeypatch):
    _opt_out(monkeypatch, {7})
    prompts: list[str] = []
    updates: list[dict[str, Any]] = []
    tickets_service = _install_ticket_fakes(monkeypatch, requester_id=3, prompts=prompts, updates=updates)

    await tickets_service.refresh_ticket_ai_insights(1)

    assert len(prompts) == 1
    assert "SECRET-FROM-OPTED-OUT" not in prompts[0]
    assert "Replaced the fuser unit." in prompts[0]


@pytest.mark.anyio
async def test_ai_ticket_modules_blocked_for_opted_out_requester(monkeypatch):
    from app.services import modules as modules_service

    async def fake_get_module(slug):
        return {"slug": slug, "enabled": True, "settings": {}}

    async def fake_allowed(ticket_id):
        return int(ticket_id) != 5

    called: list[str] = []

    async def fake_reprocess(settings, payload, *, event_future=None):
        called.append("reprocess")
        return {"status": "succeeded"}

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(ai_consent, "is_ai_allowed_for_ticket_id", fake_allowed)
    monkeypatch.setattr(modules_service, "_invoke_reprocess_ai", fake_reprocess)

    result = await modules_service.trigger_module(
        "reprocess-ai", {"context": {"ticket": {"id": 5}}}, background=False
    )

    assert result["status"] == "skipped"
    assert result["ai_opt_out"] is True
    assert called == []


# ── RAG indexing ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_index_agent_sources_excludes_opted_out_content(monkeypatch):
    from app.services import rag_index

    _opt_out(monkeypatch, {7})
    indexed: list[Any] = []
    deleted: list[tuple[str, list[str]]] = []

    async def fake_index(document, **kwargs):
        indexed.append(document)
        return 1

    async def fake_delete(source_type, source_ids):
        deleted.append((source_type, list(source_ids)))
        return 1

    monkeypatch.setattr(rag_index, "index_document", fake_index)
    monkeypatch.setattr(rag_index.rag_repo, "delete_documents_for_sources", fake_delete)

    count = await rag_index.index_agent_sources(
        {
            "tickets": [
                {"id": 1, "subject": "Mine", "requester_id": 7, "company_id": 2},
                {
                    "id": 2,
                    "subject": "Someone else's",
                    "requester_id": 8,
                    "company_id": 2,
                    "replies": [
                        {"id": 1, "author_id": 7, "body": "OPTED-OUT-REPLY"},
                        {"id": 2, "author_id": 8, "body": "Requester reply"},
                    ],
                },
            ],
            "chats": [{"id": 9, "subject": "Help", "created_by_user_id": 7, "company_id": 2}],
        }
    )

    assert count == 1
    assert [doc.source_id for doc in indexed] == ["2"]
    assert "OPTED-OUT-REPLY" not in indexed[0].text
    assert "OPTED-OUT-REPLY" not in str(indexed[0].metadata)
    assert ("tickets", ["1"]) in deleted
    assert ("chats", ["9"]) in deleted


@pytest.mark.anyio
async def test_rag_outbox_load_source_removes_opted_out_ticket(monkeypatch):
    from app.services import rag_outbox

    _opt_out(monkeypatch, {7})
    deleted: list[tuple[str, list[str]]] = []

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "Mine", "requester_id": 7}

    async def fake_list_replies(ticket_id, include_internal=True):
        return []

    async def fake_delete(source_type, source_ids):
        deleted.append((source_type, list(source_ids)))
        return 1

    monkeypatch.setattr(rag_outbox.tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(rag_outbox.tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(rag_outbox.rag_repo, "delete_documents_for_sources", fake_delete)

    assert await rag_outbox._load_source("tickets", "3") is None
    assert deleted == [("tickets", ["3"])]


# ── Chat waiting assistant ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_waiting_assistant_ignores_opted_out_customer(monkeypatch):
    from app.services import matrix_ai_waiting_assistant as assistant

    _opt_out(monkeypatch, {7})
    settings = SimpleNamespace(matrixbot_ai_max_responses=2)

    async def fake_has_technician_message(room_id):
        return False

    monkeypatch.setattr(assistant, "_enabled", lambda: True)
    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    monkeypatch.setattr(assistant.chat_repo, "has_technician_message", fake_has_technician_message)

    room = {"id": 42, "status": "open", "ai_bot_response_count": 0}
    assert await assistant._eligible_room({**room, "created_by_user_id": 7}) is False
    assert await assistant._eligible_room({**room, "created_by_user_id": 8}) is True


# ── Agent ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_agent_disabled_for_opted_out_user(monkeypatch):
    from app.services import agent as agent_service

    _opt_out(monkeypatch, {7})

    async def fail_trigger(*args, **kwargs):
        raise AssertionError("The LLM must not be called for an opted-out user")

    monkeypatch.setattr(agent_service.modules_service, "trigger_module", fail_trigger)

    result = await agent_service.execute_agent_query("Why is my VPN down?", {"id": 7}, memberships=[])

    assert result["status"] == "ai_opted_out"
    assert result["answer"] is None
    assert result["message"] == ai_consent.AGENT_OPTED_OUT_MESSAGE


@pytest.mark.anyio
async def test_filter_agent_sources_removes_opted_out_people(monkeypatch):
    _opt_out(monkeypatch, {7})

    filtered = await ai_consent.filter_agent_sources(
        {
            "tickets": [{"id": 1, "requester_id": 7}, {"id": 2, "requester_id": 8}],
            "ticket_comments": [
                {"id": "2:5", "ticket_id": 2, "requester_id": 8, "replies": [{"id": 5, "author_id": 7}]},
                {"id": "1:6", "ticket_id": 1, "requester_id": 7, "replies": [{"id": 6, "author_id": 9}]},
            ],
            "chats": [{"id": 3, "created_by_user_id": 7}, {"id": 4, "created_by_user_id": 9}],
            "products": [{"id": 99}],
        }
    )

    assert [item["id"] for item in filtered["tickets"]] == [2]
    assert filtered["ticket_comments"] == []
    assert [item["id"] for item in filtered["chats"]] == [4]
    assert filtered["products"] == [{"id": 99}]


# ── Calls ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_call_transcription_skipped_for_opted_out_caller(monkeypatch):
    from app.services import call_recordings

    updates: list[dict[str, Any]] = []

    async def fake_get(recording_id):
        return {"id": recording_id, "caller_email": "caller@example.test", "transcription_status": "queued"}

    async def fake_update(recording_id, **fields):
        updates.append(fields)
        return {"id": recording_id, **fields}

    async def fake_email(email):
        return email == "caller@example.test"

    monkeypatch.setattr(call_recordings.call_recordings_repo, "get_call_recording_by_id", fake_get)
    monkeypatch.setattr(call_recordings.call_recordings_repo, "update_call_recording", fake_update)
    monkeypatch.setattr(ai_consent.user_repo, "ai_opt_out_exists_for_email", fake_email)
    monkeypatch.setattr(
        call_recordings, "_whisperx_env_settings", lambda: pytest.fail("WhisperX must not be used")
    )

    result = await call_recordings.transcribe_recording(1)

    assert updates == [{"transcription_status": "skipped"}]
    assert result["transcription_status"] == "skipped"


# ── Migration ───────────────────────────────────────────────────────────────


def test_migration_is_idempotent_and_sqlite_compatible():
    from app.core.database import Database

    sql = (Path(__file__).resolve().parent.parent / "migrations" / "458_user_ai_opt_out.sql").read_text()
    statements = [line for line in sql.splitlines() if line.startswith("ALTER TABLE")]
    assert statements and all("ADD COLUMN IF NOT EXISTS" in line for line in statements)
    adapted = Database._adapt_sql_for_sqlite(Database.__new__(Database), sql)
    assert "ADD COLUMN ai_opt_out TINYINT(1) NOT NULL DEFAULT 0" in adapted
    assert "ADD COLUMN ai_opt_out_at TEXT NULL" in adapted
