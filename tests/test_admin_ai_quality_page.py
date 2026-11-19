"""/admin/ai-quality shows ratings for every AI feature."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.repositories import ai_quality as quality_repo

ROWS = [
    {"feature": "rag_relationships", "outputs": 4, "helpful": 1, "unhelpful": 3, "technician_votes": 4,
     "customer_votes": 0, "top_reason": "unrelated", "last_rated_at": "2026-11-18T03:00:00+00:00"},
    {"feature": "legacy_x", "outputs": 1, "helpful": 0, "unhelpful": 0, "technician_votes": 0,
     "customer_votes": 0, "top_reason": None, "last_rated_at": None},
]


def _get(monkeypatch, rows, overview):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_aggregate():
        return rows

    async def fake_overview(*, since):
        return overview

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return None

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(quality_repo, "aggregate_by_feature", fake_aggregate)
    monkeypatch.setattr(quality_repo, "rating_overview", fake_overview)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    # No context manager: skip startup/shutdown hooks that need a live database.
    return TestClient(main_module.app).get("/admin/ai-quality")


def test_page_lists_every_rated_feature(monkeypatch):
    response = _get(monkeypatch, ROWS, {"ratings_since": 5, "helpful_pct": 20, "features_rated": 1})
    assert response.status_code == 200
    html = response.text
    assert "data-ae-workspace" in html
    assert "RAG relationship evaluation" in html
    assert "legacy_x" in html
    assert "Unrelated" in html
    assert 'data-utc="2026-11-18T03:00:00+00:00"' in html
    assert "20%" in html
    assert "/static/js/asset_edit_sections.js" in html
    assert "/static/js/tables.js" in html


def test_page_explains_empty_state(monkeypatch):
    response = _get(monkeypatch, [], {"ratings_since": 0, "helpful_pct": None, "features_rated": 0})
    assert response.status_code == 200
    assert "No AI output has been rated yet. Ratings appear here as people use 👍/👎 on AI content." in response.text
