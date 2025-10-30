from datetime import datetime, timedelta, timezone

import pytest

from app.security.session import SessionData
from app.services import impersonation as impersonation_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_user_is_impersonatable_checks_membership(monkeypatch):
    async def fake_list_memberships(user_id, *, status="active"):
        assert user_id == 5
        assert status == "active"
        return [
            {"permissions": ["tickets.view"], "is_admin": False},
        ]

    monkeypatch.setattr(
        impersonation_service.membership_repo,
        "list_memberships_for_user",
        fake_list_memberships,
    )
    async def fake_get_user(user_id):
        return {"id": user_id, "is_super_admin": False}

    monkeypatch.setattr(
        impersonation_service.user_repo,
        "get_user_by_id",
        fake_get_user,
    )

    assert await impersonation_service.user_is_impersonatable(5)


@pytest.mark.anyio("asyncio")
async def test_user_is_impersonatable_super_admin(monkeypatch):
    async def fake_list_memberships(user_id, *, status="active"):
        assert user_id == 7
        return []

    async def fake_get_user(user_id):
        return {"id": user_id, "is_super_admin": True}

    monkeypatch.setattr(
        impersonation_service.membership_repo,
        "list_memberships_for_user",
        fake_list_memberships,
    )
    monkeypatch.setattr(impersonation_service.user_repo, "get_user_by_id", fake_get_user)

    assert await impersonation_service.user_is_impersonatable(7)


@pytest.mark.anyio("asyncio")
async def test_list_impersonatable_users_combines_memberships(monkeypatch):
    async def fake_list_memberships():
        return [
            {
                "user_id": 2,
                "email": "user@example.com",
                "first_name": "End",
                "last_name": "User",
                "company_id": 9,
                "company_name": "Example Co",
                "role_name": "Support",
                "permissions": ["tickets.view"],
                "is_admin": 0,
                "is_super_admin": 0,
            }
        ]

    async def fake_list_users():
        return [
            {"id": 1, "email": "admin@example.com", "is_super_admin": True},
            {"id": 2, "email": "user@example.com", "is_super_admin": False},
        ]

    monkeypatch.setattr(
        impersonation_service.membership_repo,
        "list_impersonatable_memberships",
        fake_list_memberships,
    )
    monkeypatch.setattr(impersonation_service.user_repo, "list_users", fake_list_users)

    results = await impersonation_service.list_impersonatable_users()
    assert len(results) == 2
    assert results[0]["email"] == "admin@example.com"
    assert results[0]["has_permissions"]
    assert results[1]["memberships"][0]["company_name"] == "Example Co"


@pytest.mark.anyio("asyncio")
async def test_start_impersonation_creates_new_session(monkeypatch):
    actor_user = {"id": 1, "email": "admin@example.com", "is_super_admin": True}
    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    actor_session = SessionData(
        id=11,
        user_id=1,
        session_token="original",
        csrf_token="csrf",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=None,
        pending_totp_secret=None,
        impersonator_user_id=None,
        impersonator_session_id=None,
        impersonation_started_at=None,
    )

    async def fake_get_user(user_id):
        assert user_id == 42
        return {"id": user_id, "email": "user@example.com"}

    async def fake_is_impersonatable(user_id):
        assert user_id == 42
        return True

    async def fake_first_company(user):
        assert user["id"] == 42
        return 17

    created_session = SessionData(
        id=99,
        user_id=42,
        session_token="impersonated",
        csrf_token="csrf-imp",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=17,
        pending_totp_secret=None,
        impersonator_user_id=1,
        impersonator_session_id=11,
        impersonation_started_at=now,
    )

    async def fake_create_session(user_id, request, *, active_company_id, impersonator_user_id, impersonator_session_id):
        assert user_id == 42
        assert active_company_id == 17
        assert impersonator_user_id == 1
        assert impersonator_session_id == 11
        return created_session

    async def fake_log_action(**kwargs):
        return None

    monkeypatch.setattr(impersonation_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(impersonation_service, "user_is_impersonatable", fake_is_impersonatable)
    monkeypatch.setattr(impersonation_service.company_access, "first_accessible_company_id", fake_first_company)
    monkeypatch.setattr(impersonation_service.session_manager, "create_session", fake_create_session)
    monkeypatch.setattr(impersonation_service.audit_service, "log_action", fake_log_action)
    monkeypatch.setattr(impersonation_service, "log_info", lambda *args, **kwargs: None)

    target_user, impersonated_session = await impersonation_service.start_impersonation(
        request=None,
        actor_user=actor_user,
        actor_session=actor_session,
        target_user_id=42,
    )

    assert target_user["company_id"] == 17
    assert impersonated_session.id == 99


@pytest.mark.anyio("asyncio")
async def test_end_impersonation_restores_original_session(monkeypatch):
    now = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc)
    impersonated_session = SessionData(
        id=50,
        user_id=42,
        session_token="impersonated",
        csrf_token="csrf-imp",
        created_at=now - timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
        active_company_id=23,
        pending_totp_secret=None,
        impersonator_user_id=1,
        impersonator_session_id=22,
        impersonation_started_at=now - timedelta(minutes=5),
    )

    original_record = {
        "id": 22,
        "user_id": 1,
        "session_token": "original",
        "csrf_token": "csrf",
        "created_at": now - timedelta(hours=2),
        "expires_at": now + timedelta(hours=1),
        "last_seen_at": now - timedelta(minutes=10),
        "ip_address": None,
        "user_agent": None,
        "active_company_id": 5,
        "pending_totp_secret": None,
        "impersonator_user_id": None,
        "impersonator_session_id": None,
        "impersonation_started_at": None,
        "is_active": 1,
    }

    async def fake_get_session(session_id):
        assert session_id == 22
        return original_record

    calls = {}

    async def fake_update_session(session_id, **kwargs):
        calls["update"] = (session_id, kwargs)

    async def fake_deactivate_session(session_id):
        calls.setdefault("deactivate", []).append(session_id)

    async def fake_get_user(user_id):
        assert user_id == 1
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}

    async def fake_log_action(**kwargs):
        calls["log"] = kwargs

    monkeypatch.setattr(impersonation_service.auth_repo, "get_session_by_id", fake_get_session)
    monkeypatch.setattr(impersonation_service.auth_repo, "update_session", fake_update_session)
    monkeypatch.setattr(impersonation_service.auth_repo, "deactivate_session", fake_deactivate_session)
    monkeypatch.setattr(impersonation_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(impersonation_service.audit_service, "log_action", fake_log_action)
    monkeypatch.setattr(impersonation_service, "log_info", lambda *args, **kwargs: None)

    restored_user, restored_session = await impersonation_service.end_impersonation(
        request=None,
        session=impersonated_session,
    )

    assert restored_user["email"] == "admin@example.com"
    assert restored_session.id == 22
    assert calls["deactivate"] == [50]
    assert calls["log"]["entity_id"] == 42
