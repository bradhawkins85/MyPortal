"""Continuation keeps ticket evidence and only dispatches allowlisted log reads."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.routes import tickets as routes
from app.schemas.tray import TicketReplyTroubleshootRequest
from app.services import ai_troubleshooter as ts
from app.services import tray as tray_service
from tests.test_ai_troubleshooter import TICKET, ASSET, DEVICE, LLM


@pytest.fixture
def setup_route(monkeypatch):
    reply = {"id": 8, "ticket_id": 77, "author_id": None,
             "author_email": tray_service.TROUBLESHOOT_AGENT_EMAIL,
             "body": "<p>Previous errors: 3 at 09:00 UTC</p>"}
    monkeypatch.setattr(routes.tickets_repo, "get_ticket", AsyncMock(return_value=TICKET))
    monkeypatch.setattr(routes.tickets_repo, "list_ticket_assets", AsyncMock(return_value=[{**ASSET, "tray_device_uid": "dev-uid"}]))
    monkeypatch.setattr(routes.tray_repo, "get_device_by_uid", AsyncMock(return_value=DEVICE))
    monkeypatch.setattr(routes.ai_consent, "is_ai_allowed_for_ticket", AsyncMock(return_value=True))
    monkeypatch.setattr(tray_service, "build_troubleshoot_llm_config", lambda model=None: LLM)
    monkeypatch.setattr(routes.tickets_repo, "get_reply_by_id", AsyncMock(return_value=reply))
    monkeypatch.setattr(routes.tickets_repo, "list_replies", AsyncMock(return_value=[reply, {"id": 9, "body": "Driver updated; drops persist"}]))
    started = AsyncMock()
    monkeypatch.setattr(ts, "start_reply_troubleshoot", started)
    return reply, started


def invoke(context=None):
    return asyncio.run(routes.troubleshoot_ticket_reply(
        ticket_id=77, reply_id=8,
        payload=TicketReplyTroubleshootRequest(asset_id=1, context=context),
        current_user={"id": 1},
    ))


@pytest.mark.parametrize("feedback", [None, "Errors reduced after driver update"])
def test_followup_keeps_selected_reply_and_latest_updates(setup_route, feedback):
    _, started = setup_route
    assert invoke(feedback).status == "started"
    args = started.call_args.kwargs
    assert args["reply_id"] == 8
    assert "Previous errors: 3" in args["problem"]
    assert "Driver updated; drops persist" in args["problem"]
    assert args["feedback"] == feedback
    if feedback:
        assert feedback in args["problem"]


@pytest.mark.parametrize("changes,code", [
    ({"ticket_id": 999}, 404),
    ({"author_id": 1}, 409),
    ({"author_email": "human@example.com"}, 409),
])
def test_followup_rejects_foreign_or_non_agent_reply(setup_route, changes, code):
    reply, started = setup_route
    reply.update(changes)
    with pytest.raises(HTTPException) as exc:
        invoke()
    assert exc.value.status_code == code
    started.assert_not_called()


@pytest.mark.parametrize("case,code", [("consent", 403), ("company", 403), ("inactive", 409), ("unlinked", 404)])
def test_followup_validates_consent_and_asset_scope(setup_route, monkeypatch, case, code):
    _, started = setup_route
    if case == "consent":
        monkeypatch.setattr(routes.ai_consent, "is_ai_allowed_for_ticket", AsyncMock(return_value=False))
    elif case == "company":
        monkeypatch.setattr(routes.tray_repo, "get_device_by_uid", AsyncMock(return_value={**DEVICE, "company_id": 99}))
    elif case == "inactive":
        monkeypatch.setattr(routes.tray_repo, "get_device_by_uid", AsyncMock(return_value={**DEVICE, "status": "inactive"}))
    else:
        monkeypatch.setattr(routes.tickets_repo, "list_ticket_assets", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException) as exc:
        invoke()
    assert exc.value.status_code == code
    started.assert_not_called()


def test_followup_skips_research_and_only_dispatches_log_reads(monkeypatch):
    chat = AsyncMock(return_value=json.dumps({"summary": "Trend unknown; collect comparable logs", "steps": [], "log_requests": [
        {"source": "System", "reason": "Compare current errors", "hours": 1},
        {"source": "powershell:Restart-Computer", "reason": "unsafe", "hours": 1},
        {"source": "file:C:\\Windows\\secret.txt", "reason": "not allowed", "hours": 1},
    ]}))
    dispatch = AsyncMock(return_value=(42, True))
    research = AsyncMock(side_effect=AssertionError("Follow-up must not restart research"))
    monkeypatch.setattr(ts, "_chat", chat)
    monkeypatch.setattr(ts, "_note", AsyncMock())
    monkeypatch.setattr(ts.ai_consent, "is_ai_allowed_for_ticket_id", AsyncMock(return_value=True))
    monkeypatch.setattr(ts.knowledge_base_service, "search_articles", research)
    monkeypatch.setattr(ts, "split_stored_log_requests", AsyncMock(side_effect=lambda requests, **kwargs: ([], requests)))
    monkeypatch.setattr(tray_service, "dispatch_troubleshoot_command", dispatch)
    asyncio.run(ts.run_research_and_plan(ticket=TICKET, asset=ASSET, device=DEVICE,
        problem="Prior error baseline; technician update", llm=LLM, continuation=True))
    research.assert_not_called()
    assert chat.await_count == 1
    messages = str(chat.call_args.args[1])
    assert "errors increased, reduced" in messages
    assert "Only read-only checks" in messages
    payload = dispatch.call_args.kwargs["payload"]
    assert payload["mode"] == "collect_logs"
    assert [r["source"] for r in payload["log_requests"]] == ["System"]
    assert payload[tray_service.SERVER_CONTEXT_KEY]["continuation"] is True


def test_followup_analysis_retains_comparison_rules(monkeypatch):
    chat = AsyncMock(return_value=json.dumps({"summary": "Insufficient comparable evidence"}))
    monkeypatch.setattr(ts, "_chat", chat)
    monkeypatch.setattr(ts, "_note", AsyncMock())
    monkeypatch.setattr(ts.ai_consent, "is_ai_allowed_for_ticket_id", AsyncMock(return_value=True))
    asyncio.run(ts.analyse_collected_logs(ticket_id=77, llm=LLM, endpoint_label="Laptop",
        command={"payload_json": {"log_requests": [{"source": "System", "reason": "Compare errors"}],
        tray_service.SERVER_CONTEXT_KEY: {"continuation": True, "problem": "Previous errors: 3"}}},
        log_text="--- System ---\n2026-10-14 Error: disconnect"))
    messages = str(chat.call_args.args[1])
    assert "manual-change assessments" in messages
    assert "Previous errors: 3" in messages


def test_followup_stops_when_consent_is_withdrawn(monkeypatch):
    chat = AsyncMock()
    monkeypatch.setattr(ts, "_chat", chat)
    monkeypatch.setattr(ts, "_note", AsyncMock())
    monkeypatch.setattr(ts.ai_consent, "is_ai_allowed_for_ticket_id", AsyncMock(return_value=False))
    assert asyncio.run(ts.run_research_and_plan(ticket=TICKET, asset=ASSET, device=DEVICE,
        problem="Previous state", llm=LLM, continuation=True)) is None
    chat.assert_not_called()


def test_followup_endpoint_and_payload_validation(setup_route):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_helpdesk_technician] = lambda: {"id": 1}
    with TestClient(app) as client:
        response = client.post("/api/tickets/77/replies/8/troubleshoot", json={"asset_id": 1})
        assert response.status_code == 200
        assert response.json()["status"] == "started"
        assert client.post("/api/tickets/77/replies/8/troubleshoot", json={"asset_id": 0}).status_code == 422
        assert client.post("/api/tickets/77/replies/8/troubleshoot", json={"asset_id": 1, "context": "x" * 2001}).status_code == 422


def test_selected_old_reply_is_preserved_with_bounded_history(setup_route, monkeypatch):
    reply, started = setup_route
    updates = [{"id": i, "body": f"Update {i}"} for i in range(9, 49)]
    monkeypatch.setattr(routes.tickets_repo, "list_replies", AsyncMock(return_value=[reply, *updates]))
    invoke()
    problem = started.call_args.kwargs["problem"]
    assert "Previous errors: 3" in problem
    assert "Update 48" in problem
    assert "Update 9" not in problem
    assert "Earlier history is omitted" in problem
