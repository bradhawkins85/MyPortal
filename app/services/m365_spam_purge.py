"""Orchestrate reviewed Microsoft Purview searches and purges via Graph eDiscovery."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import date, datetime, timezone
from typing import Any

from email_validator import EmailNotValidError, validate_email

from app.core.logging import log_error, log_info
from app.repositories import m365_spam_purge as purge_repo
from app.services import m365 as m365_service
from app.services import m365_ediscovery as ediscovery


POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 120
POST_PURGE_ESTIMATE_ATTEMPTS = 3
_API = "graph_ediscovery"


_AUTH_HINT = (
    " Microsoft Graph rejected the eDiscovery request under the signed-in "
    "administrator's permissions. Spam search and purge run as the Microsoft "
    "admin who last reconnected this company. In the customer tenant's "
    "Microsoft Purview portal, confirm that admin is a member of the "
    "eDiscovery Manager role group (search) and holds the Search And Purge "
    "role (purge; included in Organization Management), and that the tenant "
    "has an eDiscovery licence. Role changes can take up to an hour to apply. "
    "If that admin is no longer available, reconnect the company from "
    "Microsoft 365 settings as another such admin."
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


async def create_request(company_id: int, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    # ``query_override`` is a query already built by a trusted caller (such as
    # the reported-email alerts page, which needs hour-level received times).
    query = data.get("query_override") or build_content_match_query(
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
        "purge_completed_at": None,
        "purge_details": {**_dict(request.get("purge_details")), "verification_only": False},
    })
    return (await purge_repo.get_request(request_id)) or request


async def start_verification(request_id: int, company_id: int) -> dict[str, Any]:
    """Queue inspection of an existing purge; never submit another deletion."""
    request = await _owned_request(request_id, company_id)
    if str(request["purge_status"]).lower() not in {"completed", "needs_review", "partiallysucceeded", "failed"}:
        raise ValueError("Only a finished purge can be verified")
    details = _dict(request.get("purge_details"))
    if not details.get("operation_url"):
        raise ValueError("This request has no Microsoft purge operation to verify")
    await purge_repo.update_request(request_id, {
        "purge_status": "queued", "error_message": None,
        "purge_completed_at": None,
        "purge_details": {**details, "verification_only": True},
    })
    return (await purge_repo.get_request(request_id)) or request


async def _owned_request(request_id: int, company_id: int) -> dict[str, Any]:
    request = await purge_repo.get_request(request_id)
    if not request or int(request["company_id"]) != company_id:
        raise LookupError("Spam purge request not found")
    return request


def _count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


async def _estimate(token: str, case_id: str, search_id: str) -> dict[str, Any]:
    """Run estimate statistics for a search and wait for that run to finish.

    ``lastEstimateStatisticsOperation`` keeps returning the previous run until
    the new one is registered, so the new run is recognised by a changed ID.
    """
    previous_id = None
    with suppress(m365_service.M365Error):
        previous_id = (await ediscovery.get_estimate(token, case_id, search_id)).get("id")
    await ediscovery.start_estimate(token, case_id, search_id)
    for _ in range(MAX_POLL_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            operation = await ediscovery.get_estimate(token, case_id, search_id)
        except m365_service.M365Error as exc:
            if exc.http_status == 404:
                continue
            raise
        if (
            operation.get("id")
            and operation.get("id") != previous_id
            and ediscovery.operation_status(operation) in ediscovery.TERMINAL_OPERATION_STATUSES
        ):
            return operation
    raise TimeoutError("Estimate statistics did not finish before the polling timeout")


def _local_status(operation: dict[str, Any]) -> str:
    status = ediscovery.operation_status(operation)
    if status == "submissionfailed":
        return "failed"
    return "completed" if status == "succeeded" else (status or "failed")


def _operation_error(operation: dict[str, Any], default: str) -> str:
    detail = operation.get("resultInfo") or operation.get("error") or default
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("code") or default
    return str(detail)[:2000]


async def _record_locations(company_id: int, estimate: dict[str, Any], details: dict[str, Any]) -> None:
    """Store the mailboxes with matches from the search statistics report.

    The report is best effort: a missing grant or a report that is not ready
    is recorded in ``locations_error`` and never fails the search itself.
    """
    url = ediscovery.report_download_url(estimate)
    if not url:
        details["locations_error"] = "Microsoft did not return a statistics report for this search."
        return
    try:
        token = await m365_service._acquire_ediscovery_download_token(company_id)
        details["locations"] = ediscovery.locations_with_matches(
            await ediscovery.download_report(token, url)
        )
    except Exception as exc:  # noqa: BLE001 - report is optional detail
        log_error("Spam search report download failed", company_id=company_id, error=str(exc))
        details["locations_error"] = _failure_message(exc)[:500]


async def _record_purge_report(company_id: int, operation: dict[str, Any], details: dict[str, Any]) -> None:
    url = ediscovery.report_download_url(operation)
    if not url:
        details["report_error"] = "Microsoft did not return a purge job report."
        return
    try:
        token = await m365_service._acquire_ediscovery_download_token(company_id)
        details["reports"] = ediscovery.purge_report_preview(await ediscovery.download_report(token, url))
        summary = {key: value for report in details["reports"] for key, value in report["metrics"].items()}
        details["summary"] = summary
        details["report_no_op"] = summary.get("location_count") == 0 or summary.get("attempted_items") == 0
        details["report_has_errors"] = bool(summary.get("failed_items")) or any(report["issues"] for report in details["reports"])
    except Exception as exc:  # report evidence is optional; retain the operation outcome
        log_error("Spam purge report download failed", company_id=company_id, error=str(exc))
        details["report_error"] = _failure_message(exc)[:500]


def _verified_estimate_count(estimate: dict[str, Any]) -> int:
    """Missing, failed or partial estimates must never be treated as zero."""
    if ediscovery.operation_status(estimate) != "succeeded":
        raise ValueError(_operation_error(estimate, "Search estimate did not succeed"))
    value = estimate.get("indexedItemCount")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Search estimate did not return a valid indexed item count")
    return value


def _purge_mailboxes(search: dict[str, Any]) -> list[str]:
    locations = search.get("locations")
    if not isinstance(locations, list) or not locations:
        raise ValueError("Cannot purge without the matched mailbox locations. Run a new search and ensure its mailbox report is available.")
    mailboxes: dict[str, str] = {}
    for item in locations:
        if not isinstance(item, dict) or _count(item.get("count")) < 1:
            continue
        try:
            mailbox = validate_email(str(item.get("location") or ""), check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise ValueError("The search report contains a match without a usable mailbox address; run a new search") from exc
        mailboxes.setdefault(mailbox.casefold(), mailbox)
    if not mailboxes:
        raise ValueError("The search report contains no matched mailbox locations; no purge was submitted")
    return list(mailboxes.values())


async def _prepare_purge_scope(token: str, request: dict[str, Any], details: dict[str, Any]) -> str:
    search = _dict(request.get("search_details"))
    case_id = str(search["case_id"])
    mailboxes = _purge_mailboxes(search)
    # Create a dedicated purge search scoped to the reviewed mailboxes instead of
    # narrowing the tenant-wide discovery search in place. Microsoft applies a
    # dataSourceScopes change asynchronously and rejects additionalSources with a
    # 409 until it does, so an in-place narrow can fail the purge indefinitely.
    purge_search_id = await ediscovery.create_purge_search(
        token, case_id, str(request["search_name"]), str(request["content_match_query"]), mailboxes,
    )
    details["mailboxes"] = mailboxes
    details["purge_search_id"] = purge_search_id
    # Estimate the explicit scope afresh; never authorize deletion from the
    # previous tenant-wide run.
    estimate = await _estimate(token, case_id, purge_search_id)
    details["scope_estimate"] = estimate
    await purge_repo.update_request(int(request["id"]), {"purge_details": details})
    count = _verified_estimate_count(estimate)
    mailbox_count = estimate.get("mailboxCount")
    if isinstance(mailbox_count, bool) or not isinstance(mailbox_count, int) or mailbox_count < 1:
        raise ValueError("Microsoft resolved zero mailbox locations for the explicit purge scope; no purge was submitted")
    if count < 1 or count != int(request.get("matched_items") or 0):
        raise ValueError("The explicit mailbox search no longer matches the reviewed item count; run a new search and review it before purging")
    return purge_search_id


async def _run_search(request_id: int, *, retry: bool = False) -> None:
    request = await purge_repo.get_request(request_id)
    if not request:
        return
    company_id = int(request["company_id"])
    try:
        await purge_repo.update_request(request_id, {"search_status": "starting"})
        token = await m365_service._acquire_ediscovery_access_token(company_id)
        previous = _dict(request.get("search_details"))
        case_id = await ediscovery.ensure_case(token)
        if retry and previous.get("case_id") and previous.get("search_id"):
            # A prior attempt may have created the search before failing.
            # Remove only this request's own, non-destructive search.
            with suppress(Exception):
                await ediscovery.delete_search(token, str(previous["case_id"]), str(previous["search_id"]))
        search_id = await ediscovery.create_search(
            token, case_id, request["search_name"], request["content_match_query"],
        )
        details: dict[str, Any] = {"api": _API, "case_id": case_id, "search_id": search_id}
        await purge_repo.update_request(request_id, {"search_status": "running", "search_details": details})
        estimate = await _estimate(token, case_id, search_id)
        status = _local_status(estimate)
        details["estimate"] = estimate
        if status == "completed":
            await _record_locations(company_id, estimate, details)
        updates: dict[str, Any] = {
            "search_status": status,
            "matched_items": _count(estimate.get("indexedItemCount")),
            "matched_size": _count(estimate.get("indexedItemsSize")),
            "search_details": details,
            "search_completed_at": _utcnow(),
        }
        if status != "completed":
            updates["error_message"] = _operation_error(estimate, "Compliance search did not complete")
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
    company_id = int(request["company_id"])
    try:
        await purge_repo.update_request(request_id, {"purge_status": "starting"})
        search = _dict(request.get("search_details"))
        case_id, search_id = search.get("case_id"), search.get("search_id")
        if not case_id or not search_id:
            raise ValueError("This search predates Graph eDiscovery; run the search again before purging")
        case_id, search_id = str(case_id), str(search_id)
        token = await m365_service._acquire_ediscovery_access_token(company_id)
        details = _dict(request.get("purge_details"))
        verification_only = bool(details.get("verification_only"))
        if verification_only and not details.get("operation_url"):
            raise ValueError("This request has no Microsoft purge operation to verify")
        operation: dict[str, Any] | None = None
        # Reconcile first. A previous attempt can have been accepted remotely and
        # then failed locally; submitting purgeData again would start a second
        # destructive operation.  Only a purge Graph reports as failed is resubmitted.
        if details.get("operation_url"):
            operation = await ediscovery.get_operation(token, str(details["operation_url"]))
            if not verification_only and ediscovery.operation_status(operation) in {"failed", "submissionfailed"}:
                operation = None
                details = {}
        if operation is None:
            purge_search_id = await _prepare_purge_scope(token, request, details)
            operation_url = await ediscovery.start_purge(token, case_id, purge_search_id)
            details.update({"api": _API, "operation_url": operation_url, "purge_type": "HardDelete"})
            await purge_repo.update_request(request_id, {"purge_status": "running", "purge_details": details})
            operation = {}
        else:
            await purge_repo.update_request(request_id, {"purge_status": "running"})
        attempts = 0
        while ediscovery.operation_status(operation) not in ediscovery.TERMINAL_OPERATION_STATUSES:
            if attempts >= MAX_POLL_ATTEMPTS:
                raise TimeoutError("Purge did not finish before the polling timeout")
            attempts += 1
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            operation = await ediscovery.get_operation(token, str(details["operation_url"]))
        status = _local_status(operation)
        for key in ("reports", "report_error", "report_has_errors", "report_no_op", "summary", "verification_estimate", "verification_error", "verification_attempts"):
            details.pop(key, None)
        matched = int(request.get("matched_items") or 0)
        remaining: int | None = None
        details["operation"] = operation
        await _record_purge_report(company_id, operation, details)
        if status in {"completed", "partiallysucceeded"}:
            # Save Microsoft's outcome before cleanup. Reconciliation must retain
            # the operation URL even if cleanup/verification subsequently fails.
            await purge_repo.update_request(request_id, {"purge_details": details})
            if not details.get("report_no_op"):
                await _run_managed_folder_assistant(request_id)
            refreshed = await purge_repo.get_request(request_id)
            details.update(_dict((refreshed or {}).get("purge_details")))
            # Cleanup and search indexing are asynchronous. Retry estimates,
            # never the destructive purge, while allowing changes to propagate.
            # Verify against the explicit purge search when available, falling
            # back to the discovery search for operations that predate it.
            verify_search_id = str(details.get("purge_search_id") or search_id)
            if not details.get("purge_search_id"):
                log_info(
                    "Post-purge verification falling back to the discovery search",
                    request_id=request_id, company_id=company_id, search_id=verify_search_id,
                )
            for attempt in range(POST_PURGE_ESTIMATE_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    token = await m365_service._acquire_ediscovery_access_token(company_id)
                    estimate = await _estimate(token, case_id, verify_search_id)
                    remaining = _verified_estimate_count(estimate)
                    details["verification_estimate"] = estimate
                    details.pop("verification_error", None)
                except Exception as exc:  # keep unknown counts separate from zero
                    log_error("Post-purge estimate failed", request_id=request_id, error=str(exc))
                    details["verification_error"] = _failure_message(exc)
                    remaining = None
                    details["verification_attempts"] = attempt + 1
                    break
                details["verification_attempts"] = attempt + 1
                if remaining == 0 or details.get("report_no_op"):
                    break
        reported_removed = _dict(details.get("summary")).get("purged_items")
        removed = reported_removed if reported_removed is not None else (max(matched - remaining, 0) if remaining is not None else 0)
        details.update({
            "operation": operation,
            "submitted_items": matched,
            "removed_items": removed,
            "removed_items_unknown": reported_removed is None and remaining is None,
            "removed_items_source": "purge_report" if reported_removed is not None else "search_estimate",
            "remaining_items": remaining,
            "remaining_items_unknown": remaining is None,
            "purge_type": "HardDelete",
        })
        if status == "completed" and (remaining != 0 or details.get("report_has_errors") or details.get("report_no_op")):
            status = "needs_review"
        details["verification_status"] = (
            "unavailable" if remaining is None else "no_remaining_matches" if remaining == 0 else "matches_remain"
        )
        updates = {
            "purge_status": status, "removed_items": removed, "purge_details": details,
            "purge_completed_at": _utcnow(),
        }
        if status == "needs_review":
            updates["error_message"] = (
                "Microsoft finished the purge job, but message removal could not be verified. "
                + ("The purge report confirms an empty job: zero locations or zero items attempted. No deletion was performed. " if details.get("report_no_op") else "")
                + ("The purge report contains errors or warnings. " if details.get("report_has_errors") else "")
                + (f"The search still returns {remaining} matching item(s). " if remaining is not None else "The remaining count is unknown. ")
                + "Check the purge job report and affected mailboxes in Outlook on the web. "
                "If messages are still visible there, the purge has not achieved the requested outcome. "
                "Recoverable or held copies can also remain searchable. Verify the existing job or inspect "
                "its report in Microsoft Purview before submitting another purge."
            )
        elif status != "completed":
            updates["error_message"] = _operation_error(operation, "Purge did not complete")
        else:
            updates["error_message"] = None
        await purge_repo.update_request(request_id, updates)
    except Exception as exc:  # noqa: BLE001 - background boundary records safe error
        log_error("M365 spam purge failed", request_id=request_id, company_id=company_id, error=str(exc))
        await purge_repo.update_request(request_id, {
            "purge_status": "failed", "error_message": _failure_message(exc),
            "purge_completed_at": _utcnow(),
        })


async def _run_managed_folder_assistant(request_id: int) -> None:
    """Start the Managed Folder Assistant on the mailboxes the purge touched.

    The mailboxes come from the search's statistics report (locations with a
    count above 0).  When that list is unavailable every mailbox is processed,
    as before, so recoverable items are still cleaned up promptly.
    """
    request = await purge_repo.get_request(request_id)
    if not request:
        return
    details = request.get("purge_details") or {}
    if not isinstance(details, dict):
        details = {"results": details}
    search = request.get("search_details") if isinstance(request.get("search_details"), dict) else {}
    locations = search.get("locations") if isinstance(search.get("locations"), list) else None
    try:
        exo_token, exo_tenant = await m365_service._acquire_exo_access_token(int(request["company_id"]))
        if locations is not None:
            identities = [
                str(item.get("location") or "").strip()
                for item in locations
                if isinstance(item, dict) and str(item.get("location") or "").strip()
            ]
            scope = "matched_mailboxes"
        else:
            payload = await m365_service._exo_invoke_command(exo_token, exo_tenant, "Get-Mailbox", {"ResultSize": "Unlimited"})
            rows = payload.get("value") or []
            if isinstance(rows, dict):
                rows = [rows]
            identities = [
                str(mailbox.get("UserPrincipalName"))
                for mailbox in (rows if isinstance(rows, list) else [])
                if isinstance(mailbox, dict) and mailbox.get("UserPrincipalName")
            ]
            scope = "all_mailboxes"
        started: list[str] = []
        failed: dict[str, str] = {}
        for identity in identities:
            try:
                await m365_service._exo_invoke_command(exo_token, exo_tenant, "Start-ManagedFolderAssistant", {"Identity": identity})
                started.append(identity)
            except m365_service.M365Error as exc:
                failed[identity] = str(exc)[:300]
        details["managed_folder_assistant_scope"] = scope
        details["managed_folder_assistant_mailboxes"] = started if scope == "matched_mailboxes" else len(started)
        if failed:
            details["managed_folder_assistant_failures"] = failed
        await purge_repo.update_request(request_id, {"purge_details": details})
        log_info(
            "Managed Folder Assistant started after M365 purge",
            request_id=request_id, mailboxes=len(started), failures=len(failed), scope=scope,
        )
    except Exception as exc:  # purge succeeded; record cleanup warning without changing status
        log_error("Managed Folder Assistant follow-up failed", request_id=request_id, error=str(exc))
        details["managed_folder_assistant_warning"] = str(exc)[:500]
        await purge_repo.update_request(request_id, {"purge_details": details})


async def process_queued() -> int:
    """Find and process queued search/purge requests. Returns the count processed.

    This is the entry point for the server-shell worker.  It finds all requests
    with ``search_status = 'queued'`` or ``purge_status = 'queued'`` and executes
    them sequentially via the Graph eDiscovery pipeline.  Safe to call repeatedly; each
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
        # Always retry: removing the previous Graph search is a no-op for
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
