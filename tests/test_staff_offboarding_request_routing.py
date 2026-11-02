"""Regression tests for submitting staff offboarding requests from the portal.

The core ``/api/staff/{id}/offboarding/request`` API-key route shares its path
with the staff page's session-authenticated route, so session submissions must
be handed to the staff feature pack instead of failing with "API key required".
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from app.api.routes import staff as staff_api
from app.features.staff import handlers
from app.services import m365 as m365_service


def _make_request(body: dict, headers: dict[str, str] | None = None) -> Request:
    raw = json.dumps(body).encode()
    header_list = [(b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        header_list.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/staff/44/offboarding/request",
        "headers": header_list,
        "query_string": b"",
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def test_session_offboarding_request_is_handled_by_staff_pack(monkeypatch):
    sentinel = object()
    pack_handler = AsyncMock(return_value=sentinel)
    api_key_check = AsyncMock()
    monkeypatch.setattr(staff_api, "_staff_pack_offboarding_handler", lambda: pack_handler)
    monkeypatch.setattr(staff_api, "require_api_key", api_key_check)
    request = _make_request({"offboardingType": "Resignation"})

    result = asyncio.run(
        staff_api.request_staff_offboarding_endpoint(staff_id=44, request=request, _=None)
    )

    assert result is sentinel
    pack_handler.assert_awaited_once_with(44, request)
    api_key_check.assert_not_awaited()


def test_staff_pack_offboarding_handler_resolves_pack_handler():
    handler = staff_api._staff_pack_offboarding_handler()
    assert handler.__name__ == "request_staff_offboarding"
    assert handler.__module__ == "app.features.staff.handlers"


def test_api_key_offboarding_request_validates_documented_body(monkeypatch):
    record = {"id": 12, "allowed_company_ids": [9]}
    monkeypatch.setattr(staff_api, "require_api_key", AsyncMock(return_value=record))
    core_handler = AsyncMock(return_value="ok")
    monkeypatch.setattr(staff_api, "request_staff_offboarding", core_handler)
    request = _make_request(
        {"dateOffboarded": "2026-08-10T12:00:00+10:00", "offboardingType": "Resignation"},
        headers={"X-API-Key": "key"},
    )

    result = asyncio.run(
        staff_api.request_staff_offboarding_endpoint(staff_id=44, request=request, _=None)
    )

    assert result == "ok"
    kwargs = core_handler.await_args.kwargs
    assert kwargs["api_key_record"] is record
    assert kwargs["payload"].offboarding_type == "Resignation"


def test_api_key_offboarding_request_rejects_invalid_body(monkeypatch):
    monkeypatch.setattr(staff_api, "require_api_key", AsyncMock(return_value={"id": 12}))
    request = _make_request({"offboardingType": "Resignation"}, headers={"X-API-Key": "key"})

    with pytest.raises(RequestValidationError):
        asyncio.run(
            staff_api.request_staff_offboarding_endpoint(staff_id=44, request=request, _=None)
        )


def test_core_offboarding_logic_still_requires_api_key():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(staff_api.request_staff_offboarding(staff_id=44, payload=None))
    assert exc.value.status_code == 401


STAFF_RECORD = {
    "id": 44,
    "company_id": 1,
    "first_name": "Alex",
    "last_name": "Smith",
    "email": "alex@example.com",
    "enabled": True,
    "is_ex_staff": False,
    "account_action": None,
    "department": None,
}

GROUPS = [
    {"id": "g1", "display_name": "Sales", "mail": "sales@example.com", "kind": "Distribution list"},
    {"id": "g2", "display_name": "Partner", "mail": "team@partner.com", "kind": "Distribution list"},
]


def _patch_offboarding_context(monkeypatch, *, groups=GROUPS):
    async def fake_load_staff_context(request, **kwargs):
        return (
            {"id": 10, "is_super_admin": False},
            {"staff_permission": 3},
            {"id": 1, "email_domains": ["example.com"]},
            3,
            1,
            None,
        )

    monkeypatch.setattr(handlers, "_load_staff_context", fake_load_staff_context)
    monkeypatch.setattr(
        handlers.staff_repo, "get_staff_by_id", AsyncMock(return_value=dict(STAFF_RECORD))
    )
    monkeypatch.setattr(
        handlers.m365_service, "get_credentials", AsyncMock(return_value={"tenant_id": "t"})
    )
    list_groups = AsyncMock(return_value=groups)
    monkeypatch.setattr(handlers.m365_service, "list_mail_forward_groups", list_groups)
    update_mock = AsyncMock(
        side_effect=lambda staff_id, **kwargs: {
            **STAFF_RECORD,
            "account_action": kwargs["account_action"],
            "offboarding_email_forward_to": kwargs["offboarding_email_forward_to"],
        }
    )
    monkeypatch.setattr(handlers.staff_repo, "update_staff", update_mock)
    monkeypatch.setattr(
        handlers.staff_onboarding_workflow_service,
        "notify_staff_approval_requested",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(handlers.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(
        handlers.staff_workflow_repo,
        "get_company_workflow_policy",
        AsyncMock(return_value={"workflow_key": "offboarding"}),
    )
    monkeypatch.setattr(
        handlers.staff_workflow_repo, "create_or_reset_execution", AsyncMock()
    )
    return update_mock


def _offboarding_request(body: dict):
    payload = {
        "offboardingType": "Resignation",
        "requestedAt": "2026-11-20T09:00",
        "requestedTimezone": "UTC",
        **body,
    }
    return SimpleNamespace(json=AsyncMock(return_value=payload))


def test_offboarding_request_can_forward_to_distribution_list(monkeypatch):
    update_mock = _patch_offboarding_context(monkeypatch)

    response = asyncio.run(
        handlers.request_staff_offboarding(
            44, _offboarding_request({"emailForwardToAddress": "Sales@Example.com"})
        )
    )

    assert response.status_code == 200
    assert update_mock.await_args.kwargs["offboarding_email_forward_to"] == "sales@example.com"


@pytest.mark.parametrize("address", ["team@partner.com", "nobody@example.com"])
def test_offboarding_request_rejects_unknown_distribution_list(monkeypatch, address):
    update_mock = _patch_offboarding_context(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            handlers.request_staff_offboarding(
                44, _offboarding_request({"emailForwardToAddress": address})
            )
        )

    assert exc.value.status_code == 400
    update_mock.assert_not_awaited()


def test_offboarding_request_reports_m365_failure_for_distribution_list(monkeypatch):
    _patch_offboarding_context(monkeypatch)
    monkeypatch.setattr(
        handlers.m365_service,
        "list_mail_forward_groups",
        AsyncMock(side_effect=m365_service.M365Error("boom")),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            handlers.request_staff_offboarding(
                44, _offboarding_request({"emailForwardToAddress": "sales@example.com"})
            )
        )

    assert exc.value.status_code == 502


def test_forward_groups_endpoint_lists_company_domain_groups(monkeypatch):
    _patch_offboarding_context(monkeypatch)

    response = asyncio.run(
        handlers.staff_offboarding_forward_groups(44, SimpleNamespace())
    )

    data = json.loads(response.body)
    assert [group["mail"] for group in data["groups"]] == ["sales@example.com"]


def test_forward_groups_endpoint_is_empty_without_m365(monkeypatch):
    _patch_offboarding_context(monkeypatch)
    monkeypatch.setattr(handlers.m365_service, "get_credentials", AsyncMock(return_value=None))

    response = asyncio.run(
        handlers.staff_offboarding_forward_groups(44, SimpleNamespace())
    )

    assert json.loads(response.body) == {"groups": []}


def test_list_mail_forward_groups_labels_and_dedupes(monkeypatch):
    monkeypatch.setattr(m365_service, "acquire_access_token", AsyncMock(return_value="token"))
    graph_get = AsyncMock(
        return_value=[
            {"id": "1", "displayName": "Sales", "mail": "Sales@example.com", "groupTypes": [], "securityEnabled": False},
            {"id": "2", "displayName": "Team", "mail": "team@example.com", "groupTypes": ["Unified"], "securityEnabled": False},
            {"id": "3", "displayName": "Admins", "mail": "admins@example.com", "groupTypes": [], "securityEnabled": True},
            {"id": "4", "displayName": "Dup", "mail": "sales@example.com", "groupTypes": [], "securityEnabled": False},
            {"id": "5", "displayName": "No mail", "mail": None},
        ]
    )
    monkeypatch.setattr(m365_service, "_graph_get_all", graph_get)

    groups = asyncio.run(m365_service.list_mail_forward_groups(1))

    assert [(g["mail"], g["kind"]) for g in groups] == [
        ("admins@example.com", "Mail-enabled security group"),
        ("sales@example.com", "Distribution list"),
        ("team@example.com", "Microsoft 365 group"),
    ]
    assert "mailEnabled eq true" in graph_get.await_args.args[1]
