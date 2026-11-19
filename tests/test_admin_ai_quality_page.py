"""/admin/ai-quality shows both AI search and ticket Related-item feedback."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.repositories import ai_quality as quality_repo
from app.repositories import rag_relationships as repo


def test_ai_quality_page_shows_ticket_related_feedback(monkeypatch):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_agent_aggregate():
        return []

    async def fake_related_aggregate():
        return [{
            "relationship_type": "RELATED", "target_source_type": "products", "evaluated_model": "model-a",
            "vote_count": 2, "helpful": 0, "unhelpful": 2, "average_relevance_score": 0.7,
            "last_voted_at": "2026-11-18T03:00:00+00:00",
        }]

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(quality_repo, "aggregate", fake_agent_aggregate)
    monkeypatch.setattr(repo, "relationship_feedback_aggregate", fake_related_aggregate)

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return None

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    # No context manager: skip startup/shutdown hooks that need a live database.
    response = TestClient(main_module.app).get("/admin/ai-quality")

    assert response.status_code == 200
    html = response.text
    assert "Ticket related items" in html
    assert "<td>products</td>" in html
    assert "<td>2</td><td>0</td><td>2</td><td>0.70</td>" in html
    assert 'data-utc="2026-11-18T03:00:00+00:00"' in html
    assert "No AI search answers have been recorded yet." in html
