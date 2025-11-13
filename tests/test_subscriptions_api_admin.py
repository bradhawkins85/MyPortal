"""Tests for subscription admin API endpoints (edit and delete)."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.repositories import subscriptions as subscriptions_repo
from app.security.session import SessionData, session_manager


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    async def fake_change_log_sync():
        return None

    async def fake_ensure_modules():
        return None

    async def fake_refresh_automations():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", fake_change_log_sync)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", fake_ensure_modules)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", fake_refresh_automations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def super_admin_session(monkeypatch):
    """Setup session for super admin user."""
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=1,
        user_id=1,
        session_token="session-token",
        csrf_token="test-csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=None,
    )

    user = {"id": 1, "email": "superadmin@example.com", "is_super_admin": True}

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return session
    
    async def fake_get_user_by_id(user_id):
        return user

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    
    return session


@pytest.fixture
def regular_user_session(monkeypatch):
    """Setup session for regular user."""
    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionData(
        id=2,
        user_id=2,
        session_token="session-token-2",
        csrf_token="test-csrf-token-2",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=None,
    )

    user = {"id": 2, "email": "user@example.com", "is_super_admin": False}

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return session
    
    async def fake_get_user_by_id(user_id):
        return user

    monkeypatch.setattr(session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    
    return session


@pytest.fixture
def mock_subscription():
    """Mock subscription data."""
    return {
        "id": "test-sub-123",
        "customer_id": 10,
        "product_id": 1,
        "product_name": "Test Product",
        "subscription_category_id": 1,
        "category_name": "Software",
        "start_date": date(2025, 1, 1),
        "end_date": date(2025, 12, 31),
        "quantity": 5,
        "unit_price": "10.00",
        "prorated_price": None,
        "status": "active",
        "auto_renew": True,
        "created_at": None,
        "updated_at": None,
    }


def test_update_subscription_as_super_admin(super_admin_session, mock_subscription, monkeypatch):
    """Test updating a subscription as super admin."""
    updated_subscription = {**mock_subscription, "unit_price": 10.00, "prorated_price": None, "status": "canceled", "auto_renew": False}
    
    call_count = [0]
    
    async def fake_get_subscription(subscription_id):
        if subscription_id == mock_subscription["id"]:
            call_count[0] += 1
            # First call returns original, second call returns updated
            if call_count[0] == 1:
                return {**mock_subscription, "unit_price": 10.00, "prorated_price": None}
            else:
                return updated_subscription
        return None
    
    async def fake_update_subscription(subscription_id, **kwargs):
        pass
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", fake_update_subscription)
    
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            json={"status": "canceled", "autoRenew": False},
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == mock_subscription["id"]
    assert data["status"] == "canceled"
    assert data["autoRenew"] is False


def test_update_subscription_as_regular_user(regular_user_session, mock_subscription, monkeypatch):
    """Test that regular users cannot update subscriptions."""
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            json={"status": "canceled", "autoRenew": False},
            headers={"X-CSRF-Token": regular_user_session.csrf_token}
        )
    
    assert response.status_code == 403
    assert "Super admin privileges required" in response.json()["detail"]


def test_update_subscription_invalid_status(super_admin_session, mock_subscription, monkeypatch):
    """Test updating subscription with invalid status."""
    async def fake_get_subscription(subscription_id):
        if subscription_id == mock_subscription["id"]:
            return {**mock_subscription, "unit_price": 10.00, "prorated_price": None}
        return None
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            json={"status": "invalid_status"},
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 400
    assert "Invalid status" in response.json()["detail"]


def test_update_nonexistent_subscription(super_admin_session, monkeypatch):
    """Test updating a subscription that doesn't exist."""
    async def fake_get_subscription(subscription_id):
        return None
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    
    with TestClient(app) as client:
        response = client.patch(
            "/api/v1/subscriptions/nonexistent-id",
            json={"status": "canceled"},
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 404
    assert "Subscription not found" in response.json()["detail"]


def test_delete_subscription_as_super_admin(super_admin_session, mock_subscription, monkeypatch):
    """Test deleting a subscription as super admin."""
    async def fake_get_subscription(subscription_id):
        if subscription_id == mock_subscription["id"]:
            return {**mock_subscription, "unit_price": 10.00, "prorated_price": None}
        return None
    
    async def fake_delete_subscription(subscription_id):
        pass
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "delete_subscription", fake_delete_subscription)
    
    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 204


