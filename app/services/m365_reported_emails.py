"""Turn user-reported email alerts into reviewed compliance searches.

Microsoft Defender raises an alert when a user reports a message as phish,
malware or junk ("Email reported by user as ...").  The alert's message
evidence carries the P1 sender, subject and received time.  This module reads
those alerts through Microsoft Graph ``alerts_v2`` and prepares a Spam Search
& Purge request from them: the search runs, the purge never does.  Purging
stays the separate, confirmed action on the Spam Search & Purge page.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from app.repositories import m365_spam_purge as purge_repo
from app.services import m365 as m365_service
from app.services import m365_spam_purge as purge_service


REPORTED_TITLE_PREFIX = "email reported by user as"
SEARCH_WINDOW = timedelta(hours=1)
DEFAULT_LOOKBACK_DAYS = 30
MAX_LOOKBACK_DAYS = 90
_MAX_PAGES = 10
_ALERTS_URL = "https://graph.microsoft.com/v1.0/security/alerts_v2"
_MESSAGE_EVIDENCE = "#microsoft.graph.security.analyzedMessageEvidence"
_ALERT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,190}$")

_PERMISSION_HINT = (
    " Reading reported-email alerts needs the SecurityAlert.Read.All "
    "application permission on the Microsoft 365 enterprise app. Reconnect "
    "the company from Microsoft 365 settings to grant it."
)


class ReportedEmailError(Exception):
    """A reported-email alert could not be loaded or turned into a search."""


def _failure(exc: m365_service.M365Error) -> ReportedEmailError:
    message = str(exc)
    if exc.http_status in (401, 403):
        message += _PERMISSION_HINT
    return ReportedEmailError(message)


def _parse_time(value: Any) -> datetime | None:
    """Parse a Graph timestamp to naive UTC."""
    if not value:
        return None
    return m365_service.parse_graph_datetime(str(value))


def _sender(value: Any) -> tuple[str | None, str | None]:
    """Return ``(address, display name)`` from a Graph ``emailSender``."""
    if not isinstance(value, dict):
        return None, None
    address = str(value.get("emailAddress") or "").strip() or None
    name = str(value.get("displayName") or "").strip() or None
    return address, name


def is_reported_email_alert(alert: dict[str, Any]) -> bool:
    return str(alert.get("title") or "").strip().lower().startswith(REPORTED_TITLE_PREFIX)


def report_kind(title: str) -> str:
    """Classify an alert title as ``phish``, ``junk`` or ``other``."""
    lowered = title.lower()
    if "phish" in lowered or "malware" in lowered:
        return "phish"
    if "not junk" in lowered:
        return "other"
    if "junk" in lowered:
        return "junk"
    return "other"


def report_label(title: str) -> str:
    """Shorten "Email reported by user as junk" to "Junk"."""
    cleaned = title.strip()
    if cleaned.lower().startswith(REPORTED_TITLE_PREFIX):
        cleaned = cleaned[len(REPORTED_TITLE_PREFIX):].strip()
    return (cleaned[:1].upper() + cleaned[1:]) if cleaned else title


def _web_url(value: Any) -> str | None:
    """Keep only https portal links so the page never renders another scheme."""
    url = str(value or "").strip()
    return url if url.lower().startswith("https://") else None


def search_window(anchor: datetime) -> tuple[datetime, datetime]:
    """Return the received-time window one hour either side of *anchor*."""
    anchor = anchor.replace(microsecond=0)
    return anchor - SEARCH_WINDOW, anchor + SEARCH_WINDOW


def summarise_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Flatten an ``alerts_v2`` alert into the fields a technician acts on."""
    message: dict[str, Any] = {}
    for item in alert.get("evidence") or []:
        if isinstance(item, dict) and item.get("@odata.type") == _MESSAGE_EVIDENCE:
            message = item
            break
    p1_sender, _ = _sender(message.get("p1Sender"))
    p2_sender, p2_name = _sender(message.get("p2Sender"))
    created = _parse_time(alert.get("createdDateTime"))
    received = _parse_time(message.get("receivedDateTime"))
    # The message's received time anchors the search; the alert's own
    # timestamps are only a fallback when the evidence omits it.
    anchor = received or _parse_time(alert.get("firstActivityDateTime")) or created
    window = search_window(anchor) if anchor else (None, None)
    title = str(alert.get("title") or "")
    recipient = str(message.get("recipientEmailAddress") or "").strip() or None
    return {
        "id": str(alert.get("id") or ""),
        "title": title,
        "kind": report_kind(title),
        "report_label": report_label(title),
        "severity": str(alert.get("severity") or "unknown"),
        "status": str(alert.get("status") or "unknown"),
        "created_at": created,
        "received_at": received,
        "anchor_at": anchor,
        "window_start": window[0],
        "window_end": window[1],
        "p1_sender": p1_sender,
        "p2_sender": p2_sender,
        "p2_sender_name": p2_name,
        "subject": str(message.get("subject") or "").strip() or None,
        "recipient": recipient,
        "network_message_id": message.get("networkMessageId"),
        "internet_message_id": message.get("internetMessageId"),
        "alert_url": _web_url(alert.get("alertWebUrl")),
        "has_message_evidence": bool(message),
    }


