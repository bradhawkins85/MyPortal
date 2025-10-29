from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData


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
    monkeypatch.setattr(
        main_module.change_log_service,
        "sync_change_log_sources",
        fake_change_log_sync,
    )
    monkeypatch.setattr(
        main_module.modules_service,
        "ensure_default_modules",
        fake_ensure_modules,
    )
    monkeypatch.setattr(
        main_module.automations_service,
        "refresh_all_schedules",
        fake_refresh_automations,
    )
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)


@pytest.fixture
def active_session(monkeypatch):
    now = datetime.now(timezone.utc)
    session = SessionData(
        id=1,
        user_id=10,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=1,
        pending_totp_secret=None,
    )

    async def fake_load_session(request, allow_inactive=False):
        request.state.session = session
        request.state.active_company_id = session.active_company_id
        return session

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    return session


@pytest.fixture
def cart_context(monkeypatch, active_session):
    async def fake_load_context(request, *, permission_field):
        return (
            {"id": active_session.user_id, "email": "user@example.com"},
            {"company_id": 1, "can_access_cart": True},
            {"id": 1, "name": "Example"},
            1,
            None,
        )

    monkeypatch.setattr(
        main_module,
        "_load_company_section_context",
        fake_load_context,
    )


def test_update_cart_quantities_success(monkeypatch, active_session, cart_context):
    recorded_updates: list[tuple[int, int, int]] = []
    recorded_removals: list[tuple[int, set[int]]] = []

    async def fake_get_item(session_id, product_id):
        return {"product_id": product_id, "quantity": 1, "product_name": "Widget"}

    async def fake_update_item_quantity(session_id, product_id, quantity):
        recorded_updates.append((session_id, product_id, quantity))

    async def fake_remove_items(session_id, product_ids):
        recorded_removals.append((session_id, set(product_ids)))

    async def fake_get_product_by_id(product_id, company_id=None):
        return {"id": product_id, "stock": 12}

    monkeypatch.setattr(main_module.cart_repo, "get_item", fake_get_item)
    monkeypatch.setattr(
        main_module.cart_repo,
        "update_item_quantity",
        fake_update_item_quantity,
    )
    monkeypatch.setattr(main_module.cart_repo, "remove_items", fake_remove_items)
    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/update",
            data={"quantity_5": "3", "_csrf": active_session.csrf_token},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    params = parse_qs(urlparse(location).query)
    assert params.get("cartMessage") == ["Quantities updated."]
    assert recorded_updates == [(active_session.id, 5, 3)]
    assert recorded_removals == []


def test_update_cart_zero_quantity_removes_item(monkeypatch, active_session, cart_context):
    recorded_removals: list[tuple[int, set[int]]] = []

    async def fake_remove_items(session_id, product_ids):
        recorded_removals.append((session_id, set(product_ids)))

    monkeypatch.setattr(main_module.cart_repo, "remove_items", fake_remove_items)
    async def fake_get_item(session_id, product_id):
        return {"product_id": product_id, "quantity": 2}

    async def fake_get_product_by_id(product_id, company_id=None):
        return {"id": product_id, "stock": 5}

    async def fake_update_item_quantity(session_id, product_id, quantity):
        return None

    monkeypatch.setattr(main_module.cart_repo, "get_item", fake_get_item)
    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )
    monkeypatch.setattr(
        main_module.cart_repo,
        "update_item_quantity",
        fake_update_item_quantity,
    )

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/update",
            data={"quantity_7": "0", "_csrf": active_session.csrf_token},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    params = parse_qs(urlparse(location).query)
    assert params.get("cartMessage") == ["Items removed."]
    assert recorded_removals == [(active_session.id, {7})]


def test_update_cart_exceeds_stock(monkeypatch, active_session, cart_context):
    recorded_updates: list[tuple[int, int, int]] = []
    recorded_removals: list[tuple[int, set[int]]] = []

    async def fake_get_item(session_id, product_id):
        return {"product_id": product_id, "quantity": 1, "product_name": "Widget"}

    async def fake_update_item_quantity(session_id, product_id, quantity):
        recorded_updates.append((session_id, product_id, quantity))

    async def fake_remove_items(session_id, product_ids):
        recorded_removals.append((session_id, set(product_ids)))

    async def fake_get_product_by_id(product_id, company_id=None):
        return {"id": product_id, "stock": 2}

    monkeypatch.setattr(main_module.cart_repo, "get_item", fake_get_item)
    monkeypatch.setattr(
        main_module.cart_repo,
        "update_item_quantity",
        fake_update_item_quantity,
    )
    monkeypatch.setattr(main_module.cart_repo, "remove_items", fake_remove_items)
    monkeypatch.setattr(
        main_module.shop_repo,
        "get_product_by_id",
        fake_get_product_by_id,
    )

    with TestClient(app, follow_redirects=False) as client:
        response = client.post(
            "/cart/update",
            data={"quantity_9": "5", "_csrf": active_session.csrf_token},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    params = parse_qs(urlparse(location).query)
    assert params.get("cartError") == [
        "Unable to increase some quantities due to limited stock."
    ]
    assert "cartMessage" not in params
    assert recorded_updates == []
    assert recorded_removals == []
