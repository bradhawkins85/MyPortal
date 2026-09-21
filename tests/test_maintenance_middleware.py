from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.maintenance import MaintenanceMiddleware


def _client(monkeypatch, state):
    monkeypatch.setattr("app.security.maintenance.get_public_upgrade_state", lambda: state)
    app = FastAPI()
    page = Path(__file__).parents[1] / "app" / "static" / "upgrade.html"
    app.add_middleware(MaintenanceMiddleware, page_path=str(page))
    @app.get("/page")
    def page_route(): return {"ok": True}
    @app.post("/api/jobs")
    def job_route(): return {"created": True}
    @app.get("/upgrade-status")
    def status_route(): return state
    return TestClient(app)


def test_normal_traffic_continues(monkeypatch):
    with _client(monkeypatch, {"maintenance": False}) as client:
        assert client.get("/page").status_code == 200
        assert client.post("/api/jobs").status_code == 200


def test_maintenance_returns_page_and_structured_api_503(monkeypatch):
    state = {"maintenance": True, "phase": "draining", "message": "Draining", "upgrade_id": "up-3"}
    with _client(monkeypatch, state) as client:
        html = client.get("/page", headers={"Accept": "text/html"})
        assert html.status_code == 503
        assert html.headers["retry-after"] == "15"
        assert "Upgrade In Progress" in html.text
        api = client.post("/api/jobs", json={})
        assert api.status_code == 503
        assert api.headers["retry-after"] == "15"
        assert api.json()["error"] == "upgrade_in_progress"
        assert client.get("/upgrade-status").status_code == 200


def test_upgrade_page_preserves_return_url_and_has_bounded_backoff():
    page = (Path(__file__).parents[1] / "app" / "static" / "upgrade.html").read_text()
    assert "location.pathname+location.search+location.hash" in page
    assert "location.replace(original)" in page
    assert "Math.min(30000" in page
