from __future__ import annotations

import asyncio
import base64
import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365_spam_purge as service
from app.services.m365 import M365Error, _jwt_appid


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_build_content_match_query_combines_safe_structured_fields():
    query = service.build_content_match_query(
        sender="attacker@example.com",
        subject='Urgent "invoice"',
        received_from=date(2026, 2, 1),
        received_to=date(2026, 2, 4),
        advanced_query=None,
    )
    assert query == (
        '(Received:02/01/2026..02/04/2026) AND '
        '(From:"attacker@example.com") AND (Subject:"Urgent \\"invoice\\"")'
    )


def test_build_content_match_query_requires_meaningful_criteria():
    with pytest.raises(ValueError, match="criterion"):
        service.build_content_match_query(
            sender=None, subject=" ", received_from=None, received_to=None,
            advanced_query=None,
        )


def test_build_content_match_query_rejects_control_characters_in_advanced_query():
    with pytest.raises(ValueError, match="control"):
        service.build_content_match_query(
            sender=None, subject=None, received_from=None, received_to=None,
            advanced_query="From:valid@example.com\nOR Subject:unsafe",
        )


def test_removed_item_count_reads_purview_result_summary():
    assert service._removed_item_count({"Results": "Purge Type: HardDelete; Item count: 1,204"}) == 1204


def test_start_purge_requires_completed_non_empty_search(monkeypatch):
    async def fake_get(_request_id):
        return {"id": 7, "company_id": 2, "search_status": "completed", "matched_items": 0,
                "purge_status": "not_started"}

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    with pytest.raises(ValueError, match="no messages"):
        asyncio.run(service.start_purge(7, 2))


def test_start_purge_is_company_scoped(monkeypatch):
    async def fake_get(_request_id):
        return {"id": 7, "company_id": 99}

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    with pytest.raises(LookupError):
        asyncio.run(service.start_purge(7, 2))


