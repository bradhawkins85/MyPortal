"""Microsoft Graph eDiscovery (Purview) client used by Spam Search & Purge.

Microsoft supports eDiscovery search and purge through the Graph
``/security/cases/ediscoveryCases`` API.  Every call uses the delegated token of
the administrator who last connected the company, so Purview authorizes the
request against that user's eDiscovery Manager / Search And Purge roles.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.services import m365 as m365_service
from app.services.monitored_http import monitored_client

GRAPH_CASES_URL = "https://graph.microsoft.com/v1.0/security/cases/ediscoveryCases"
CASE_DISPLAY_NAME = "MyPortal spam removal"
CASE_DESCRIPTION = "Created by MyPortal Spam Search & Purge. Searches in this case are reviewed before purge."

# Graph ``ediscoveryCaseOperation.status`` values that will not change again.
TERMINAL_OPERATION_STATUSES = frozenset({"succeeded", "failed", "partiallysucceeded"})


def _segment(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise m365_service.M365Error("Microsoft Graph returned an object without an ID")
    return quote(text, safe="")


def _error_from_response(action: str, response: httpx.Response) -> m365_service.M365Error:
    """Build an error that keeps Graph's own code and message.

    eDiscovery failures are almost always role or licensing problems that Graph
    explains precisely (for example a missing eDiscovery Manager role); a bare
    status code would hide that.
    """
    code = message = ""
    try:
        error = (response.json() or {}).get("error") or {}
        code = str(error.get("code") or "")
        message = " ".join(str(error.get("message") or "").split())[:500]
    except Exception:  # noqa: BLE001 - non-JSON body; status alone remains useful
        pass
    detail = ": ".join(part for part in (code, message) if part)
    return m365_service.M365Error(
        f"Microsoft Graph eDiscovery {action} failed ({response.status_code})"
        + (f": {detail}" if detail else ""),
        http_status=response.status_code,
        graph_error_code=code or None,
    )


async def _request(
    token: str, method: str, url: str, action: str, payload: dict[str, Any] | None = None,
) -> httpx.Response:
    m365_service._validate_graph_url(url)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with monitored_client(httpx.AsyncClient, timeout=60) as client:
            response = await client.request(method, url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise m365_service.M365Error(
            f"Microsoft Graph eDiscovery {action} timed out ({type(exc).__name__})"
        ) from exc
    except httpx.NetworkError as exc:
        raise m365_service.M365Error(
            f"Microsoft Graph eDiscovery {action} network error ({type(exc).__name__})"
        ) from exc
    if response.status_code not in (200, 201, 202, 204):
        raise _error_from_response(action, response)
    return response


def _json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def ensure_case(token: str) -> str:
    """Return the ID of MyPortal's spam-removal case, creating it on first use."""
    url: str | None = GRAPH_CASES_URL + "?$select=id,displayName,status"
    while url:
        body = _json(await _request(token, "GET", url, "case lookup"))
        for case in body.get("value") or []:
            if (
                isinstance(case, dict)
                and case.get("displayName") == CASE_DISPLAY_NAME
                and str(case.get("status") or "").lower() not in {"closed", "closing", "closederror"}
                and case.get("id")
            ):
                return str(case["id"])
        url = body.get("@odata.nextLink")
    created = _json(await _request(
        token, "POST", GRAPH_CASES_URL, "case creation",
        {"displayName": CASE_DISPLAY_NAME, "description": CASE_DESCRIPTION},
    ))
    return str(created.get("id") or "") or _segment(None)


def _search_url(case_id: str, search_id: str | None = None) -> str:
    url = f"{GRAPH_CASES_URL}/{_segment(case_id)}/searches"
    return f"{url}/{_segment(search_id)}" if search_id else url


async def create_search(token: str, case_id: str, name: str, query: str) -> str:
    """Create a search over every mailbox in the tenant and return its ID."""
    created = _json(await _request(
        token, "POST", _search_url(case_id), "search creation",
        {
            "displayName": name,
            "description": "MyPortal spam removal search",
            "contentQuery": query,
            "dataSourceScopes": "allTenantMailboxes",
        },
    ))
    return str(created.get("id") or "") or _segment(None)


async def delete_search(token: str, case_id: str, search_id: str) -> None:
    await _request(token, "DELETE", _search_url(case_id, search_id), "search removal")


async def start_estimate(token: str, case_id: str, search_id: str) -> None:
    await _request(
        token, "POST", _search_url(case_id, search_id) + "/estimateStatistics", "estimate",
    )


async def get_estimate(token: str, case_id: str, search_id: str) -> dict[str, Any]:
    """Return the search's latest estimate-statistics operation."""
    return _json(await _request(
        token, "GET", _search_url(case_id, search_id) + "/lastEstimateStatisticsOperation",
        "estimate status",
    ))


async def start_purge(token: str, case_id: str, search_id: str) -> str:
    """Hard-delete the search results from mailboxes; return the operation URL."""
    response = await _request(
        token, "POST", _search_url(case_id, search_id) + "/purgeData", "purge",
        {"purgeType": "permanentlyDelete", "purgeAreas": "mailboxes"},
    )
    location = str(response.headers.get("Location") or "").strip()
    if not location:
        raise m365_service.M365Error(
            "Microsoft Graph accepted the purge but returned no operation to track"
        )
    m365_service._validate_graph_url(location)
    return location


async def get_operation(token: str, operation_url: str) -> dict[str, Any]:
    return _json(await _request(token, "GET", operation_url, "purge status"))


def operation_status(operation: dict[str, Any]) -> str:
    return str(operation.get("status") or "").lower()
