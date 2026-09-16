"""Orchestrate reviewed Microsoft Purview compliance searches and purges."""

from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import suppress
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import log_error, log_info
from app.repositories import m365_spam_purge as purge_repo
from app.services import m365 as m365_service


POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 120
TERMINAL_STATUSES = frozenset({"completed", "failed", "partiallysucceeded", "stopped"})
# SCC reports both messages below when the request has reached a worker before
# its Purview organization context is available.  Keep the matching deliberately
# narrow: retries must not hide unrelated validation or authorization failures.
_ORG_CONTEXT_ERRORS = ("organization container", "parameter name: orgunit")
_NEW_SEARCH_MAX_RETRIES = 3
_NEW_SEARCH_RETRY_BASE_SECONDS = 30
_tasks: dict[tuple[int, str], asyncio.Task[None]] = {}


def _is_organization_context_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _ORG_CONTEXT_ERRORS)


def _organization_context_error(organization: str) -> m365_service.M365Error:
    """Return a useful operator-facing error without exposing directory paths."""
    return m365_service.M365Error(
        "Microsoft Purview could not load the compliance organization for "
        f"{organization}. Confirm that the app is assigned Exchange or Compliance "
        "Administrator, that the tenant has completed Purview provisioning, and "
        "then retry the search.",
        http_status=503,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _quote_kql(value: str) -> str:
    cleaned = " ".join(value.split()).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + cleaned + '"'


def build_content_match_query(
    *, sender: str | None, subject: str | None, received_from: date | None,
    received_to: date | None, advanced_query: str | None,
) -> str:
    """Build a bounded KQL query without evaluating or shell-interpolating user input."""
    clauses: list[str] = []
    if received_from or received_to:
        start = (received_from or received_to).strftime("%m/%d/%Y")  # type: ignore[union-attr]
        end = (received_to or received_from).strftime("%m/%d/%Y")  # type: ignore[union-attr]
        clauses.append(f"(Received:{start}..{end})")
    if sender and sender.strip():
        clauses.append("(From:" + _quote_kql(sender.strip()) + ")")
    if subject and subject.strip():
        clauses.append("(Subject:" + _quote_kql(subject.strip()) + ")")
    if advanced_query and advanced_query.strip():
        raw = advanced_query.strip()
        if any(ord(char) < 32 for char in raw):
            raise ValueError("Advanced query cannot contain control characters")
        clauses.append("(" + raw + ")")
    if not clauses:
        raise ValueError("At least one search criterion is required")
    return " AND ".join(clauses)


def _first_row(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("value") or payload.get("Value") or []
    if isinstance(rows, dict):
        return rows
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return payload if isinstance(payload, dict) else {}


def _int_value(row: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            match = re.search(r"\d+", str(value).replace(",", ""))
            if match:
                return int(match.group(0))
    return 0


def _removed_item_count(result: dict[str, Any]) -> int:
    direct = _int_value(result, "Items", "ItemCount")
    if direct:
        return direct
    summary = str(result.get("Results") or result.get("Result") or "")
    match = re.search(r"(?:item\s*count|items?)\s*[:=]\s*([\d,]+)", summary, re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else 0


async def create_request(company_id: int, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    query = build_content_match_query(
        sender=data.get("sender"), subject=data.get("subject"),
        received_from=data.get("received_from"), received_to=data.get("received_to"),
        advanced_query=data.get("content_match_query"),
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    search_name = f"MyPortal spam removal {stamp}-{suffix}"
    return await purge_repo.create_request({
        **data, "company_id": company_id, "created_by": user_id,
        "search_name": search_name, "action_name": search_name + "_Purge",
        "content_match_query": query,
    })


def _start_task(request_id: int, action: str, *, retry: bool = False) -> None:
    key = (request_id, action)
    current = _tasks.get(key)
    if current and not current.done():
        raise ValueError(f"{action.capitalize()} is already running")
    task = asyncio.create_task(
        _run_search(request_id, retry=retry) if action == "search" else _run_purge(request_id)
    )
    _tasks[key] = task
    task.add_done_callback(lambda _task: _tasks.pop(key, None))


async def start_search(request_id: int, company_id: int) -> dict[str, Any]:
    request = await _owned_request(request_id, company_id)
    previous_status = str(request["search_status"]).lower()
    if previous_status not in {"draft", "failed"}:
        raise ValueError("Only draft or failed searches can be started")
    await purge_repo.update_request(request_id, {
        "search_status": "queued", "error_message": None, "search_details": None,
        "matched_items": 0, "matched_size": 0, "search_started_at": _utcnow(),
        "search_completed_at": None,
    })
    _start_task(request_id, "search", retry=previous_status == "failed")
    return (await purge_repo.get_request(request_id)) or request


async def start_purge(request_id: int, company_id: int) -> dict[str, Any]:
    request = await _owned_request(request_id, company_id)
    if str(request["search_status"]).lower() != "completed":
        raise ValueError("The compliance search must complete before purge")
    if int(request.get("matched_items") or 0) < 1:
        raise ValueError("The completed search contains no messages to purge")
    if str(request["purge_status"]).lower() not in {"not_started", "failed"}:
        raise ValueError("Purge has already been started")
    await purge_repo.update_request(request_id, {
        "purge_status": "queued", "error_message": None, "purge_started_at": _utcnow(),
    })
    _start_task(request_id, "purge")
    return (await purge_repo.get_request(request_id)) or request


async def _owned_request(request_id: int, company_id: int) -> dict[str, Any]:
    request = await purge_repo.get_request(request_id)
    if not request or int(request["company_id"]) != company_id:
        raise LookupError("Spam purge request not found")
    return request


async def _poll_command(
    token: str, tenant: str, cmdlet: str, identity: str, *, organization: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for _ in range(MAX_POLL_ATTEMPTS):
        payload = await m365_service._scc_invoke_command(
            token, tenant, cmdlet, {"Identity": identity},
            organization=organization,
        )
        row = _first_row(payload)
        if str(row.get("Status") or "").lower() in TERMINAL_STATUSES:
            return row
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"{cmdlet} did not finish before the polling timeout")


async def _scc_organization(company_id: int) -> str:
    """Resolve the initial domain used as the Purview routing organization."""
    graph_token = await m365_service.acquire_access_token(company_id)
    payload = await m365_service._graph_get(
        graph_token,
        "https://graph.microsoft.com/v1.0/domains?$select=id,isInitial",
    )
    domains = payload.get("value") or []
    for domain in domains if isinstance(domains, list) else []:
        if not isinstance(domain, dict) or not domain.get("isInitial"):
            continue
        name = str(domain.get("id") or "").strip().lower()
        if name.endswith(".onmicrosoft.com"):
            return name
    raise ValueError(
        "Microsoft 365 initial domain could not be resolved; grant the app "
        "Domain.Read.All and reconnect the tenant"
    )


async def _run_search(request_id: int, *, retry: bool = False) -> None:
    request = await purge_repo.get_request(request_id)
    if not request:
        return
    try:
        await purge_repo.update_request(request_id, {"search_status": "starting"})
        company_id = int(request["company_id"])
        token, tenant_id = await m365_service._acquire_scc_access_token(company_id)
        organization = await _scc_organization(company_id)
        if retry:
            # A prior attempt can fail after Purview persisted the object. Remove
            # only this request's unique, non-destructive search before recreating it.
            with suppress(Exception):
                await m365_service._scc_invoke_command(token, tenant_id, "Remove-ComplianceSearch", {
                    "Identity": request["search_name"], "Confirm": False,
                }, organization=organization)
        for attempt in range(_NEW_SEARCH_MAX_RETRIES + 1):
            try:
                await m365_service._scc_invoke_command(token, tenant_id, "New-ComplianceSearch", {
                    "Name": request["search_name"], "ExchangeLocation": "All",
                    "ContentMatchQuery": request["content_match_query"],
                }, organization=organization)
                break
            except m365_service.M365Error as exc:
                if _is_organization_context_error(exc) and attempt < _NEW_SEARCH_MAX_RETRIES:
                    wait = _NEW_SEARCH_RETRY_BASE_SECONDS * (2 ** attempt)
                    log_info(
                        "New-ComplianceSearch transient org-container error; retrying",
                        request_id=request_id, attempt=attempt + 1, wait_seconds=wait,
                    )
                    await asyncio.sleep(wait)
                elif _is_organization_context_error(exc):
                    raise _organization_context_error(organization) from exc
                else:
                    raise
        await m365_service._scc_invoke_command(token, tenant_id, "Start-ComplianceSearch", {
            "Identity": request["search_name"],
        }, organization=organization)
        await purge_repo.update_request(request_id, {"search_status": "running"})
        result = await _poll_command(
            token, tenant_id, "Get-ComplianceSearch", request["search_name"],
            organization=organization,
        )
        status = str(result.get("Status") or "failed").lower()
        updates = {
            "search_status": status, "matched_items": _int_value(result, "Items", "ItemCount"),
            "matched_size": _int_value(result, "Size", "SizeInBytes"), "search_details": result,
            "search_completed_at": _utcnow(),
        }
        if status != "completed":
            updates["error_message"] = str(result.get("Errors") or "Compliance search did not complete")[:2000]
        await purge_repo.update_request(request_id, updates)
    except Exception as exc:  # noqa: BLE001 - background boundary records safe error
        log_error("M365 spam search failed", request_id=request_id, error=str(exc))
        await purge_repo.update_request(request_id, {
            "search_status": "failed", "error_message": str(exc)[:2000],
            "search_completed_at": _utcnow(),
        })


async def _run_purge(request_id: int) -> None:
    request = await purge_repo.get_request(request_id)
    if not request:
        return
    try:
        await purge_repo.update_request(request_id, {"purge_status": "starting"})
        company_id = int(request["company_id"])
        token, tenant_id = await m365_service._acquire_scc_access_token(company_id)
        organization = await _scc_organization(company_id)
        await m365_service._scc_invoke_command(token, tenant_id, "New-ComplianceSearchAction", {
            "SearchName": request["search_name"], "Purge": True,
            "PurgeType": "HardDelete", "Confirm": False,
        }, organization=organization)
        await purge_repo.update_request(request_id, {"purge_status": "running"})
        result = await _poll_command(
            token, tenant_id, "Get-ComplianceSearchAction", request["action_name"],
            organization=organization,
        )
        status = str(result.get("Status") or "failed").lower()
        removed = _removed_item_count(result)
        updates = {
            "purge_status": status, "removed_items": removed, "purge_details": result,
            "purge_completed_at": _utcnow(),
        }
        if status != "completed":
            updates["error_message"] = str(result.get("Errors") or "Purge did not complete")[:2000]
        await purge_repo.update_request(request_id, updates)
        if status == "completed":
            await _run_managed_folder_assistant(token, tenant_id, request_id)
    except Exception as exc:  # noqa: BLE001 - background boundary records safe error
        log_error("M365 spam purge failed", request_id=request_id, error=str(exc))
        await purge_repo.update_request(request_id, {
            "purge_status": "failed", "error_message": str(exc)[:2000],
            "purge_completed_at": _utcnow(),
        })


async def _run_managed_folder_assistant(_scc_token: str, _tenant: str, request_id: int) -> None:
    request = await purge_repo.get_request(request_id)
    if not request:
        return
    try:
        exo_token, exo_tenant = await m365_service._acquire_exo_access_token(int(request["company_id"]))
        payload = await m365_service._exo_invoke_command(exo_token, exo_tenant, "Get-Mailbox", {"ResultSize": "Unlimited"})
        rows = payload.get("value") or []
        if isinstance(rows, dict):
            rows = [rows]
        count = 0
        for mailbox in rows if isinstance(rows, list) else []:
            identity = mailbox.get("UserPrincipalName") if isinstance(mailbox, dict) else None
            if identity:
                await m365_service._exo_invoke_command(exo_token, exo_tenant, "Start-ManagedFolderAssistant", {"Identity": identity})
                count += 1
        details = request.get("purge_details") or {}
        if not isinstance(details, dict):
            details = {"results": details}
        details["managed_folder_assistant_mailboxes"] = count
        await purge_repo.update_request(request_id, {"purge_details": details})
        log_info("Managed Folder Assistant started after M365 purge", request_id=request_id, mailboxes=count)
    except Exception as exc:  # purge succeeded; record cleanup warning without changing status
        log_error("Managed Folder Assistant follow-up failed", request_id=request_id, error=str(exc))
        details = request.get("purge_details") or {}
        if not isinstance(details, dict):
            details = {"results": details}
        details["managed_folder_assistant_warning"] = str(exc)[:500]
        await purge_repo.update_request(request_id, {"purge_details": details})
