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


@pytest.mark.anyio("asyncio")
async def test_ediscovery_reconnect_error_includes_entra_reason(monkeypatch):
    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={
        "tenant_id": "t", "client_id": "c", "client_secret": "s", "refresh_token": "rt",
    }))

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, data=None, **_kwargs):
            return httpx.Response(400, json={
                "error": "invalid_grant",
                "error_description": (
                    "AADSTS65001: The user or administrator has not consented to use the "
                    "application. Trace ID: abc Correlation ID: def"
                ),
            })

    monkeypatch.setattr(m365_service.httpx, "AsyncClient", FakeClient)

    with pytest.raises(M365Error) as exc_info:
        await m365_service._acquire_ediscovery_access_token(3)

    message = str(exc_info.value)
    assert "AADSTS65001: The user or administrator has not consented" in message
    assert "Trace ID" not in message
    assert "eDiscovery.ReadWrite.All" in message


@pytest.mark.anyio("asyncio")
async def test_ensure_ediscovery_permission_adds_graph_scope_and_keeps_roles(monkeypatch):
    scope_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    app_object = "11111111-2222-3333-4444-555555555555"
    required = [{"resourceAppId": m365_service._GRAPH_APP_ID,
                 "resourceAccess": [{"id": "graph-role", "type": "Role"}]}]
    patches: list = []

    async def fake_get(_token, url, **_kwargs):
        if "servicePrincipals" in url:
            return {"value": [{"id": "graph-sp", "appId": m365_service._GRAPH_APP_ID,
                               "oauth2PermissionScopes": [
                                   {"id": "other", "value": "User.Read"},
                                   {"id": scope_id, "value": "eDiscovery.ReadWrite.All"},
                               ]}]}
        return {"value": [{"id": app_object, "requiredResourceAccess": required}]}

    async def fake_patch(_token, url, payload, **_kwargs):
        patches.append((url, payload))
        return {}

    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={"client_id": "client"}))
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    monkeypatch.setattr(m365_service, "_graph_patch", fake_patch)

    assert await m365_service.ensure_ediscovery_delegated_permission(1, "token") is True
    (_url, payload), = patches
    graph = payload["requiredResourceAccess"][0]["resourceAccess"]
    assert {"id": "graph-role", "type": "Role"} in graph
    assert {"id": scope_id, "type": "Scope"} in graph

    patches.clear()
    required[0]["resourceAccess"] = graph
    assert await m365_service.ensure_ediscovery_delegated_permission(1, "token") is False
    assert patches == []


# ---------------------------------------------------------------------------
# Statistics report: mailboxes with matches
# ---------------------------------------------------------------------------

import io
import zipfile

REPORT_URL = (
    "https://apc.proxyservice.ediscovery.svc.cloud.microsoft/ediscovery/api/proxy/"
    "exportaedblobFileResult(abc)?downloadType=Report"
)


def _report_zip(csv_text: str, *, bom: bool = True, name: str = "Locations-2026-10-02_06-19-06.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Summary-2026-10-02_06-19-06.csv", "Name,Value\nItems,6\n")
        archive.writestr(name, (("﻿" if bom else "") + csv_text).encode("utf-8"))
    return buffer.getvalue()


def test_locations_with_matches_lists_only_positive_counts():
    report = _report_zip(
        "Location,Location type,Count,Size\n"
        "alice@bjp.example,Mailbox,4,1000\n"
        "bob@bjp.example,Mailbox,0,0\n"
        "carol@bjp.example,Mailbox,\"1,202\",900\n"
        "dave@bjp.example,Mailbox,2,10\n"
    )

    assert ediscovery.locations_with_matches(report) == [
        {"location": "carol@bjp.example", "count": 1202},
        {"location": "alice@bjp.example", "count": 4},
        {"location": "dave@bjp.example", "count": 2},
    ]


