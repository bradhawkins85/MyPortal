import pytest
from fastapi.testclient import TestClient

from app.core.database import db
from app.main import app, PWA_BACKGROUND_COLOR, PWA_THEME_COLOR
from app.services.scheduler import scheduler_service


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

    monkeypatch.setattr(db, 'connect', fake_connect)
    monkeypatch.setattr(db, 'disconnect', fake_disconnect)
    monkeypatch.setattr(db, 'run_migrations', fake_run_migrations)
    monkeypatch.setattr(scheduler_service, 'start', fake_start)
    monkeypatch.setattr(scheduler_service, 'stop', fake_stop)


def test_manifest_exposes_expected_metadata():
    with TestClient(app) as client:
        response = client.get('/manifest.webmanifest')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/manifest+json')
    manifest = response.json()
    assert manifest['name'] == manifest['short_name'] == app.title
    assert manifest['theme_color'] == PWA_THEME_COLOR
    assert manifest['background_color'] == PWA_BACKGROUND_COLOR
    assert any(icon['sizes'] == '192x192' for icon in manifest['icons'])
    assert any(icon['sizes'] == '512x512' for icon in manifest['icons'])
    assert all(icon['type'] == 'image/svg+xml' for icon in manifest['icons'])


def test_service_worker_served_with_no_cache_headers():
    with TestClient(app) as client:
        response = client.get('/service-worker.js')

    assert response.status_code == 200
    assert 'javascript' in response.headers['content-type']
    cache_control = response.headers.get('cache-control', '')
    assert 'no-cache' in cache_control.lower()
    assert 'no-store' in cache_control.lower()
    assert response.headers.get('x-content-type-options') == 'nosniff'
    assert response.headers.get('service-worker-allowed') == '/'
