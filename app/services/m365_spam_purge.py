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


def _is_organization_context_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _ORG_CONTEXT_ERRORS)


def _organization_context_error(organization: str) -> m365_service.M365Error:
    """Return a useful operator-facing error without exposing directory paths."""
    return m365_service.M365Error(
        "Microsoft Purview could not load the compliance organization for "
        f"{organization}. The Entra administrator-role check is separate from this "
        "failure and does not prove Purview readiness. In M365 diagnostics, confirm "
        "Exchange.ManageAsApp is granted specifically on Microsoft Exchange Online "
        "Protection (not only Office 365 Exchange Online). Then register the enterprise "
        "application service principal in Purview, add it to the eDiscoveryManager "
        "role group, and confirm the tenant has completed Purview provisioning before "
        "retrying.",
        http_status=503,
    )


_AUTH_HINT = (
    " Purview rejected the signed-in administrator's permissions. In the "
    "customer tenant, confirm the Microsoft admin who last reconnected this "
    "company holds the Exchange Online 'Compliance Administrator' role "
    "(the M365 admin center lists it under Roles → Exchange; the Exchange "
    "Online role group is 'Compliance Management') — note that the "
    "separately-named Compliance Administrator under Roles → Compliance is "
    "a Microsoft Purview role group and does NOT authorize this cmdlet. "
    "Quickest check: sign in to compliance.microsoft.com (Microsoft Purview "
    "compliance portal) as that admin and confirm Search and purge is "
    "usable in eDiscovery. Role changes can take up to an hour to reach "
    "Purview. If that admin is no longer available, reconnect the company "
    "from the Spam Search & Purge page as another Compliance Administrator."
)


def _failure_message(exc: Exception) -> str:
    """Return the recorded error, adding remediation for authorization failures."""
    message = str(exc)
    if isinstance(exc, m365_service.M365Error) and exc.http_status in (401, 403):
        message += _AUTH_HINT
    return message[:2000]


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
                        request_id=request_id, company_id=company_id, attempt=attempt + 1, wait_seconds=wait,
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
        log_error("M365 spam search failed", request_id=request_id, company_id=company_id, error=str(exc))
        await purge_repo.update_request(request_id, {
            "search_status": "failed", "error_message": _failure_message(exc),
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
        # Reconcile first. A previous request can have succeeded remotely and then
        # timed out locally; issuing New-ComplianceSearchAction again would create
        # a second destructive operation.
        result: dict[str, Any] | None = None
        try:
            existing = await m365_service._scc_invoke_command(
                token, tenant_id, "Get-ComplianceSearchAction",
                {"Identity": request["action_name"]}, organization=organization,
            )
            existing_row = _first_row(existing)
            if existing_row and existing_row.get("Status"):
                result = existing_row
        except m365_service.M365Error as exc:
            if exc.http_status != 404:
                raise
        if result is None:
            await m365_service._scc_invoke_command(token, tenant_id, "New-ComplianceSearchAction", {
                "SearchName": request["search_name"], "Purge": True,
                "PurgeType": "HardDelete", "Confirm": False,
            }, organization=organization)
        await purge_repo.update_request(request_id, {"purge_status": "running"})
        if result is None or str(result.get("Status") or "").lower() not in TERMINAL_STATUSES:
            result = await _poll_command(
                token, tenant_id, "Get-ComplianceSearchAction", request["action_name"],
                organization=organization,
            )
        status = str(result.get("Status") or "failed").lower()
        removed = _removed_item_count(result)
        matched = int(request.get("matched_items") or 0)
        result = dict(result)
        result.update({
            "submitted_items": matched,
            "removed_items": removed,
            "remaining_items": max(matched - removed, 0) if removed else None,
            "remaining_items_unknown": removed == 0,
            "purge_type": "HardDelete",
        })
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
        log_error("M365 spam purge failed", request_id=request_id, company_id=company_id, error=str(exc))
        await purge_repo.update_request(request_id, {
            "purge_status": "failed", "error_message": _failure_message(exc),
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


async def process_queued() -> int:
    """Find and process queued search/purge requests. Returns the count processed.

    This is the entry point for the server-shell worker.  It finds all requests
    with ``search_status = 'queued'`` or ``purge_status = 'queued'`` and executes
    them sequentially via the SCC pipeline.  Safe to call repeatedly; each
    invocation processes whatever is currently queued and returns.

    Each request is validated against its company before processing.  If the
    company no longer exists, the request is marked as failed with a clear
    message rather than attempted against a missing tenant.
    """
    from app.core.database import db
    from app.repositories import companies as companies_repo

    processed = 0

    # --- Queued searches ---
    rows = await db.fetch_all(
        "SELECT id, company_id FROM m365_spam_purge_requests WHERE search_status = 'queued' ORDER BY created_at"
    )
    for row in rows:
        request_id = int(row["id"])
        company_id = int(row["company_id"])
        request = await purge_repo.get_request(request_id)
        if not request:
            continue
        # Validate company context before processing.
        company = await companies_repo.get_company_by_id(company_id)
        if not company:
            log_error("Worker skipping queued search: company not found", request_id=request_id, company_id=company_id)
            await purge_repo.update_request(request_id, {
                "search_status": "failed",
                "error_message": f"Company {company_id} no longer exists; search cannot be processed.",
                "search_completed_at": _utcnow(),
            })
            continue
        log_info("Worker processing queued search", request_id=request_id, company_id=company_id, company=str(company.get("name") or ""))
        # Always retry: the Remove-ComplianceSearch cleanup is a no-op for
        # first-time searches (suppressed), but essential for re-runs.
        await _run_search(request_id, retry=True)
        processed += 1

    # --- Queued purges ---
    rows = await db.fetch_all(
        "SELECT id, company_id FROM m365_spam_purge_requests WHERE purge_status = 'queued' ORDER BY created_at"
    )
    for row in rows:
        request_id = int(row["id"])
        company_id = int(row["company_id"])
        request = await purge_repo.get_request(request_id)
        if not request:
            continue
        # Validate company context before processing.
        company = await companies_repo.get_company_by_id(company_id)
        if not company:
            log_error("Worker skipping queued purge: company not found", request_id=request_id, company_id=company_id)
            await purge_repo.update_request(request_id, {
                "purge_status": "failed",
                "error_message": f"Company {company_id} no longer exists; purge cannot be processed.",
                "purge_completed_at": _utcnow(),
            })
            continue
        log_info("Worker processing queued purge", request_id=request_id, company_id=company_id, company=str(company.get("name") or ""))
        await _run_purge(request_id)
        processed += 1

    return processed
