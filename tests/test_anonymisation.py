"""Tests for account anonymisation (issue #4554).

Coverage:

* the repository's pure helpers (deterministic placeholder email + detection);
* the repository's destructive ``anonymise_user`` run against a mocked ``db`` —
  asserting the ``users`` row PII is nulled and that a run tolerates missing
  optional feature-pack tables;
* the service orchestration (``create`` / ``list`` / ``get`` / ``execute`` /
  ``cancel``) with the repository and the best-effort side effects mocked;
* the ``rag_index.list_document_ids_for_source`` helper used for RAG cleanup;
* registration of the anonymisation routes.
"""

import pytest

from app.repositories import anonymisation as anon_repo
from app.repositories import rag_index as rag_repo
from app.repositories import users as users_repo
from app.services import anonymisation as anon_service


def _pending_request(request_id: int = 5, user_id: int = 10) -> dict:
    return {
        "id": request_id,
        "user_id": user_id,
        "status": "pending",
        "reason": None,
        "requested_at": None,
        "completed_at": None,
        "executed_by_user_id": None,
        "error_message": None,
        "support_ticket_id": None,
    }


def test_placeholder_email_is_deterministic_and_undeliverable():
    assert anon_repo.placeholder_email_for(42) == "anonymised+42@invalid"
    assert anon_repo.placeholder_email_for(42) == anon_repo.placeholder_email_for(42)
    assert anon_repo.placeholder_email_for(7) != anon_repo.placeholder_email_for(42)


def test_is_anonymised_email_detects_placeholders():
    assert anon_repo.is_anonymised_email("anonymised+42@invalid")
    assert anon_repo.is_anonymised_email("ANONYMISED+42@INVALID")
    assert not anon_repo.is_anonymised_email("user@example.com")
    assert not anon_repo.is_anonymised_email(None)
    assert not anon_repo.is_anonymised_email("")


def test_sha256_is_deterministic():
    assert anon_service._sha256("a") == anon_service._sha256("a")

@pytest.mark.anyio("asyncio")
async def test_anonymise_user_nulls_user_pii_and_tolerates_missing_tables(monkeypatch):
    captured = []

    async def _execute(sql, params=None):
        captured.append((sql, params))
        return None

    async def _execute_rowcount(sql, params=None):
        return 0

    async def _fetch_all(sql, params=None):
        return []  # no staff rows -> staff-specific branches are skipped

    monkeypatch.setattr(anon_repo.db, "execute", _execute)
    monkeypatch.setattr(anon_repo.db, "execute_rowcount", _execute_rowcount)
    monkeypatch.setattr(anon_repo.db, "fetch_all", _fetch_all)

    user_id = 99
    summary = await anon_repo.anonymise_user(user_id, original_email="u@x.com", original_phone="12345")

    # The account row itself is the one step guaranteed to run.
    assert summary.get("users") == 1
    users_updates = [(sql, p) for sql, p in captured if "UPDATE users" in sql and "password_hash" in sql]
    assert users_updates, "expected the users PII update to run"
    sql, params = users_updates[0]
    assert "password_hash = NULL" in sql
    assert "totp_secret = NULL" in sql
    assert "mobile_phone = NULL" in sql
    assert "is_active = 0" in sql
    assert params[2] == anon_repo.placeholder_email_for(user_id)
    assert params[3] == user_id


@pytest.mark.anyio("asyncio")
async def test_anonymise_user_is_idempotent(monkeypatch):
    """Running twice yields the same placeholder and the same users update."""
    seen = []

    async def _execute(sql, params=None):
        if "UPDATE users" in sql:
            seen.append(params)
        return None

    async def _execute_rowcount(sql, params=None):
        return 0

    async def _fetch_all(sql, params=None):
        return []

    monkeypatch.setattr(anon_repo.db, "execute", _execute)
    monkeypatch.setattr(anon_repo.db, "execute_rowcount", _execute_rowcount)
    monkeypatch.setattr(anon_repo.db, "fetch_all", _fetch_all)

    await anon_repo.anonymise_user(7, original_email="u@x.com")
    await anon_repo.anonymise_user(7, original_email="u@x.com")
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert seen[0][2] == anon_repo.placeholder_email_for(7)

