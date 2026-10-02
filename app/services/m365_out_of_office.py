from __future__ import annotations

import asyncio
import html
import time
from typing import Any
from urllib.parse import quote

from app.repositories import companies as companies_repo
from app.repositories import m365 as m365_repo
from app.schemas.m365_out_of_office import OutOfOfficeCreate, OutOfOfficeDisable
from app.core.logging import log_warning
from app.services import m365 as m365_service


# Microsoft Graph JSON batching accepts at most 20 requests per $batch call.
_GRAPH_BATCH_URL = "https://graph.microsoft.com/v1.0/$batch"
_GRAPH_BATCH_SIZE = 20
# Batches sent in parallel.  Exchange throttles per mailbox, so spreading
# requests across mailboxes in parallel batches stays well within limits.
_GRAPH_BATCH_CONCURRENCY = 6
# Individual requests throttled or briefly unavailable are retried once.
_RETRYABLE_STATUSES = {429, 503, 504}
_MAX_RETRY_DELAY_SECONDS = 5.0
# Reading state must finish well inside the reverse proxy's 60 second upstream
# timeout, otherwise the page is replaced by the "Upgrade In Progress" screen.
_READ_TIME_BUDGET_SECONDS = 40.0
_READ_TIMEOUT_ERROR = "Timed out loading current state; refresh to retry"


_HTML_TAGS = {
    "html",
    "body",
    "p",
    "div",
    "br",
    "span",
    "table",
    "a",
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ul",
    "ol",
    "li",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}


def _contains_known_html_markup(text: str) -> bool:
    """Return True when text contains one of the supported HTML tags."""
    length = len(text)
    i = 0
    while i < length:
        if text[i] != "<":
            i += 1
            continue
        j = i + 1
        while j < length and text[j].isspace():
            j += 1
        if j < length and text[j] == "/":
            j += 1
            while j < length and text[j].isspace():
                j += 1
        start = j
        while j < length and text[j].isalnum():
            j += 1
        if start == j:
            i += 1
            continue
        tag = text[start:j].casefold()
        next_char = text[j] if j < length else ""
        is_word_char = bool(next_char) and (next_char.isalnum() or next_char == "_")
        if tag in _HTML_TAGS and not is_word_char:
            return True
        i += 1
    return False


def _as_reply_html(message: str | None) -> str:
    """Return a reply body Exchange renders as entered.

    Exchange stores automatic replies as HTML, so plain text submitted with
    line breaks would otherwise collapse onto a single line in the sent reply.
    Messages that already contain HTML markup are passed through unchanged.
    """
    text = str(message or "")
    if _contains_known_html_markup(text):
        return text
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(normalised, quote=False).replace("\n", "<br>\n")


def _mailbox_uses_company_domain(mailbox: dict[str, object], domains: set[str]) -> bool:
    """Return whether a mailbox UPN uses an explicitly configured company domain."""
    upn = str(mailbox.get("user_principal_name") or "").strip()
    _, separator, domain = upn.rpartition("@")
    return bool(separator and domain.casefold() in domains)


def _mailbox_settings_path(mailbox: str) -> str:
    """Return the Graph batch-relative mailboxSettings path for a mailbox."""
    return "/users/" + quote(mailbox, safe="") + "/mailboxSettings"


def _batch_item_error(status: int, body: Any) -> str:
    """Describe a failed batch item the way the single-request helpers do."""
    message = f"Microsoft Graph request failed ({status})"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"]:
        message = f"{message}: {error['message']}"
    return message


def _retry_delay(headers: Any) -> float:
    try:
        value = float((headers or {}).get("Retry-After") or 1)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.0, min(value, _MAX_RETRY_DELAY_SECONDS))


async def _send_batch(
    token: str, requests: list[dict[str, Any]]
) -> list[tuple[int, Any, Any]]:
    """Send one $batch call and return (status, body, headers) per request in order."""
    payload = {
        "requests": [dict(request, id=str(index)) for index, request in enumerate(requests)]
    }
    response = await m365_service._graph_post(token, _GRAPH_BATCH_URL, payload)
    by_id = {
        str(item.get("id")): item
        for item in (response or {}).get("responses") or []
        if isinstance(item, dict)
    }
    results: list[tuple[int, Any, Any]] = []
    for index in range(len(requests)):
        item = by_id.get(str(index))
        if item is None:
            results.append((0, None, None))
            continue
        try:
            status = int(item.get("status") or 0)
        except (TypeError, ValueError):
            status = 0
        results.append((status, item.get("body"), item.get("headers")))
    return results


async def _run_batch_chunk(
    token: str, requests: list[dict[str, Any]], deadline: float | None
) -> list[tuple[int, Any, str | None]]:
    """Run up to 20 requests in one $batch, retrying throttled items once."""
    outcomes: list[tuple[int, Any, str | None]] = [(0, None, None)] * len(requests)
    pending = list(range(len(requests)))
    for attempt in range(2):
        try:
            sent = await _send_batch(token, [requests[index] for index in pending])
        except m365_service.M365Error as exc:
            for index in pending:
                outcomes[index] = (0, None, str(exc))
            return outcomes
        retry: list[int] = []
        delay = 0.0
        for index, (status, body, headers) in zip(pending, sent):
            if 200 <= status < 300:
                outcomes[index] = (status, body, None)
                continue
            if status == 0:
                outcomes[index] = (status, body, "Microsoft Graph returned no response")
                continue
            outcomes[index] = (status, body, _batch_item_error(status, body))
            if status in _RETRYABLE_STATUSES:
                retry.append(index)
                delay = max(delay, _retry_delay(headers))
        if not retry or attempt:
            break
        if deadline is not None and time.monotonic() + delay >= deadline:
            break
        await asyncio.sleep(delay)
        pending = retry
    return outcomes


