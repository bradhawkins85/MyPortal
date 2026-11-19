"""/admin/ai-quality shows ratings for every AI feature."""
from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
import pytest

from app.repositories import ai_quality as quality_repo
from app.services import ai_learning

ROWS = [
    {"feature": "rag_relationships", "outputs": 4, "helpful": 1, "unhelpful": 3, "technician_votes": 4,
     "customer_votes": 0, "top_reason": "unrelated", "last_rated_at": "2026-11-18T03:00:00+00:00"},
    {"feature": "legacy_x", "outputs": 1, "helpful": 0, "unhelpful": 0, "technician_votes": 0,
     "customer_votes": 0, "top_reason": None, "last_rated_at": None},
]


LINKS = [
    {"target_type": "knowledge_base", "target_id": "11", "title": "VPN setup", "unhelpful": 4, "helpful": 1,
     "tickets": 4, "ignored": False, "last_voted_at": "2026-11-18T03:00:00+00:00"},
    {"target_type": "products", "target_id": "12", "title": None, "unhelpful": 1, "helpful": 0,
     "tickets": 1, "ignored": True, "last_voted_at": "2026-11-17T03:00:00+00:00"},
]


def _get(monkeypatch, rows, overview, links=()):
    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_aggregate():
        return rows

    async def fake_overview(*, since):
        return overview

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return None

    async def fake_learned_links():
        return list(links)

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(quality_repo, "aggregate_by_feature", fake_aggregate)
    monkeypatch.setattr(quality_repo, "rating_overview", fake_overview)
    monkeypatch.setattr(quality_repo, "learned_links", fake_learned_links)
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


def test_page_lists_learned_links_with_undo_and_restore(monkeypatch):
    html = _get(monkeypatch, ROWS, {"ratings_since": 5, "helpful_pct": 20, "features_rated": 1}, LINKS).text
    assert 'id="ai-quality-learned-links"' in html
    assert "VPN setup" in html and "4 not helpful" in html and "1 helpful" in html and "on 4 tickets" in html
    assert "products 12" in html  # no title: falls back to the type and id
    assert 'action="/admin/ai-quality/links/ignore"' in html
    assert 'action="/admin/ai-quality/links/restore"' in html
    assert 'name="target_type" value="knowledge_base"' in html and 'name="target_id" value="11"' in html
    assert "Ignored" in html


def test_learned_links_empty_state(monkeypatch):
    html = _get(monkeypatch, [], {"ratings_since": 0, "helpful_pct": None, "features_rated": 0}).text
    assert "Nothing learned yet. When technicians mark a Related item 👍 or 👎, it appears here." in html


class _FormRequest:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


@pytest.fixture
def link_routes(monkeypatch):
    calls = {"ignored": [], "audit": [], "invalidated": 0}

    async def admin(request):
        return {"id": 1, "is_super_admin": True}, None

    async def set_ignored(target_type, target_id, *, ignored):
        calls["ignored"].append((target_type, target_id, ignored))
        return 3

    async def links():
        return LINKS

    async def audit(**kwargs):
        calls["audit"].append(kwargs)

    def invalidate():
        calls["invalidated"] += 1

    monkeypatch.setattr(main_module, "_require_super_admin_page", admin)
    monkeypatch.setattr(quality_repo, "set_link_learning_ignored", set_ignored)
    monkeypatch.setattr(quality_repo, "learned_links", links)
    monkeypatch.setattr(main_module.audit_service, "record", audit)
    monkeypatch.setattr(ai_learning, "invalidate_cache", invalidate)

    async def requeue(target_type, target_id):
        calls.setdefault("requeued", []).append((target_type, target_id))
        return 2

    monkeypatch.setattr(main_module.rag_relationship_repo, "requeue_ticket_links_for_target", requeue)
    real_flash_redirect = main_module.flash_redirect

    def flash(url, message, variant="info", **kwargs):
        calls["flash"] = (message, variant)
        return real_flash_redirect(url, message, variant, **kwargs)

    monkeypatch.setattr(main_module, "flash_redirect", flash)
    return calls


@pytest.mark.anyio
@pytest.mark.parametrize("route,ignored,action,message", [
    ("admin_ai_quality_ignore_link", True, "ai_quality.link_learning.ignore",
     "MyPortal will stop using feedback on VPN setup."),
    ("admin_ai_quality_restore_link", False, "ai_quality.link_learning.restore",
     "MyPortal will use feedback on VPN setup again."),
])
async def test_undo_and_restore_routes(link_routes, route, ignored, action, message):
    response = await getattr(main_module, route)(_FormRequest({"target_type": "knowledge_base", "target_id": "11"}))
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/ai-quality#ai-quality-learned-links"
    assert link_routes["ignored"] == [("knowledge_base", "11", ignored)]
    assert link_routes["invalidated"] == 1
    assert link_routes["requeued"] == [("knowledge_base", "11")]
    assert link_routes["audit"][0]["action"] == action
    assert link_routes["audit"][0]["metadata"] == {"target_type": "knowledge_base", "target_id": "11", "votes": 3}
    assert link_routes["flash"] == (message, "success")


@pytest.mark.anyio
async def test_link_routes_require_super_admin(link_routes, monkeypatch):
    from fastapi.responses import RedirectResponse

    async def not_admin(request):
        return None, RedirectResponse("/login", status_code=303)

    monkeypatch.setattr(main_module, "_require_super_admin_page", not_admin)
    response = await main_module.admin_ai_quality_ignore_link(
        _FormRequest({"target_type": "knowledge_base", "target_id": "11"}))
    assert response.headers["location"] == "/login"
    assert link_routes["ignored"] == []


@pytest.mark.anyio
async def test_undo_still_works_when_rechecking_links_fails(link_routes, monkeypatch):
    async def broken(target_type, target_id):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(main_module.rag_relationship_repo, "requeue_ticket_links_for_target", broken)
    response = await main_module.admin_ai_quality_ignore_link(
        _FormRequest({"target_type": "knowledge_base", "target_id": "11"}))
    assert response.status_code == 303
    assert link_routes["ignored"] == [("knowledge_base", "11", True)]