@pytest.mark.anyio("asyncio")
async def test_service_list_requests_delegates_to_repo(monkeypatch):
    seen = {}

    async def _list(*, status=None, limit=100):
        seen["status"] = status
        seen["limit"] = limit
        return [{"id": 1}]

    monkeypatch.setattr(anon_repo, "list_requests", _list)
    result = await anon_service.list_requests(status="pending", limit=7)
    assert result == [{"id": 1}]
    assert seen == {"status": "pending", "limit": 7}


@pytest.mark.anyio("asyncio")
async def test_service_get_request_delegates_to_repo(monkeypatch):
    async def _get(request_id):
        return {"id": request_id}

    monkeypatch.setattr(anon_repo, "get_request", _get)
    assert await anon_service.get_request(12) == {"id": 12}


@pytest.mark.anyio("asyncio")
async def test_service_get_request_for_user_delegates_to_repo(monkeypatch):
    async def _get(user_id):
        return {"id": 1, "user_id": user_id}

    monkeypatch.setattr(anon_repo, "get_request_for_user", _get)
    assert (await anon_service.get_request_for_user(55))["user_id"] == 55


@pytest.mark.anyio("asyncio")
async def test_create_request_is_idempotent_when_one_exists(monkeypatch):
    existing = _pending_request(9, 10)
    create_calls = []

    async def _get_for_user(user_id):
        return existing

    async def _create(**kwargs):
        create_calls.append(kwargs)
        return 999

    monkeypatch.setattr(anon_repo, "get_request_for_user", _get_for_user)
    monkeypatch.setattr(anon_repo, "create_request", _create)

    result = await anon_service.create_request(user={"id": 10, "email": "u@x.com"})
    assert result["created"] is False
    assert result["status"] == "pending"
    assert result["request"]["id"] == 9
    assert create_calls == []  # did not insert a second row


@pytest.mark.anyio("asyncio")
async def test_create_request_creates_row_and_links_support_ticket(monkeypatch):
    created_ids = []
    set_ticket_calls = []

    async def _get_for_user(user_id):
        return None

    async def _create(*, user_id, reason=None, requested_by_user_id=None, ip_address=None, user_agent=None):
        created_ids.append((user_id, requested_by_user_id, ip_address, user_agent))
        return 42

    async def _get(request_id):
        return _pending_request(request_id, user_id=10)

    async def _set_ticket(request_id, ticket_id):
        set_ticket_calls.append((request_id, ticket_id))

    async def _create_ticket(user, request_id):
        return 7

    monkeypatch.setattr(anon_repo, "get_request_for_user", _get_for_user)
    monkeypatch.setattr(anon_repo, "create_request", _create)
    monkeypatch.setattr(anon_repo, "get_request", _get)
    monkeypatch.setattr(anon_repo, "set_support_ticket", _set_ticket)
    monkeypatch.setattr(anon_service, "_create_support_ticket", _create_ticket)

    result = await anon_service.create_request(
        user={"id": 10, "email": "u@x.com"},
        ip_address="1.2.3.4",
        user_agent="TestAgent",
    )
    assert result["created"] is True
    assert result["ticket_id"] == 7
    assert result["status"] == "pending"
    assert created_ids == [(10, 10, "1.2.3.4", "TestAgent")]
    assert set_ticket_calls == [(42, 7)]


@pytest.mark.anyio("asyncio")
async def test_create_request_raises_on_missing_user_id(monkeypatch):
    async def _get_for_user(user_id):
        return None

    monkeypatch.setattr(anon_repo, "get_request_for_user", _get_for_user)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.create_request(user={"email": "no-id@x.com"})

@pytest.mark.anyio("asyncio")
async def test_execute_request_raises_when_not_found(monkeypatch):
    async def _get(request_id):
        return None

    monkeypatch.setattr(anon_repo, "get_request", _get)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.execute_request(request_id=5, actor_user={"id": 1})