async def _run_batched(
    token: str, requests: list[dict[str, Any]], *, deadline: float | None = None
) -> list[tuple[int, Any, str | None]]:
    """Run Graph requests through concurrent $batch calls, preserving order.

    Each result is ``(status, body, error)`` where ``error`` is ``None`` on
    success.  When ``deadline`` (a ``time.monotonic()`` value) passes, batches
    still waiting or in flight are reported as timed out instead of blocking
    the caller.
    """
    chunks = [
        requests[start:start + _GRAPH_BATCH_SIZE]
        for start in range(0, len(requests), _GRAPH_BATCH_SIZE)
    ]
    semaphore = asyncio.Semaphore(_GRAPH_BATCH_CONCURRENCY)

    async def run(chunk: list[dict[str, Any]]) -> list[tuple[int, Any, str | None]]:
        async with semaphore:
            if deadline is None:
                return await _run_batch_chunk(token, chunk, None)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    return await asyncio.wait_for(
                        _run_batch_chunk(token, chunk, deadline), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    pass
            return [(0, None, _READ_TIMEOUT_ERROR)] * len(chunk)

    chunk_results = await asyncio.gather(*(run(chunk) for chunk in chunks))
    results = [item for chunk in chunk_results for item in chunk]
    timed_out = sum(1 for _, _, error in results if error == _READ_TIMEOUT_ERROR)
    if timed_out:
        log_warning(
            "Microsoft Graph batch requests timed out",
            timed_out=timed_out,
            total=len(results),
        )
    return results


async def _patch_mailbox_settings(
    token: str, mailboxes: list[str], setting: dict[str, Any]
) -> list[dict[str, object]]:
    """PATCH the same mailboxSettings body onto each mailbox via batched calls."""
    requests = [
        {
            "method": "PATCH",
            "url": _mailbox_settings_path(mailbox),
            "headers": {"Content-Type": "application/json"},
            "body": setting,
        }
        for mailbox in mailboxes
    ]
    responses = await _run_batched(token, requests)
    return [
        {"mailbox": mailbox, "success": error is None, "error": error}
        for mailbox, (_, _, error) in zip(mailboxes, responses)
    ]


async def get_selectable_mailboxes(company_id: int) -> list[dict[str, object]]:
    """Return cached user mailboxes restricted to the company's email domains."""
    domains = {
        domain.casefold()
        for domain in await companies_repo.get_email_domains_for_company(company_id)
    }
    mailboxes = await m365_repo.get_mailboxes(company_id, "UserMailbox")
    return [mailbox for mailbox in mailboxes if _mailbox_uses_company_domain(mailbox, domains)]


async def set_automatic_replies(company_id: int, payload: OutOfOfficeCreate) -> list[dict[str, object]]:
    """Set scheduled automatic replies, restricted to cached user mailboxes."""
    allowed = {
        str(row["user_principal_name"]).casefold(): str(row["user_principal_name"])
        for row in await get_selectable_mailboxes(company_id)
    }
    requested = [str(mailbox) for mailbox in payload.mailboxes]
    unknown = [mailbox for mailbox in requested if mailbox.casefold() not in allowed]
    if unknown:
        raise ValueError("Unknown user mailbox selection: " + ", ".join(unknown))

    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    setting = {
        "automaticRepliesSetting": {
            "status": "scheduled",
            "externalAudience": payload.external_audience,
            "scheduledStartDateTime": {
                "dateTime": payload.start_time.replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "scheduledEndDateTime": {
                "dateTime": payload.end_time.replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "internalReplyMessage": _as_reply_html(payload.internal_message),
            "externalReplyMessage": _as_reply_html(payload.external_message),
        }
    }
    mailboxes = [allowed[submitted.casefold()] for submitted in requested]
    return await _patch_mailbox_settings(token, mailboxes, setting)


async def get_automatic_replies(company_id: int) -> list[dict[str, object]]:
    """Read current Graph state for each selectable mailbox, retaining individual errors."""
    mailboxes = await get_selectable_mailboxes(company_id)
    if not mailboxes:
        return []
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    names = [str(row["user_principal_name"]) for row in mailboxes]
    requests = [
        {"method": "GET", "url": _mailbox_settings_path(mailbox) + "?$select=automaticRepliesSetting"}
        for mailbox in names
    ]
    responses = await _run_batched(
        token, requests, deadline=time.monotonic() + _READ_TIME_BUDGET_SECONDS
    )
    results: list[dict[str, object]] = []
    for mailbox, (status, body, error) in zip(names, responses):
        if error is None:
            setting = (body or {}).get("automaticRepliesSetting") or {}
            results.append({"mailbox": mailbox, "success": True, "setting": setting, "error": None})
        else:
            results.append({"mailbox": mailbox, "success": False, "setting": None, "error": error})
    return results


async def disable_automatic_replies(
    company_id: int, payload: OutOfOfficeDisable
) -> list[dict[str, object]]:
    """Disable replies without overwriting messages, audience, or schedule fields."""
    allowed = {
        str(row["user_principal_name"]).casefold(): str(row["user_principal_name"])
        for row in await get_selectable_mailboxes(company_id)
    }
    requested = [str(mailbox) for mailbox in payload.mailboxes]
    unknown = [mailbox for mailbox in requested if mailbox.casefold() not in allowed]
    if unknown:
        raise ValueError("Unknown user mailbox selection: " + ", ".join(unknown))
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    mailboxes = [allowed[submitted.casefold()] for submitted in requested]
    return await _patch_mailbox_settings(
        token, mailboxes, {"automaticRepliesSetting": {"status": "disabled"}}
    )
