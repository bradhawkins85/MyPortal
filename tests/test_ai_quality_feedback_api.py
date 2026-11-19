"""The shared 👍/👎 endpoint for any AI output."""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies.auth import get_current_user
from app.api.routes import agent as agent_routes
from app.api.routes import ai_quality as routes

USER = {"id": 5, "is_super_admin": False}
OUTPUT = {"id": 11, "user_id": 99, "feature": "ticket_ai_summary", "subject_type": "ticket", "subject_id": "7"}


@pytest.fixture
def calls(monkeypatch):
    recorded: dict[str, list] = {"saved": [], "audited": []}

    async def get_response(response_id):
        return OUTPUT if response_id == 11 else None

    async def can_rate(user, response, *, is_technician):
        return user["id"] == 5

    async def is_technician(user):
        return True

    async def save_feedback(**kwargs):
        recorded["saved"].append(kwargs)

    async def response_counts(response_id):
        return {"helpful": 1, "unhelpful": 0}

    async def audit(**kwargs):
        recorded["audited"].append(kwargs)

    monkeypatch.setattr(routes.quality_repo, "get_response", get_response)
    monkeypatch.setattr(routes.quality_repo, "save_feedback", save_feedback)
    monkeypatch.setattr(routes.quality_repo, "response_counts", response_counts)
    monkeypatch.setattr(routes.access, "can_rate", can_rate)
    monkeypatch.setattr(routes.access, "is_technician", is_technician)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    return recorded


def _client(user=USER):
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(agent_routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://portal.example.com")


@pytest.mark.anyio
async def test_json_vote_saves_and_returns_counts(calls):
    async with _client() as client:
        response = await client.post("/api/ai-quality/feedback", json={"response_id": 11, "rating": "down",
                                                                      "reason": "unrelated"})
    assert response.status_code == 200
    assert response.json() == {"response_id": 11, "rating": "down", "helpful": 1, "unhelpful": 0}
    assert calls["saved"] == [{"response_id": 11, "user_id": 5, "rating": "down", "reason": "unrelated",
                               "comment": None, "is_technician": True}]
    assert calls["audited"][0]["action"] == "ai_quality.feedback"
    assert calls["audited"][0]["metadata"]["feature"] == "ticket_ai_summary"


@pytest.mark.anyio
async def test_null_rating_clears_vote(calls):
    async with _client() as client:
        response = await client.post("/api/ai-quality/feedback", json={"response_id": 11, "rating": None})
    assert response.status_code == 200
    assert calls["saved"][0]["rating"] is None


@pytest.mark.anyio
async def test_unknown_reason_is_rejected(calls):
    async with _client() as client:
        response = await client.post("/api/ai-quality/feedback", json={"response_id": 11, "rating": "down",
                                                                      "reason": "meh"})
    assert response.status_code == 422
    assert calls["saved"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("response_id,user", [(11, {"id": 40}), (12, USER)])
async def test_unrateable_or_missing_output_is_not_found(calls, response_id, user):
    async with _client(user) as client:
        response = await client.post("/api/ai-quality/feedback", json={"response_id": response_id, "rating": "up"})
    assert response.status_code == 404
    assert calls["saved"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("return_to,expected", [
    ("/tickets/7", "/tickets/7"),
    ("https://evil.example", "/"),
    ("//evil.example", "/"),
    ("/\\evil.example", "/"),
    ("", "/"),
    ("/\t/evil.example", "/"),
    ("/\n/evil.example", "/"),
    ("/%2F/evil.example", "/"),
    ("/tickets/7?tab=ai", "/tickets/7?tab=ai"),
])
async def test_form_post_redirects_back_safely(calls, return_to, expected):
    async with _client() as client:
        response = await client.post("/api/ai-quality/feedback", data={
            "response_id": "11", "rating": "up", "return_to": return_to, "_csrf": "x",
        })
    assert response.status_code == 303
    assert response.headers["location"] == expected
    assert calls["saved"][0]["rating"] == "up"


@pytest.mark.anyio
async def test_agent_feedback_path_still_works(calls):
    async with _client() as client:
        response = await client.post("/api/agent/feedback", json={"response_id": 11, "rating": "up"})
    assert response.status_code == 204
    assert calls["saved"][0]["rating"] == "up"


def test_endpoint_is_documented():
    app = FastAPI()
    app.include_router(routes.router)
    operation = app.openapi()["paths"]["/api/ai-quality/feedback"]["post"]
    assert "AI Quality" in operation["tags"]
    assert operation["summary"]


def test_rating_api_is_off_when_ai_quality_is_disabled():
    from app.services.component_availability import ComponentAvailability

    policy = ComponentAvailability(disabled_feature_packs=frozenset({"ai_quality"}))
    assert not policy.path_available("/api/ai-quality/feedback")
    assert ComponentAvailability(disabled_feature_packs=frozenset()).path_available("/api/ai-quality/feedback")


def _admin_client(monkeypatch):
    from app.api.dependencies.auth import require_super_admin

    async def by_feature():
        return [{"feature": "ticket_ai_summary", "outputs": 2, "helpful": 1, "unhelpful": 2, "technician_votes": 2,
                 "customer_votes": 1, "top_reason": "incomplete", "last_rated_at": "2026-11-18T03:00:00+00:00"}]

    async def empty():
        return []

    monkeypatch.setattr(agent_routes.quality_repo, "aggregate_by_feature", by_feature)
    monkeypatch.setattr(agent_routes.quality_repo, "aggregate", empty)
    monkeypatch.setattr(agent_routes.rag_relationship_repo, "relationship_feedback_aggregate", empty)
    app = FastAPI()
    app.include_router(agent_routes.router)
    app.dependency_overrides[require_super_admin] = lambda: {"id": 1, "is_super_admin": True}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://portal.example.com")


@pytest.mark.anyio
async def test_summary_includes_every_feature(monkeypatch):
    async with _admin_client(monkeypatch) as client:
        body = (await client.get("/api/agent/quality/summary")).json()
    assert body["features"][0]["feature"] == "ticket_ai_summary"
    assert body["features"][0]["label"] == "Ticket AI summary"
    assert "groups" in body and "related_feedback_groups" in body


@pytest.mark.anyio
async def test_csv_export_is_by_feature(monkeypatch):
    async with _admin_client(monkeypatch) as client:
        text = (await client.get("/api/agent/quality/export.csv")).text
    lines = text.strip().splitlines()
    assert lines[0] == ("feature,label,outputs,helpful,unhelpful,technician_votes,customer_votes,"
                        "top_reason,last_rated_at")
    assert lines[1].startswith("ticket_ai_summary,Ticket AI summary,2,1,2,2,1,incomplete,")


@pytest.mark.anyio
async def test_multipart_post_with_a_file_is_rejected_cleanly(calls):
    async with _client() as client:
        response = await client.post(
            "/api/ai-quality/feedback",
            data={"rating": "up", "return_to": "/tickets/7"},
            files={"response_id": ("x.txt", b"11", "text/plain")},
        )
    assert response.status_code == 422
    assert "x.txt" not in response.text
    assert calls["saved"] == []