@pytest.mark.anyio("asyncio")
async def test_execute_request_rejects_non_pending(monkeypatch):
    async def _get(request_id):
        return _pending_request(5, 10) | {"status": "completed"}

    monkeypatch.setattr(anon_repo, "get_request", _get)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.execute_request(request_id=5, actor_user={"id": 1})


@pytest.mark.anyio("asyncio")
async def test_execute_request_rejects_when_claim_fails(monkeypatch):
    async def _get(request_id):
        return _pending_request(5, 10)

    async def _get_user(user_id):
        return {"id": user_id, "email": "u@x.com", "mobile_phone": None}

    async def _tickets(user_id):
        return [1, 2]

    async def _mark_executing(request_id, *, executed_by_user_id):
        return False

    monkeypatch.setattr(anon_repo, "get_request", _get)
    monkeypatch.setattr(users_repo, "get_user_by_id", _get_user)
    monkeypatch.setattr(anon_repo, "list_request_ticket_ids", _tickets)
    monkeypatch.setattr(anon_repo, "mark_executing", _mark_executing)

    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.execute_request(request_id=5, actor_user={"id": 1})


@pytest.mark.anyio("asyncio")
async def test_execute_request_success_flow(monkeypatch):
    calls = {"completed": False, "rag": 0, "marketing": 0, "notify": None, "audit": []}

    async def _get(request_id):
        return _pending_request(5, 10)

    async def _get_user(user_id):
        return {"id": user_id, "email": "u@x.com", "mobile_phone": None}

    async def _tickets(user_id):
        return [3]

    async def _mark_executing(request_id, *, executed_by_user_id):
        assert executed_by_user_id == 1
        return True

    async def _anonymise(user_id, *, original_email=None, original_phone=None, placeholder_email=None):
        return {"users": 1, "tickets": 2}

    async def _mark_completed(request_id, *, original_email_hash=None):
        calls["completed"] = True
        return True

    async def _rag(ticket_ids):
        calls["rag"] += 1
        return 4

    async def _marketing(email):
        calls["marketing"] += 1

    async def _audit(*, request_id, actor_id, user_id, success, details):
        calls["audit"].append((success, user_id))

    async def _notify(email):
        calls["notify"] = email
        return True

    monkeypatch.setattr(anon_repo, "get_request", _get)
    monkeypatch.setattr(users_repo, "get_user_by_id", _get_user)
    monkeypatch.setattr(anon_repo, "list_request_ticket_ids", _tickets)
    monkeypatch.setattr(anon_repo, "mark_executing", _mark_executing)
    monkeypatch.setattr(anon_repo, "anonymise_user", _anonymise)
    monkeypatch.setattr(anon_repo, "mark_completed", _mark_completed)
    monkeypatch.setattr(anon_service, "_delete_rag_documents_for_user", _rag)
    monkeypatch.setattr(anon_service, "_opt_out_marketing", _marketing)
    monkeypatch.setattr(anon_service, "_audit_execution", _audit)
    monkeypatch.setattr(anon_service, "_notify_user_anonymised", _notify)

    result = await anon_service.execute_request(request_id=5, actor_user={"id": 1})
    assert result["status"] == "completed"
    assert result["user_id"] == 10
    assert result["rag_documents_deleted"] == 4
    assert result["notified"] is True
    assert calls["completed"] is True
    assert calls["marketing"] == 1
    assert calls["notify"] == "u@x.com"
    assert any(success for success, _ in calls["audit"])


