"""Export/import of event automations as a single JSON document."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.requests import Request

from app import main
from app.features.automations import handlers
from app.repositories import automations as automation_repo
from app.services import audit as audit_service
from app.services import automation_transfer


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _event_automation(**overrides: Any) -> dict[str, Any]:
    record = {
        "id": 7,
        "name": "Escalate VIP tickets",
        "description": "Bump priority for VIP companies",
        "kind": "event",
        "execution_order": 3,
        "cadence": None,
        "cron_expression": None,
        "scheduled_time": None,
        "run_once": False,
        "trigger_event": "tickets.created",
        "trigger_filters": {"match": {"ticket.priority": "high"}},
        "action_module": None,
        "action_payload": {
            "actions": [
                {"module": "update-ticket", "payload": {"priority": "urgent"}},
                {"module": "ntfy", "payload": {"message": "VIP ticket {{ ticket.number }}"}},
            ]
        },
        "status": "active",
        "business_hours_mode": "pause",
        "business_hours_source": "company",
    }
    record.update(overrides)
    return record


def test_export_contains_complete_event_automation() -> None:
    document = automation_transfer.build_export(_event_automation())

    assert document["format"] == "myportal.automation"
    assert document["version"] == 1
    exported = document["automation"]
    assert exported["name"] == "Escalate VIP tickets"
    assert exported["trigger_event"] == "tickets.created"
    assert exported["trigger_filters"] == {"match": {"ticket.priority": "high"}}
    assert len(exported["action_payload"]["actions"]) == 2
    assert exported["business_hours_mode"] == "pause"
    assert "id" not in exported
    json.dumps(document)


def test_export_rejects_scheduled_automation() -> None:
    with pytest.raises(automation_transfer.AutomationImportError):
        automation_transfer.build_export(_event_automation(kind="scheduled"))


def test_round_trip_import_is_inactive_event_automation() -> None:
    document = json.loads(json.dumps(automation_transfer.build_export(_event_automation())))

    data = automation_transfer.parse_import(document)

    assert data["kind"] == "event"
    assert data["status"] == "inactive"
    assert data["execution_order"] == 0
    assert data["trigger_event"] == "tickets.created"
    assert data["action_payload"]["actions"][1]["module"] == "ntfy"
    assert data["business_hours_source"] == "company"


@pytest.mark.parametrize(
    "document, message",
    [
        ([], "not an exported automation"),
        ({"format": "other"}, "not a MyPortal automation export"),
        ({"format": "myportal.automation", "version": 99, "automation": {}}, "version"),
        ({"format": "myportal.automation", "version": 1}, "does not contain"),
        (
            {"format": "myportal.automation", "version": 1, "automation": {"kind": "scheduled", "name": "x"}},
            "Only event automations",
        ),
        (
            {"format": "myportal.automation", "version": 1, "automation": {"kind": "event", "name": "x"}},
            "no trigger event",
        ),
        (
            {
                "format": "myportal.automation",
                "version": 1,
                "automation": {"kind": "event", "name": "", "trigger_event": "tickets.created"},
            },
            "invalid",
        ),
    ],
)
def test_parse_import_rejects_bad_documents(document: Any, message: str) -> None:
    with pytest.raises(automation_transfer.AutomationImportError, match=message):
        automation_transfer.parse_import(document)


def test_unique_import_name() -> None:
    assert automation_transfer.unique_import_name("A", set()) == "A"
    assert automation_transfer.unique_import_name("A", {"A"}) == "A (imported)"
    assert (
        automation_transfer.unique_import_name("A", {"A", "A (imported)"})
        == "A (imported 2)"
    )


def _request(path: str, method: str = "GET") -> Request:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": method, "path": path, "headers": []}, receive)


@pytest.mark.anyio("asyncio")
async def test_admin_export_downloads_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(automation_repo, "get_automation", AsyncMock(return_value=_event_automation()))

    response = await handlers.admin_export_automation(7, _request("/admin/automations/7/export"))

    assert response.status_code == status.HTTP_200_OK
    assert response.media_type == "application/json"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="automation-escalate-vip-tickets.json"'
    )
    body = json.loads(response.body)
    assert body["automation"]["trigger_event"] == "tickets.created"


@pytest.mark.anyio("asyncio")
async def test_admin_import_creates_inactive_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(
        automation_repo,
        "list_automations",
        AsyncMock(return_value=[{"id": 7, "name": "Escalate VIP tickets"}]),
    )
    create_mock = AsyncMock(side_effect=lambda **kw: {"id": 12, **kw})
    monkeypatch.setattr(automation_repo, "create_automation", create_mock)
    audit_mock = AsyncMock()
    monkeypatch.setattr(audit_service, "record", audit_mock)

    content = json.dumps(automation_transfer.build_export(_event_automation())).encode()
    upload = UploadFile(
        io.BytesIO(content),
        filename="automation.json",
        headers=Headers({"content-type": "application/json"}),
    )
    request = _request("/admin/automations/import", method="POST")
    monkeypatch.setattr(request, "form", AsyncMock(return_value=FormData([("automation_file", upload)])))

    response = await handlers.admin_import_automation(request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/automations/12/edit"
    kwargs = create_mock.await_args.kwargs
    assert kwargs["name"] == "Escalate VIP tickets (imported)"
    assert kwargs["status"] == "inactive"
    assert kwargs["next_run_at"] is None
    assert kwargs["kind"] == "event"
    assert audit_mock.await_args.kwargs["action"] == "automation.import"


@pytest.mark.anyio("asyncio")
async def test_admin_import_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    create_mock = AsyncMock()
    monkeypatch.setattr(automation_repo, "create_automation", create_mock)
    upload = UploadFile(io.BytesIO(b"{not json"), filename="bad.json")
    request = _request("/admin/automations/import", method="POST")
    monkeypatch.setattr(request, "form", AsyncMock(return_value=FormData([("automation_file", upload)])))

    response = await handlers.admin_import_automation(request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/automations"
    create_mock.assert_not_awaited()


def test_automations_page_actions_menu() -> None:
    template = main.templates.env.loader.get_source(main.templates.env, "admin/automations.html")[0]
    assert '"label": "New scheduled automation", "type": "link", "href"' in template
    assert '"variant": "primary"' not in template.split("{% block header_actions %}")[1].split("{% endblock %}")[0]
    assert "data-automation-import-open" in template
    assert 'id="automation-import-modal"' in template
    assert "/admin/automations/{{ automation.id }}/export" in template


@pytest.mark.anyio("asyncio")
async def test_api_import_and_export(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone

    from fastapi import HTTPException

    from app.api.routes import automations as api

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(automation_repo, "list_automations", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        automation_repo,
        "create_automation",
        AsyncMock(
            side_effect=lambda **kw: {
                "id": 3, **kw, "last_run_at": None, "last_error": None,
                "created_at": now, "updated_at": now,
            }
        ),
    )
    monkeypatch.setattr(audit_service, "record", AsyncMock())

    document = automation_transfer.build_export(_event_automation())
    created = await api.import_automation(
        _request("/api/automations/import", "POST"), document, {"id": 1}
    )
    assert created.id == 3
    assert created.status == "inactive"

    with pytest.raises(HTTPException) as exc:
        await api.import_automation(_request("/api/automations/import", "POST"), {"format": "x"}, {"id": 1})
    assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    monkeypatch.setattr(automation_repo, "get_automation", AsyncMock(return_value=_event_automation()))
    exported = await api.export_automation(7, {"id": 1})
    assert exported["automation"]["name"] == "Escalate VIP tickets"
