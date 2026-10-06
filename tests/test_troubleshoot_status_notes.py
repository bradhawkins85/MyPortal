"""Tests for AI troubleshooter status notes on tickets.

Covers the device progress callback (``/troubleshoot-status``), error
reporting on ``/troubleshoot-complete`` and the "requested" note written
when a technician starts the troubleshooter.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from app.api.routes import tickets as tickets_routes
from app.services import tray as tray_service


DEVICE = {"id": 5, "device_uid": "dev-uid", "hostname": "ws-01", "company_id": 3}


class _Recorder:
    def __init__(self) -> None:
        self.replies: list[dict] = []
        self.completed: list[tuple[int, str | None]] = []
        self.delivered: list[int] = []
        self.broadcasts: list[dict] = []


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    command = {
        "id": 11,
        "command": "troubleshoot",
        "device_id": 5,
        "status": "delivered",
        "payload_json": json.dumps({"ticket_id": 77}),
    }
    rec.command = command

    async def fake_get_command(command_id):
        return dict(command) if command_id == command["id"] else None

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "company_id": 3} if ticket_id == 77 else None

    async def fake_create_reply(**kwargs):
        rec.replies.append(kwargs)
        return {"id": len(rec.replies), **kwargs}

    async def fake_mark_completed(command_id, *, error=None):
        rec.completed.append((command_id, error))

    async def fake_mark_delivered(command_id, *, error=None):
        rec.delivered.append(command_id)

    async def fake_broadcast(**kwargs):
        rec.broadcasts.append(kwargs)

    from app.repositories import tickets as tickets_repo
    from app.repositories import tray as tray_repo
    from app.services import tickets as tickets_service

    monkeypatch.setattr(tray_repo, "get_command", fake_get_command)
    monkeypatch.setattr(tray_repo, "mark_command_completed", fake_mark_completed)
    monkeypatch.setattr(tray_repo, "mark_command_delivered", fake_mark_delivered)
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(tickets_service, "broadcast_ticket_event", fake_broadcast)
    return rec


def _status(**kwargs):
    params = {
        "ticket_id": 77,
        "command_id": 11,
        "stage": "received",
        "message": "",
        "endpoint": "",
        "device": DEVICE,
    }
    params.update(kwargs)
    return asyncio.run(tickets_routes.receive_troubleshoot_status(**params))


def test_status_update_adds_internal_note(recorder):
    response = _status(stage="collecting_logs", message="Collecting <logs>")
    assert response.status_code == 200
    assert len(recorder.replies) == 1
    reply = recorder.replies[0]
    assert reply["is_internal"] is True
    assert reply["author_display_name"] == "Troubleshooting Agent"
    assert "Collecting endpoint logs" in reply["body"]
    assert "ws-01" in reply["body"]
    assert "<logs>" not in reply["body"]
    assert recorder.broadcasts == [{"action": "reply", "ticket_id": 77}]
    assert recorder.completed == []


def test_status_received_marks_queued_command_delivered(recorder):
    recorder.command["status"] = "queued"
    _status(stage="received")
    assert recorder.delivered == [11]


def test_status_failed_marks_command_error(recorder):
    _status(stage="failed", message="upload failed")
    assert recorder.completed == [(11, "upload failed")]
    assert "Troubleshooter failed" in recorder.replies[0]["body"]


def test_status_rejects_unknown_stage(recorder):
    with pytest.raises(HTTPException) as exc:
        _status(stage="rm -rf")
    assert exc.value.status_code == 400
    assert recorder.replies == []


def test_status_rejects_finished_command(recorder):
    recorder.command["status"] = "completed"
    with pytest.raises(HTTPException) as exc:
        _status(stage="analysing")
    assert exc.value.status_code == 409


def test_status_rejects_other_device(recorder):
    with pytest.raises(HTTPException) as exc:
        _status(device={**DEVICE, "id": 99})
    assert exc.value.status_code == 403


def test_status_rejects_other_ticket(recorder):
    with pytest.raises(HTTPException) as exc:
        _status(ticket_id=78)
    assert exc.value.status_code == 400


class _Request:
    base_url = "https://portal.example/"


def test_complete_with_only_error_records_failure(recorder):
    response = asyncio.run(
        tickets_routes.receive_troubleshoot_result(
            ticket_id=77,
            request=_Request(),
            command_id=11,
            guidance="",
            endpoint="ws-01",
            error="llm: connection refused",
            log_bundle=None,
            device=DEVICE,
        )
    )
    assert response.status_code == 200
    body = recorder.replies[0]["body"]
    assert "connection refused" in body
    assert "No guidance produced" in body
    assert recorder.completed == [(11, "llm: connection refused")]


def test_complete_with_guidance_and_error_is_completed(recorder):
    asyncio.run(
        tickets_routes.receive_troubleshoot_result(
            ticket_id=77,
            request=_Request(),
            command_id=11,
            guidance="Restart the spooler.",
            endpoint="ws-01",
            error="log collection: denied",
            log_bundle=None,
            device=DEVICE,
        )
    )
    body = recorder.replies[0]["body"]
    assert "Restart the spooler." in body
    assert "log collection: denied" in body
    assert recorder.completed == [(11, None)]


def test_complete_requires_some_content(recorder):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            tickets_routes.receive_troubleshoot_result(
                ticket_id=77,
                request=_Request(),
                command_id=11,
                guidance="",
                endpoint="",
                error="",
                log_bundle=None,
                device=DEVICE,
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("delivered", "expected"),
    [(True, "Sent to the device"), (False, "queued")],
)
def test_requested_note(recorder, delivered, expected):
    asyncio.run(
        tray_service.add_troubleshoot_requested_note(
            ticket_id=77,
            command_id=11,
            delivered=delivered,
            device=DEVICE,
            model="llama3",
            requested_by={"email": "tech@example.com"},
            target_label="Reception PC",
        )
    )
    body = recorder.replies[0]["body"]
    assert recorder.replies[0]["is_internal"] is True
    assert "tech@example.com" in body
    assert "Reception PC" in body
    assert "command #11" in body
    assert expected in body


def test_requested_note_swallows_errors(monkeypatch):
    from app.repositories import tickets as tickets_repo

    async def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(tickets_repo, "create_reply", boom)
    asyncio.run(
        tray_service.add_troubleshoot_requested_note(
            ticket_id=77,
            command_id=11,
            delivered=True,
            device=DEVICE,
            model="llama3",
            requested_by=None,
        )
    )


# ---------------------------------------------------------------------------
# AI opt-out: the troubleshooter must fail closed for opted-out requesters
# ---------------------------------------------------------------------------


def _patch_consent(monkeypatch, allowed: bool):
    from app.services import ai_consent

    async def fake_ticket(ticket):
        return allowed

    async def fake_ticket_id(ticket_id):
        return allowed

    monkeypatch.setattr(ai_consent, "is_ai_allowed_for_ticket", fake_ticket)
    monkeypatch.setattr(ai_consent, "is_ai_allowed_for_ticket_id", fake_ticket_id)


def _fail_dispatch(monkeypatch):
    async def boom(**kwargs):
        raise AssertionError("troubleshoot command must not be dispatched")

    monkeypatch.setattr(tray_service, "dispatch_troubleshoot_command", boom)


def test_ticket_asset_troubleshoot_refused_when_requester_opted_out(recorder, monkeypatch):
    _patch_consent(monkeypatch, allowed=False)
    _fail_dispatch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            tickets_routes.troubleshoot_ticket_asset(
                ticket_id=77, asset_id=1, payload=None, current_user={"id": 1}
            )
        )
    assert exc.value.status_code == 403
    assert "opted out" in exc.value.detail
    assert recorder.replies == []


def test_device_troubleshoot_refused_when_requester_opted_out(recorder, monkeypatch):
    from app.api.routes import tray as tray_routes
    from app.repositories import tray as tray_repo
    from app.schemas.tray import TrayTroubleshootRequest

    async def fake_device(uid):
        return {**DEVICE, "status": "active"}

    monkeypatch.setattr(tray_repo, "get_device_by_uid", fake_device)
    _patch_consent(monkeypatch, allowed=False)
    _fail_dispatch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            tray_routes.run_troubleshoot(
                device_uid="dev-uid",
                payload=TrayTroubleshootRequest(ticket_id=77, prompt="Printer offline"),
                current_user={"id": 1},
            )
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("allowed", [True, False])
def test_queued_troubleshoot_rechecks_consent(recorder, monkeypatch, allowed):
    from app.repositories import tray as tray_repo

    _patch_consent(monkeypatch, allowed=allowed)
    sent: list[dict] = []

    async def fake_queued(device_id):
        return [
            {
                "id": 11,
                "command": "troubleshoot",
                "payload_json": json.dumps({"ticket_id": 77, "prompt": "secret"}),
            }
        ]

    async def fake_send(uid, payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(tray_repo, "get_queued_commands_for_device", fake_queued)
    monkeypatch.setattr(tray_service, "send_to_device", fake_send)

    result = asyncio.run(tray_service.deliver_queued_commands(DEVICE))
    if allowed:
        assert result["delivered"] == 1
        assert recorder.delivered == [11]
    else:
        assert sent == []
        assert result["delivered"] == 0
        assert recorder.completed == [(11, tray_service.TROUBLESHOOT_AI_OPT_OUT_DETAIL)]