@pytest.mark.anyio("asyncio")
async def test_execute_request_marks_failed_when_run_raises(monkeypatch):
    failed = {}

    async def _get(request_id):
        return _pending_request(5, 10)

    async def _get_user(user_id):
        return {"id": user_id, "email": "u@x.com", "mobile_phone": None}

    async def _tickets(user_id):
        return []

    async def _mark_executing(request_id, *, executed_by_user_id):
        return True

    async def _anonymise(user_id, *, original_email=None, original_phone=None, placeholder_email=None):
        raise RuntimeError("boom")

    async def _mark_failed(request_id, *, error_message=None):
        failed["message"] = error_message
        return True

    async def _audit(*, request_id, actor_id, user_id, success, details):
        failed["success"] = success

    monkeypatch.setattr(anon_repo, "get_request", _get)
    monkeypatch.setattr(users_repo, "get_user_by_id", _get_user)
    monkeypatch.setattr(anon_repo, "list_request_ticket_ids", _tickets)
    monkeypatch.setattr(anon_repo, "mark_executing", _mark_executing)
    monkeypatch.setattr(anon_repo, "anonymise_user", _anonymise)
    monkeypatch.setattr(anon_repo, "mark_failed", _mark_failed)
    monkeypatch.setattr(anon_service, "_audit_execution", _audit)

    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.execute_request(request_id=5, actor_user={"id": 1})
    assert "boom" in failed["message"]
    assert failed["success"] is False

@pytest.mark.anyio("asyncio")
async def test_cancel_request_raises_when_not_found(monkeypatch):
    async def _get(request_id):
        return None

    monkeypatch.setattr(anon_repo, "get_request", _get)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.cancel_request(request_id=5, actor_user={"id": 1})


@pytest.mark.anyio("asyncio")
async def test_cancel_request_rejects_non_pending(monkeypatch):
    async def _get(request_id):
        return _pending_request(5, 10) | {"status": "completed"}

    monkeypatch.setattr(anon_repo, "get_request", _get)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.cancel_request(request_id=5, actor_user={"id": 1})


@pytest.mark.anyio("asyncio")
async def test_cancel_request_success_deletes_and_audits(monkeypatch):
    state = {"cancelled": False, "audit": None}

    async def _get(request_id):
        return _pending_request(5, 10)

    async def _cancel(request_id):
        state["cancelled"] = True
        return True

    async def _audit(*, request_id, actor_id, user_id, success, details):
        state["audit"] = (success, details)

    monkeypatch.setattr(anon_repo, "get_request", _get)
    monkeypatch.setattr(anon_repo, "cancel_request", _cancel)
    monkeypatch.setattr(anon_service, "_audit_execution", _audit)

    result = await anon_service.cancel_request(request_id=5, actor_user={"id": 1})
    assert result["status"] == "cancelled"
    assert result["user_id"] == 10
    assert state["cancelled"] is True
    assert state["audit"][0] is True


@pytest.mark.anyio("asyncio")
async def test_list_document_ids_for_source_returns_matching_ids(monkeypatch):
    async def _fetch_all(sql, params=None):
        assert "source_type" in sql
        assert "source_id IN" in sql
        return [{"id": 101}, {"id": 102}, {"id": None}]

    monkeypatch.setattr(rag_repo.db, "fetch_all", _fetch_all)
    assert await rag_repo.list_document_ids_for_source("tickets", [3, 4]) == [101, 102]


@pytest.mark.anyio("asyncio")
async def test_list_document_ids_for_source_short_circuits_when_empty(monkeypatch):
    calls = []

    async def _fetch_all(sql, params=None):
        calls.append(1)
        return []

    monkeypatch.setattr(rag_repo.db, "fetch_all", _fetch_all)
    assert await rag_repo.list_document_ids_for_source("tickets", []) == []
    assert await rag_repo.list_document_ids_for_source("tickets", [None, ""]) == []
    assert calls == []  # never touches the database


def test_anonymisation_routes_are_registered():
    from app.main import app

    expected = {
        ("POST", "/admin/profile/anonymisation"),
        ("GET", "/admin/anonymisation"),
        ("GET", "/admin/anonymisation/{request_id}"),
        ("POST", "/admin/anonymisation/{request_id}/execute"),
        ("POST", "/admin/anonymisation/{request_id}/cancel"),
    }
    registered = {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or [])}
    missing = expected - registered
    assert not missing, f"missing routes: {sorted(missing)}"
    assert anon_service._sha256("a") != anon_service._sha256("b")