def test_locations_with_matches_requires_locations_csv():
    with pytest.raises(M365Error, match="no Locations CSV"):
        ediscovery.locations_with_matches(_report_zip("a,b\n", name="Other.csv"))


@pytest.mark.anyio("asyncio")
async def test_download_report_sends_purview_token_only_to_ediscovery_proxy(monkeypatch):
    seen: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, headers):
            seen.update(url=url, headers=headers)
            return httpx.Response(200, content=b"zip-bytes")

    monkeypatch.setattr(ediscovery.httpx, "AsyncClient", FakeClient)

    assert await ediscovery.download_report("dl-token", REPORT_URL) == b"zip-bytes"
    assert seen["headers"] == {"Authorization": "Bearer dl-token", "X-AllowWithAADToken": "true"}

    for bad in (
        "https://evil.example/ediscovery/api/proxy/x",
        "http://apc.proxyservice.ediscovery.svc.cloud.microsoft/x",
        "https://apc.proxyservice.ediscovery.svc.cloud.microsoft.evil.example/x",
    ):
        with pytest.raises(M365Error, match="not a Microsoft eDiscovery download host"):
            await ediscovery.download_report("dl-token", bad)


@pytest.mark.anyio("asyncio")
async def test_run_search_records_mailboxes_from_report(monkeypatch, repo):
    repo["request"] = {"id": 1, "company_id": 5, "search_name": "n", "content_match_query": "q"}
    monkeypatch.setattr(ediscovery, "ensure_case", AsyncMock(return_value=CASE))
    monkeypatch.setattr(ediscovery, "create_search", AsyncMock(return_value=SEARCH))
    monkeypatch.setattr(service, "_estimate", AsyncMock(return_value={
        "id": "e", "status": "succeeded", "indexedItemCount": 6,
        "reportFileMetadata": [{"downloadUrl": REPORT_URL, "fileName": "Reports.zip"}],
    }))
    monkeypatch.setattr(
        service.m365_service, "_acquire_ediscovery_download_token", AsyncMock(return_value="dl"),
    )
    download = AsyncMock(return_value=_report_zip("Location,Count\nalice@bjp.example,6\nbob@bjp.example,0\n"))
    monkeypatch.setattr(ediscovery, "download_report", download)

    await service._run_search(1)

    download.assert_awaited_once_with("dl", REPORT_URL)
    final = repo["updates"][-1]
    assert final["search_status"] == "completed"
    assert final["search_details"]["locations"] == [{"location": "alice@bjp.example", "count": 6}]


@pytest.mark.anyio("asyncio")
async def test_report_failure_does_not_fail_the_search(monkeypatch, repo):
    repo["request"] = {"id": 1, "company_id": 5, "search_name": "n", "content_match_query": "q"}
    monkeypatch.setattr(ediscovery, "ensure_case", AsyncMock(return_value=CASE))
    monkeypatch.setattr(ediscovery, "create_search", AsyncMock(return_value=SEARCH))
    monkeypatch.setattr(service, "_estimate", AsyncMock(return_value={
        "id": "e", "status": "succeeded", "indexedItemCount": 6,
        "reportFileMetadata": [{"downloadUrl": REPORT_URL}],
    }))
    monkeypatch.setattr(
        service.m365_service, "_acquire_ediscovery_download_token",
        AsyncMock(side_effect=M365Error("AADSTS65001: not consented", http_status=503)),
    )

    await service._run_search(1)

    final = repo["updates"][-1]
    assert final["search_status"] == "completed"
    assert "AADSTS65001" in final["search_details"]["locations_error"]
    assert "locations" not in final["search_details"]