def test_delete_subscription_as_regular_user(regular_user_session, mock_subscription, monkeypatch):
    """Test that regular users cannot delete subscriptions."""
    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            headers={"X-CSRF-Token": regular_user_session.csrf_token}
        )
    
    assert response.status_code == 403
    assert "Super admin privileges required" in response.json()["detail"]


def test_delete_nonexistent_subscription(super_admin_session, monkeypatch):
    """Test deleting a subscription that doesn't exist."""
    async def fake_get_subscription(subscription_id):
        return None
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    
    with TestClient(app) as client:
        response = client.delete(
            "/api/v1/subscriptions/nonexistent-id",
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 404
    assert "Subscription not found" in response.json()["detail"]


def test_update_subscription_end_date_as_super_admin(super_admin_session, mock_subscription, monkeypatch):
    """Test updating subscription end date as super admin."""
    new_end_date = date(2026, 6, 30)
    updated_subscription = {**mock_subscription, "unit_price": 10.00, "prorated_price": None, "end_date": new_end_date}
    
    call_count = [0]
    
    async def fake_get_subscription(subscription_id):
        if subscription_id == mock_subscription["id"]:
            call_count[0] += 1
            # First call returns original, second call returns updated
            if call_count[0] == 1:
                return {**mock_subscription, "unit_price": 10.00, "prorated_price": None}
            else:
                return updated_subscription
        return None
    
    async def fake_update_subscription(subscription_id, **kwargs):
        pass
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", fake_update_subscription)
    
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            json={"endDate": "2026-06-30"},
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == mock_subscription["id"]
    assert data["endDate"] == "2026-06-30"


def test_update_subscription_end_date_before_start_date(super_admin_session, mock_subscription, monkeypatch):
    """Test updating subscription end date to before start date fails."""
    async def fake_get_subscription(subscription_id):
        if subscription_id == mock_subscription["id"]:
            return {**mock_subscription, "unit_price": 10.00, "prorated_price": None}
        return None
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            json={"endDate": "2024-12-31"},  # Before start date of 2025-01-01
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 400
    assert "End date must be after start date" in response.json()["detail"]


def test_update_subscription_multiple_fields(super_admin_session, mock_subscription, monkeypatch):
    """Test updating multiple subscription fields at once."""
    new_end_date = date(2026, 3, 31)
    updated_subscription = {
        **mock_subscription, 
        "unit_price": 10.00, 
        "prorated_price": None, 
        "status": "pending_renewal",
        "auto_renew": False,
        "end_date": new_end_date
    }
    
    call_count = [0]
    
    async def fake_get_subscription(subscription_id):
        if subscription_id == mock_subscription["id"]:
            call_count[0] += 1
            if call_count[0] == 1:
                return {**mock_subscription, "unit_price": 10.00, "prorated_price": None}
            else:
                return updated_subscription
        return None
    
    async def fake_update_subscription(subscription_id, **kwargs):
        pass
    
    monkeypatch.setattr(subscriptions_repo, "get_subscription", fake_get_subscription)
    monkeypatch.setattr(subscriptions_repo, "update_subscription", fake_update_subscription)
    
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/subscriptions/{mock_subscription['id']}",
            json={"status": "pending_renewal", "autoRenew": False, "endDate": "2026-03-31"},
            headers={"X-CSRF-Token": super_admin_session.csrf_token}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == mock_subscription["id"]
    assert data["status"] == "pending_renewal"
    assert data["autoRenew"] is False
    assert data["endDate"] == "2026-03-31"
