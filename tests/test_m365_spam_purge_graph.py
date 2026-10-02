"""Spam Search & Purge through the Microsoft Graph eDiscovery API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import m365 as m365_service
from app.services import m365_ediscovery as ediscovery
from app.services import m365_spam_purge as service
from app.services.m365 import M365Error

CASE = "11111111-1111-1111-1111-111111111111"
SEARCH = "22222222-2222-2222-2222-222222222222"
OPERATION = f"{ediscovery.GRAPH_CASES_URL}/{CASE}/operations/op-1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def repo(monkeypatch):
    state: dict = {"request": None, "updates": []}

    async def get_request(_id):
        return state["request"]

    async def update_request(_id, values):
        state["updates"].append(values)

    monkeypatch.setattr(service.purge_repo, "get_request", get_request)
    monkeypatch.setattr(service.purge_repo, "update_request", update_request)
    monkeypatch.setattr(service.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        service.m365_service, "_acquire_ediscovery_access_token", AsyncMock(return_value="tok"),
    )
    return state


@pytest.mark.anyio("asyncio")
async def test_run_search_creates_graph_search_and_records_estimate(monkeypatch, repo):
    repo["request"] = {"id": 1, "company_id": 5, "search_name": "MyPortal spam removal x",
                       "content_match_query": '(From:"x@example.com")'}
    monkeypatch.setattr(ediscovery, "ensure_case", AsyncMock(return_value=CASE))
    create = AsyncMock(return_value=SEARCH)
    monkeypatch.setattr(ediscovery, "create_search", create)
    monkeypatch.setattr(ediscovery, "start_estimate", AsyncMock())
    estimates = [
        M365Error("not found", http_status=404),  # no previous run
        M365Error("not found", http_status=404),  # new run not registered yet
        {"id": "est-1", "status": "running"},
        {"id": "est-1", "status": "succeeded", "indexedItemCount": 7, "indexedItemsSize": 900},
    ]

    async def get_estimate(*_args):
        value = estimates.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(ediscovery, "get_estimate", get_estimate)

    await service._run_search(1)

    create.assert_awaited_once_with("tok", CASE, "MyPortal spam removal x", '(From:"x@example.com")')
    final = repo["updates"][-1]
    assert final["search_status"] == "completed"
    assert final["matched_items"] == 7
    assert final["matched_size"] == 900
    assert final["search_details"]["case_id"] == CASE
    assert final["search_details"]["search_id"] == SEARCH


@pytest.mark.anyio("asyncio")
async def test_run_search_records_graph_role_error_with_remediation(monkeypatch, repo):
    repo["request"] = {"id": 1, "company_id": 5, "search_name": "n", "content_match_query": "q"}
    monkeypatch.setattr(ediscovery, "ensure_case", AsyncMock(side_effect=M365Error(
        "Microsoft Graph eDiscovery case lookup failed (403): Forbidden", http_status=403,
    )))

    await service._run_search(1)

    final = repo["updates"][-1]
    assert final["search_status"] == "failed"
    assert final["error_message"].startswith("Microsoft Graph eDiscovery case lookup failed (403)")
    assert "eDiscovery Manager" in final["error_message"]
    assert "Search And Purge" in final["error_message"]


@pytest.mark.anyio("asyncio")
async def test_run_purge_submits_once_and_reports_remaining_from_reestimate(monkeypatch, repo):
    repo["request"] = {"id": 8, "company_id": 2, "matched_items": 12,
                       "search_details": {"case_id": CASE, "search_id": SEARCH}}
    start = AsyncMock(return_value=OPERATION)
    monkeypatch.setattr(ediscovery, "start_purge", start)
    operations = [{"status": "running"}, {"status": "succeeded"}]
    monkeypatch.setattr(ediscovery, "get_operation", AsyncMock(side_effect=lambda *_a: operations.pop(0)))
    monkeypatch.setattr(service, "_estimate", AsyncMock(return_value={"indexedItemCount": 2}))
    folder_assistant = AsyncMock()
    monkeypatch.setattr(service, "_run_managed_folder_assistant", folder_assistant)

    await service._run_purge(8)

    start.assert_awaited_once_with("tok", CASE, SEARCH)
    final = repo["updates"][-1]
    assert final["purge_status"] == "completed"
    assert final["removed_items"] == 10
    assert final["purge_details"]["remaining_items"] == 2
    assert final["purge_details"]["operation_url"] == OPERATION
    folder_assistant.assert_awaited_once_with(8)


@pytest.mark.anyio("asyncio")
async def test_purge_retry_reconciles_remote_success_without_second_purge(monkeypatch, repo):
    repo["request"] = {"id": 8, "company_id": 2, "matched_items": 12,
                       "search_details": {"case_id": CASE, "search_id": SEARCH},
                       "purge_details": {"operation_url": OPERATION}}
    start = AsyncMock()
    monkeypatch.setattr(ediscovery, "start_purge", start)
    monkeypatch.setattr(ediscovery, "get_operation", AsyncMock(return_value={"status": "succeeded"}))
    monkeypatch.setattr(service, "_estimate", AsyncMock(return_value={"indexedItemCount": 0}))
    monkeypatch.setattr(service, "_run_managed_folder_assistant", AsyncMock())

    await service._run_purge(8)

    start.assert_not_awaited()
    assert repo["updates"][-1]["removed_items"] == 12


@pytest.mark.anyio("asyncio")
async def test_purge_resubmits_after_remote_failure(monkeypatch, repo):
    repo["request"] = {"id": 8, "company_id": 2, "matched_items": 3,
                       "search_details": {"case_id": CASE, "search_id": SEARCH},
                       "purge_details": {"operation_url": OPERATION}}
    start = AsyncMock(return_value=OPERATION)
    monkeypatch.setattr(ediscovery, "start_purge", start)
    operations = [{"status": "failed"}, {"status": "succeeded"}]
    monkeypatch.setattr(ediscovery, "get_operation", AsyncMock(side_effect=lambda *_a: operations.pop(0)))
    monkeypatch.setattr(service, "_estimate", AsyncMock(side_effect=TimeoutError("slow")))
    monkeypatch.setattr(service, "_run_managed_folder_assistant", AsyncMock())

    await service._run_purge(8)

    start.assert_awaited_once()
    final = repo["updates"][-1]
    assert final["purge_status"] == "completed"
    assert final["purge_details"]["remaining_items_unknown"] is True


@pytest.mark.anyio("asyncio")
async def test_purge_requires_graph_search_ids(repo):
    repo["request"] = {"id": 8, "company_id": 2, "matched_items": 3, "search_details": {"Status": "Completed"}}

    await service._run_purge(8)

    final = repo["updates"][-1]
    assert final["purge_status"] == "failed"
    assert "run the search again" in final["error_message"]


class _FakeClient:
    responses: list = []
    calls: list = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, method, url, *, headers, json=None):
        self.calls.append((method, url, json))
        return self.responses.pop(0)


@pytest.mark.anyio("asyncio")
async def test_start_purge_posts_hard_delete_and_returns_operation(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.responses = [httpx.Response(202, headers={"Location": OPERATION})]
    monkeypatch.setattr(ediscovery.httpx, "AsyncClient", _FakeClient)

    assert await ediscovery.start_purge("tok", CASE, SEARCH) == OPERATION

    method, url, payload = _FakeClient.calls[0]
    assert method == "POST"
    assert url.endswith(f"/ediscoveryCases/{CASE}/searches/{SEARCH}/purgeData")
    assert payload == {"purgeType": "permanentlyDelete", "purgeAreas": "mailboxes"}


@pytest.mark.anyio("asyncio")
async def test_start_purge_rejects_non_graph_operation_url(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.responses = [httpx.Response(202, headers={"Location": "https://evil.example/op"})]
    monkeypatch.setattr(ediscovery.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(Exception):
        await ediscovery.start_purge("tok", CASE, SEARCH)


@pytest.mark.anyio("asyncio")
async def test_ensure_case_reuses_open_case_and_creates_when_missing(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.responses = [
        httpx.Response(200, json={"value": [
            {"id": "closed", "displayName": ediscovery.CASE_DISPLAY_NAME, "status": "closed"},
            {"id": "other", "displayName": "Someone else's case", "status": "active"},
        ]}),
        httpx.Response(201, json={"id": CASE}),
    ]
    monkeypatch.setattr(ediscovery.httpx, "AsyncClient", _FakeClient)

    assert await ediscovery.ensure_case("tok") == CASE
    assert _FakeClient.calls[1][0] == "POST"
    assert _FakeClient.calls[1][2]["displayName"] == ediscovery.CASE_DISPLAY_NAME


@pytest.mark.anyio("asyncio")
async def test_graph_error_keeps_code_and_message(monkeypatch):
    _FakeClient.responses = [httpx.Response(403, json={"error": {
        "code": "Forbidden", "message": "User is not a member of eDiscovery Manager",
    }})]
    monkeypatch.setattr(ediscovery.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(M365Error) as exc_info:
        await ediscovery.create_search("tok", CASE, "n", "q")

    assert exc_info.value.http_status == 403
    assert "Forbidden: User is not a member of eDiscovery Manager" in str(exc_info.value)


@pytest.mark.anyio("asyncio")
async def test_ediscovery_token_uses_delegated_refresh_token(monkeypatch):
    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={
        "tenant_id": "t", "client_id": "c", "client_secret": "s", "refresh_token": "rt",
    }))
    exchange = AsyncMock(return_value=("graph-token", None, None))
    monkeypatch.setattr(m365_service, "_exchange_token", exchange)

    assert await m365_service._acquire_ediscovery_access_token(3) == "graph-token"
    assert exchange.await_args.kwargs["scope"] == m365_service.EDISCOVERY_SCOPE
    assert exchange.await_args.kwargs["refresh_token"] == "rt"


@pytest.mark.anyio("asyncio")
async def test_ediscovery_token_requires_reconnect_without_consent(monkeypatch):
    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={
        "tenant_id": "t", "client_id": "c", "client_secret": "s", "refresh_token": "rt",
    }))
    error = M365Error("consent_required")
    error.failure_kind = "reauthentication_required"
    monkeypatch.setattr(m365_service, "_exchange_token", AsyncMock(side_effect=error))

    with pytest.raises(M365Error, match="eDiscovery.ReadWrite.All"):
        await m365_service._acquire_ediscovery_access_token(3)


def test_connect_scope_requests_ediscovery_consent():
    assert m365_service.EDISCOVERY_SCOPE in m365_service.CONNECT_SCOPE.split()
