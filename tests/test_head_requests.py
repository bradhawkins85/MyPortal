"""Test HEAD request support for monitored routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import db
from app.main import app
from app.services.scheduler import scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    """Mock startup hooks to avoid database connections in tests."""
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

    monkeypatch.setattr(db, 'connect', fake_connect)
    monkeypatch.setattr(db, 'disconnect', fake_disconnect)
    monkeypatch.setattr(db, 'run_migrations', fake_run_migrations)
    monkeypatch.setattr(scheduler_service, 'start', fake_start)
    monkeypatch.setattr(scheduler_service, 'stop', fake_stop)


def test_invoices_head_request():
    """Test that HEAD requests to /invoices don't return 405."""
    with TestClient(app, follow_redirects=False) as client:
        response = client.head("/invoices")
    
    # Should not return 405 Method Not Allowed
    assert response.status_code != 405, "HEAD requests should be supported"
    # Expect redirect to login (303) since no authentication
    # or other appropriate status, but not 405
    assert response.status_code in {200, 303, 401, 403}


def test_admin_service_status_head_request():
    """Test that HEAD requests to /admin/service-status don't return 405."""
    with TestClient(app, follow_redirects=False) as client:
        response = client.head("/admin/service-status")
    
    # Should not return 405 Method Not Allowed
    assert response.status_code != 405, "HEAD requests should be supported"
    # Expect redirect to login (303) or forbidden (403) since no authentication
    assert response.status_code in {200, 303, 401, 403}


def test_service_status_head_request():
    """Test that HEAD requests to /service-status don't return 405."""
    with TestClient(app, follow_redirects=False) as client:
        response = client.head("/service-status")
    
    # Should not return 405 Method Not Allowed
    assert response.status_code != 405, "HEAD requests should be supported"
    # Expect redirect to login (303) since no authentication
    assert response.status_code in {200, 303, 401, 403}