def test_scc_organization_uses_initial_onmicrosoft_domain(monkeypatch):
    async def fake_token(_company_id):
        return "graph-token"

    async def fake_graph(token, url):
        assert token == "graph-token"
        assert "$select=id,isInitial" in url
        return {"value": [
            {"id": "contoso.com", "isInitial": False},
            {"id": "ContosoTenant.onmicrosoft.com", "isInitial": True},
        ]}

    monkeypatch.setattr(service.m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(service.m365_service, "_graph_get", fake_graph)

    assert asyncio.run(service._scc_organization(7)) == "contosotenant.onmicrosoft.com"


def test_scc_organization_requires_initial_domain(monkeypatch):
    async def fake_token(_company_id):
        return "graph-token"

    async def fake_graph(_token, _url):
        return {"value": [{"id": "contoso.com", "isInitial": False}]}

    monkeypatch.setattr(service.m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(service.m365_service, "_graph_get", fake_graph)

    with pytest.raises(ValueError, match="Domain.Read.All"):
        asyncio.run(service._scc_organization(7))


def test_spam_purge_template_has_review_confirmation_and_live_refresh():
    source = open("app/templates/m365/spam_purge.html", encoding="utf-8").read()
    assert 'name="confirmation"' in source
    assert 'pattern="PURGE"' in source
    assert 'action="/m365/spam-purge/{{ item.id }}/retry"' in source
    assert "Retry search" in source
    assert "active_jobs" in source
    assert 'data-utc="{{ item.created_at }}"' in source


@pytest.mark.anyio("asyncio")
async def test_run_search_retries_new_compliance_search_on_org_container_error(monkeypatch):
    """_run_search retries New-ComplianceSearch when a transient org-container 500 is returned."""
    request = {
        "id": 1, "company_id": 5, "search_name": "MyPortal spam removal test",
        "action_name": "MyPortal spam removal test_Purge",
        "content_match_query": '(From:"x@example.com")',
    }

    update_calls: list[dict] = []

    async def fake_get(_id):
        return request

    async def fake_update(_id, data):
        update_calls.append(data)

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    monkeypatch.setattr(service.purge_repo, "update_request", fake_update)

    org_error = M365Error(
        "Security & Compliance New-ComplianceSearch failed (500): "
        "Could not find the organization container 'CN=abc,OU=Microsoft Exchange Hosted Organizations'",
        http_status=500,
    )
    invoke_calls: list[str] = []
    call_count = 0

    organizations: list[str | None] = []

    async def fake_scc_invoke(_token, _tenant, cmdlet, _params=None, **kwargs):
        nonlocal call_count
        invoke_calls.append(cmdlet)
        organizations.append(kwargs.get("organization"))
        if cmdlet == "New-ComplianceSearch":
            call_count += 1
            if call_count < 2:
                raise org_error
        if cmdlet == "Get-ComplianceSearch":
            return {"value": [{"Status": "Completed", "Items": 3, "Size": 100}]}
        return {}

    with (
        patch(
            "app.services.m365_spam_purge.m365_service._acquire_scc_access_token",
            new_callable=AsyncMock,
            return_value=("tok", "tenant-id"),
        ),
        patch(
            "app.services.m365_spam_purge._scc_organization",
            new_callable=AsyncMock,
            return_value="contoso.onmicrosoft.com",
        ),
        patch(
            "app.services.m365_spam_purge.m365_service._scc_invoke_command",
            side_effect=fake_scc_invoke,
        ),
        patch(
            "app.services.m365_spam_purge.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep,
    ):
        await service._run_search(1)

    assert invoke_calls.count("New-ComplianceSearch") == 2, "Expected one retry of New-ComplianceSearch"
    mock_sleep.assert_awaited_once_with(service._NEW_SEARCH_RETRY_BASE_SECONDS)
    search_status_updates = [d.get("search_status") for d in update_calls if "search_status" in d]
    assert "failed" not in search_status_updates, "Search should not be marked failed after a successful retry"
    assert "completed" in search_status_updates
    assert set(organizations) == {"contoso.onmicrosoft.com"}


@pytest.mark.anyio("asyncio")
async def test_run_search_exhausts_retries_and_marks_failed(monkeypatch):
    """_run_search marks the search as failed when all retries are exhausted."""
    request = {
        "id": 2, "company_id": 5, "search_name": "MyPortal spam removal exhausted",
        "action_name": "MyPortal spam removal exhausted_Purge",
        "content_match_query": '(From:"x@example.com")',
    }

    update_calls: list[dict] = []

    async def fake_get(_id):
        return request

    async def fake_update(_id, data):
        update_calls.append(data)

    monkeypatch.setattr(service.purge_repo, "get_request", fake_get)
    monkeypatch.setattr(service.purge_repo, "update_request", fake_update)

    org_error = M365Error(
        "Security & Compliance New-ComplianceSearch failed (500): "
        "Could not find the organization container",
        http_status=500,
    )

    async def always_fail(_token, _tenant, cmdlet, _params=None, **_kwargs):
        if cmdlet == "New-ComplianceSearch":
            raise org_error
        return {}

    with (
        patch(
            "app.services.m365_spam_purge.m365_service._acquire_scc_access_token",
            new_callable=AsyncMock,
            return_value=("tok", "tenant-id"),
        ),
        patch(
            "app.services.m365_spam_purge._scc_organization",
            new_callable=AsyncMock,
            return_value="contoso.onmicrosoft.com",
        ),
        patch(
            "app.services.m365_spam_purge.m365_service._scc_invoke_command",
            side_effect=always_fail,
        ),
        patch(
            "app.services.m365_spam_purge.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep,
    ):
        await service._run_search(2)

    assert mock_sleep.await_count == service._NEW_SEARCH_MAX_RETRIES, \
        "Should sleep before each retry but not after the final failed attempt"
    failed_updates = [d for d in update_calls if d.get("search_status") == "failed"]
    assert failed_updates, "Search should be marked failed after exhausting retries"
    assert "organization container" in failed_updates[-1].get("error_message", "")


def test_spam_purge_sidebar_requires_explicit_permission():
    source = open("app/templates/base.html", encoding="utf-8").read()

    assert source.count("{% if can_access_m365_spam_purge %}") == 2
    assert "or can_access_m365_spam_purge)" in source
    assert "{% if is_super_admin or is_helpdesk_technician %}" not in source


def test_spam_purge_permission_is_available_to_roles():
    from app.security.menu_permissions import catalogue_for_api

    permission = next(
        item for item in catalogue_for_api() if item["key"] == "menu.m365.spam_purge"
    )
    assert permission["admin_only"] is True
    assert permission["levels"] == ["none", "read", "write"]

def test_jwt_appid_extracts_appid_from_valid_jwt():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"appid": "my-client-id", "tid": "my-tenant"}).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}.fakesig"
    assert _jwt_appid(token) == "my-client-id"


def test_jwt_appid_returns_none_when_claim_absent():
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(json.dumps({"tid": "my-tenant"}).encode()).rstrip(b"=").decode()
    token = f"{header}.{payload}.fakesig"
    assert _jwt_appid(token) is None


def test_jwt_appid_returns_none_for_invalid_token():
    assert _jwt_appid("notavalidjwt") is None
    assert _jwt_appid("") is None