@pytest.mark.anyio("asyncio")
async def test_managed_folder_assistant_targets_only_matched_mailboxes(monkeypatch, repo):
    repo["request"] = {
        "id": 8, "company_id": 2, "purge_details": {"purge_type": "HardDelete"},
        "search_details": {"locations": [
            {"location": "alice@bjp.example", "count": 4},
            {"location": "carol@bjp.example", "count": 2},
        ]},
    }
    monkeypatch.setattr(
        service.m365_service, "_acquire_exo_access_token", AsyncMock(return_value=("exo", "tenant")),
    )
    calls: list = []

    async def invoke(_token, _tenant, cmdlet, params=None, **_kwargs):
        calls.append((cmdlet, params))
        if params == {"Identity": "carol@bjp.example"}:
            raise M365Error("mailbox not found")
        return {}

    monkeypatch.setattr(service.m365_service, "_exo_invoke_command", invoke)

    await service._run_managed_folder_assistant(8)

    assert calls == [
        ("Start-ManagedFolderAssistant", {"Identity": "alice@bjp.example"}),
        ("Start-ManagedFolderAssistant", {"Identity": "carol@bjp.example"}),
    ]
    details = repo["updates"][-1]["purge_details"]
    assert details["managed_folder_assistant_scope"] == "matched_mailboxes"
    assert details["managed_folder_assistant_mailboxes"] == ["alice@bjp.example"]
    assert "carol@bjp.example" in details["managed_folder_assistant_failures"]


@pytest.mark.anyio("asyncio")
async def test_managed_folder_assistant_falls_back_to_all_mailboxes(monkeypatch, repo):
    repo["request"] = {"id": 8, "company_id": 2, "purge_details": {}, "search_details": {"locations_error": "x"}}
    monkeypatch.setattr(
        service.m365_service, "_acquire_exo_access_token", AsyncMock(return_value=("exo", "tenant")),
    )
    calls: list = []

    async def invoke(_token, _tenant, cmdlet, params=None, **_kwargs):
        calls.append(cmdlet)
        if cmdlet == "Get-Mailbox":
            return {"value": [{"UserPrincipalName": "a@x"}, {"UserPrincipalName": "b@x"}]}
        return {}

    monkeypatch.setattr(service.m365_service, "_exo_invoke_command", invoke)

    await service._run_managed_folder_assistant(8)

    assert calls == ["Get-Mailbox", "Start-ManagedFolderAssistant", "Start-ManagedFolderAssistant"]
    details = repo["updates"][-1]["purge_details"]
    assert details["managed_folder_assistant_scope"] == "all_mailboxes"
    assert details["managed_folder_assistant_mailboxes"] == 2


@pytest.mark.anyio("asyncio")
async def test_ensure_download_permission_creates_purview_service_principal(monkeypatch):
    scope_id = "99999999-8888-7777-6666-555555555555"
    app_object = "11111111-2222-3333-4444-555555555555"
    state = {"created": False}
    patches: list = []
    posts: list = []

    async def fake_get(_token, url, **_kwargs):
        if "servicePrincipals" in url:
            if not state["created"]:
                return {"value": []}
            return {"value": [{"id": "sp", "appId": m365_service.PURVIEW_EDISCOVERY_APP_ID,
                               "oauth2PermissionScopes": [{"id": scope_id, "value": "eDiscovery.Download.Read"}]}]}
        return {"value": [{"id": app_object, "requiredResourceAccess": []}]}

    async def fake_post(_token, url, payload):
        posts.append(payload)
        state["created"] = True
        return {}

    async def fake_patch(_token, url, payload, **_kwargs):
        patches.append(payload)
        return {}

    monkeypatch.setattr(m365_service, "get_credentials", AsyncMock(return_value={"client_id": "client"}))
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    monkeypatch.setattr(m365_service, "_graph_post", fake_post)
    monkeypatch.setattr(m365_service, "_graph_patch", fake_patch)

    assert await m365_service.ensure_ediscovery_download_permission(1, "token") is True
    assert posts == [{"appId": m365_service.PURVIEW_EDISCOVERY_APP_ID}]
    assert patches[0]["requiredResourceAccess"] == [{
        "resourceAppId": m365_service.PURVIEW_EDISCOVERY_APP_ID,
        "resourceAccess": [{"id": scope_id, "type": "Scope"}],
    }]