def build_alert_query(summary: dict[str, Any]) -> str:
    """Build the ContentMatchQuery for an alert's sender, subject and window."""
    clauses: list[str] = []
    start, end = summary.get("window_start"), summary.get("window_end")
    if start and end:
        clauses.append(
            "(Received>=" + start.strftime("%Y-%m-%dT%H:%M:%S")
            + " AND Received<=" + end.strftime("%Y-%m-%dT%H:%M:%S") + ")"
        )
    sender = summary.get("p1_sender")
    subject = summary.get("subject")
    if sender:
        clauses.append("(From:" + purge_service._quote_kql(sender) + ")")
    if subject:
        clauses.append("(Subject:" + purge_service._quote_kql(subject) + ")")
    if not sender and not subject:
        raise ReportedEmailError(
            "The alert has no sender or subject to search on. Open it in "
            "Microsoft Defender and build the search manually."
        )
    return " AND ".join(clauses)


async def _alerts_get(company_id: int, url: str) -> dict[str, Any]:
    """GET an alerts URL app-only, retrying a 403 once with a fresh token.

    A cached app-only token keeps the roles it was issued with, so a
    permission granted moments ago (by reconnecting) is not visible until a
    new token is requested.
    """
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    try:
        return await m365_service._graph_get(token, url)
    except m365_service.M365Error as exc:
        if exc.http_status != 403:
            raise
    token = await m365_service.acquire_access_token(
        company_id, force_client_credentials=True, force_refresh=True,
    )
    return await m365_service._graph_get(token, url)


async def list_reported_alerts(
    company_id: int, *, days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    """Return reported-email alerts from the last *days* days, newest first."""
    days = max(1, min(int(days), MAX_LOOKBACK_DAYS))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        _ALERTS_URL + "?$filter=" + quote(f"createdDateTime ge {since}", safe="")
        + "&$top=200"
    )
    try:
        alerts: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            page = await _alerts_get(company_id, url)
            alerts.extend(
                summarise_alert(item) for item in page.get("value") or []
                if isinstance(item, dict) and is_reported_email_alert(item)
            )
            url = page.get("@odata.nextLink")
            if not url:
                break
    except m365_service.M365Error as exc:
        raise _failure(exc) from exc
    alerts.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
    return alerts


async def get_reported_alert(company_id: int, alert_id: str) -> dict[str, Any]:
    if not _ALERT_ID_PATTERN.fullmatch(alert_id or ""):
        raise LookupError("Alert not found")
    try:
        alert = await _alerts_get(company_id, _ALERTS_URL + "/" + quote(alert_id, safe=""))
    except m365_service.M365Error as exc:
        if exc.http_status == 404:
            raise LookupError("Alert not found") from exc
        raise _failure(exc) from exc
    if not is_reported_email_alert(alert):
        raise LookupError("The alert is not a user-reported email alert")
    return summarise_alert(alert)


async def create_search_from_alert(
    company_id: int, user_id: int, alert_id: str,
) -> tuple[dict[str, Any], bool]:
    """Create and queue the compliance search for a reported-email alert.

    Returns ``(request, created)``.  An alert that already has a search returns
    that request with ``created`` false, so a double click never queues a second
    search.  The purge is never started here.
    """
    existing = await purge_repo.find_by_source_alert(company_id, alert_id)
    if existing:
        return existing, False
    summary = await get_reported_alert(company_id, alert_id)
    query = build_alert_query(summary)
    start, end = summary["window_start"], summary["window_end"]
    request = await purge_service.create_request(company_id, user_id, {
        "sender": summary["p1_sender"],
        "subject": (summary["subject"] or "")[:500] or None,
        "received_from": start.date() if start else None,
        "received_to": end.date() if end else None,
        "content_match_query": None,
        "query_override": query,
        "source_alert_id": summary["id"],
    })
    request = await purge_service.start_search(int(request["id"]), company_id)
    return request, True
