"""Turn user-reported email alerts into reviewed compliance searches.

Microsoft Defender raises an alert when a user reports a message as phish,
malware or junk ("Email reported by user as ...").  The alert's message
evidence carries the P1 sender, subject and received time.  This module reads
those alerts through Microsoft Graph ``alerts_v2`` and prepares a Spam Search
& Purge request from them: the search runs, the purge never does.  Purging
stays the separate, confirmed action on the Spam Search & Purge page.

Not every tenant gets those alerts through Graph, so three sources are read
and merged:

* ``alerts_v2`` - Defender for Office 365 Plan 2 / E5 tenants.
* ``security/threatSubmission/emailThreats`` - the user's report as a threat
  submission (Defender for Office 365 Plan 1, e.g. Business Premium, where
  the alert-policy alert never reaches ``alerts_v2``).
* The unified audit log ``UserSubmission`` record, read with
  ``Search-UnifiedAuditLog`` - every tenant with auditing, including
  Business Standard, which has no Defender for Office 365 at all.
"""

from __future__ import annotations

import json
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
# Threat submissions are only published on the beta endpoint.
_SUBMISSIONS_URL = "https://graph.microsoft.com/beta/security/threatSubmission/emailThreats"
_SUBMISSIONS_PORTAL_URL = "https://security.microsoft.com/reportsubmission?viewid=user"
SUBMISSION_PREFIX = "submission:"
AUDIT_PREFIX = "audit:"
_AUDIT_OPERATION = "UserSubmission"
_AUDIT_RESULT_SIZE = 1000
_AUDIT_LOOKUP_MINUTES = 10
_AUDIT_CATEGORIES = {
    "phish": ("phish", "Phish"),
    "phishing": ("phish", "Phish"),
    "malware": ("phish", "Malware"),
    "junk": ("junk", "Junk"),
    "spam": ("junk", "Junk"),
    "notjunk": ("other", "Not junk"),
    "notspam": ("other", "Not junk"),
}
_EMAIL_IN_ANGLES = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
_SUBMISSION_LABELS = {
    "phishing": ("phish", "Phish"),
    "malware": ("phish", "Malware"),
    "spam": ("junk", "Junk"),
    "notjunk": ("other", "Not junk"),
    "notspam": ("other", "Not junk"),
}
_MESSAGE_EVIDENCE = "#microsoft.graph.security.analyzedMessageEvidence"
_ALERT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,190}$")

_PERMISSION_HINT = (
    " Reading reported-email alerts needs the SecurityAlert.Read.All "
    "application permission on the Microsoft 365 enterprise app. Reconnect "
    "the company from Microsoft 365 settings to grant it."
)


_SUBMISSION_PERMISSION_HINT = (
    " Reading user-reported submissions needs the ThreatSubmission.Read.All "
    "application permission on the Microsoft 365 enterprise app. Reconnect "
    "the company from Microsoft 365 settings to grant it."
)


_AUDIT_HINT = (
    " Reading user reports from the audit log needs auditing enabled in the "
    "tenant and the Exchange Administrator role plus Exchange.ManageAsApp on "
    "the Microsoft 365 enterprise app. Run the Microsoft 365 diagnostics "
    "repair to grant them."
)


class ReportedEmailError(Exception):
    """A reported-email alert could not be loaded or turned into a search."""


def _failure(exc: m365_service.M365Error, hint: str = _PERMISSION_HINT) -> ReportedEmailError:
    message = str(exc)
    if exc.http_status in (401, 403):
        message += hint
    return ReportedEmailError(message)


def _source_failure(source: str, exc: ReportedEmailError) -> ReportedEmailError:
    return ReportedEmailError(f"{source}: {exc}")


def _parse_time(value: Any) -> datetime | None:
    """Parse a Graph timestamp to naive UTC."""
    if not value:
        return None
    text = str(value).strip()
    # Audit records omit the zone; their times are UTC, not server-local.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?", text):
        text += "Z"
    return m365_service.parse_graph_datetime(text)


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
        "source": "alert",
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
        "reporter": recipient,
        "network_message_id": message.get("networkMessageId"),
        "internet_message_id": message.get("internetMessageId"),
        "alert_url": _web_url(alert.get("alertWebUrl")),
        "has_message_evidence": bool(message),
    }


def _address(value: Any) -> str | None:
    """Return the bare address from ``a@b`` or ``Name <a@b>``."""
    text = str(value or "").strip()
    if not text:
        return None
    match = _EMAIL_IN_ANGLES.search(text)
    return (match.group(1) if match else text).strip() or None


def summarise_submission(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten a user's ``emailThreatSubmission`` into the alert row shape."""
    category = str(item.get("category") or "").strip()
    kind, label = _SUBMISSION_LABELS.get(category.lower(), ("other", category or "Unknown"))
    created = _parse_time(item.get("createdDateTime"))
    received = _parse_time(item.get("receivedDateTime"))
    anchor = received or created
    window = search_window(anchor) if anchor else (None, None)
    created_by = item.get("createdBy") if isinstance(item.get("createdBy"), dict) else {}
    user = created_by.get("user") if isinstance(created_by.get("user"), dict) else {}
    recipient = str(item.get("recipientEmailAddress") or "").strip() or None
    sender = _address(item.get("sender"))
    subject = str(item.get("subject") or "").strip() or None
    return {
        "id": SUBMISSION_PREFIX + str(item.get("id") or ""),
        "source": "submission",
        "title": "Email reported by user as " + label.lower(),
        "kind": kind,
        "report_label": label,
        "severity": "unknown",
        "status": str(item.get("status") or "unknown"),
        "created_at": created,
        "received_at": received,
        "anchor_at": anchor,
        "window_start": window[0],
        "window_end": window[1],
        "p1_sender": sender,
        "p2_sender": None,
        "p2_sender_name": None,
        "subject": subject,
        "recipient": recipient,
        "reporter": str(user.get("email") or "").strip() or recipient,
        "network_message_id": None,
        "internet_message_id": item.get("internetMessageId"),
        "alert_url": _SUBMISSIONS_PORTAL_URL,
        "has_message_evidence": bool(sender or subject),
    }


def _audit_data(record: dict[str, Any]) -> dict[str, Any]:
    data = record.get("AuditData")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _name_value_pairs(value: Any) -> list[tuple[str, str]]:
    """Collect every ``{"Name": ..., "Value": ...}`` pair nested in *value*."""
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        if "Name" in value and "Value" in value and not isinstance(value["Value"], (dict, list)):
            pairs.append((str(value["Name"]), str(value["Value"])))
        for child in value.values():
            pairs.extend(_name_value_pairs(child))
    elif isinstance(value, list):
        for child in value:
            pairs.extend(_name_value_pairs(child))
    return pairs


def _audit_category(data: dict[str, Any]) -> tuple[str, str]:
    """Return ``(kind, label)`` for what the user reported the message as.

    The category sits in a Name/Value list whose property name varies; any
    ``...Verdict`` entry is Microsoft's own verdict, not the user's report.
    """
    candidates = [
        (name, value) for name, value in _name_value_pairs(data)
        if "verdict" not in name.lower()
    ]
    candidates += [
        (key, str(value)) for key, value in data.items()
        if isinstance(value, str) and any(word in key.lower() for word in ("category", "submissiontype", "reportedas"))
    ]
    for _, value in candidates:
        mapped = _AUDIT_CATEGORIES.get(value.strip().lower().replace(" ", ""))
        if mapped:
            return mapped
    return "other", "Reported"


def _epoch(value: datetime) -> int:
    return int(value.replace(tzinfo=timezone.utc).timestamp())


def summarise_audit_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten a ``UserSubmission`` audit record into the alert row shape."""
    data = _audit_data(record)
    kind, label = _audit_category(data)
    created = _parse_time(data.get("CreationTime")) or _parse_time(record.get("CreationDate"))
    received = _parse_time(data.get("MessageDate"))
    anchor = received or created
    window = search_window(anchor) if anchor else (None, None)
    recipients = data.get("Recipients")
    if isinstance(recipients, str):
        recipients = [recipients]
    recipient = next(
        (str(item).strip() for item in recipients or [] if str(item).strip()), None,
    )
    reporter = str(data.get("UserId") or record.get("UserIds") or "").strip() or recipient
    record_id = str(data.get("Id") or record.get("Identity") or "").strip()
    sender = _address(data.get("P1Sender"))
    subject = str(data.get("Subject") or "").strip() or None
    p2_raw = str(data.get("P2Sender") or "").strip()
    p2_name = p2_raw.split("<", 1)[0].strip().strip('"').strip() or None if "<" in p2_raw else None
    return {
        "id": AUDIT_PREFIX + record_id + (f":{_epoch(created)}" if created else ""),
        "source": "audit",
        "title": "Email reported by user as " + label.lower(),
        "kind": kind,
        "report_label": label,
        "severity": "unknown",
        "status": "reported",
        "created_at": created,
        "received_at": received,
        "anchor_at": anchor,
        "window_start": window[0],
        "window_end": window[1],
        "p1_sender": sender,
        "p2_sender": _address(p2_raw),
        "p2_sender_name": p2_name,
        "subject": subject,
        "recipient": recipient,
        "reporter": reporter,
        "network_message_id": None,
        "internet_message_id": data.get("InternetMessageId"),
        "alert_url": None,
        "has_message_evidence": bool(sender or subject),
    }


async def _search_audit(company_id: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Run ``Search-UnifiedAuditLog`` for user submissions between two UTC times."""
    try:
        token, tenant = await m365_service._acquire_exo_access_token(company_id)
        payload = await m365_service._exo_invoke_command(token, tenant, "Search-UnifiedAuditLog", {
            "StartDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "EndDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Operations": _AUDIT_OPERATION,
            "ResultSize": _AUDIT_RESULT_SIZE,
        })
    except m365_service.M365Error as exc:
        raise _failure(exc, _AUDIT_HINT) from exc
    rows = payload.get("value") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [row for row in rows if isinstance(row, dict)]


async def list_reported_audit_records(
    company_id: int, *, days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    """Return ``UserSubmission`` audit records from the last *days* days."""
    days = max(1, min(int(days), MAX_LOOKBACK_DAYS))
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = await _search_audit(company_id, end - timedelta(days=days), end)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        summary = summarise_audit_record(row)
        if summary["id"] in seen:
            continue
        seen.add(summary["id"])
        items.append(summary)
    return items


async def _get_audit_record(company_id: int, reference: str) -> dict[str, Any]:
    record_id, _, stamp = reference.rpartition(":")
    if not record_id or not stamp.isdigit():
        raise LookupError("Reported email not found")
    created = datetime.fromtimestamp(int(stamp), tz=timezone.utc).replace(tzinfo=None)
    margin = timedelta(minutes=_AUDIT_LOOKUP_MINUTES)
    for row in await _search_audit(company_id, created - margin, created + margin):
        summary = summarise_audit_record(row)
        if summary["id"] == AUDIT_PREFIX + reference:
            return summary
    raise LookupError("Reported email not found in the audit log")


def _is_user_submission(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "").strip().lower()
    return source in ("", "user")


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


async def list_reported_submissions(
    company_id: int, *, days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[dict[str, Any]]:
    """Return user-reported email submissions from the last *days* days."""
    days = max(1, min(int(days), MAX_LOOKBACK_DAYS))
    since_dt = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        _SUBMISSIONS_URL + "?$filter=" + quote(f"createdDateTime ge {since}", safe="")
        + "&$top=200"
    )
    items: list[dict[str, Any]] = []
    try:
        try:
            page = await _alerts_get(company_id, url)
        except m365_service.M365Error as exc:
            if exc.http_status != 400:
                raise
            # The beta endpoint may reject the date filter; filter locally.
            page = await _alerts_get(company_id, _SUBMISSIONS_URL)
        for _ in range(_MAX_PAGES):
            items.extend(
                summarise_submission(item) for item in page.get("value") or []
                if isinstance(item, dict) and _is_user_submission(item)
            )
            next_url = page.get("@odata.nextLink")
            if not next_url:
                break
            page = await _alerts_get(company_id, next_url)
    except m365_service.M365Error as exc:
        raise _failure(exc, _SUBMISSION_PERMISSION_HINT) from exc
    return [item for item in items if item["created_at"] is None or item["created_at"] >= since_dt]


def _dedupe_keys(item: dict[str, Any]) -> list[tuple[str, ...]]:
    """Keys identifying one reported message across the three sources."""
    recipient = str(item.get("recipient") or "").strip().lower()
    keys: list[tuple[str, ...]] = []
    message_id = str(item.get("internet_message_id") or "").strip().lower()
    if message_id and recipient:
        keys.append(("message", message_id, recipient))
    sender = str(item.get("p1_sender") or "").strip().lower()
    received = item.get("received_at")
    if sender and recipient and received:
        keys.append(("received", sender, recipient, received.strftime("%Y%m%d%H%M")))
    return keys


async def load_reported_emails(
    company_id: int, *, days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Merge Defender alerts and user submissions; return ``(items, warnings)``.

    Each source fails independently: a source the tenant is not licensed for
    or cannot read becomes a warning while the others' rows are still shown.
    Only when every source fails is an error raised.
    """
    items: list[dict[str, Any]] = []
    errors: list[ReportedEmailError] = []
    # Richest source first: an alert wins over its submission, which wins
    # over its audit record.
    sources = (
        ("Defender alerts", list_reported_alerts),
        ("Threat submissions", list_reported_submissions),
        ("Audit log", list_reported_audit_records),
    )
    for name, loader in sources:
        try:
            items.extend(await loader(company_id, days=days))
        except ReportedEmailError as exc:
            errors.append(_source_failure(name, exc))
    if len(errors) == len(sources):
        raise ReportedEmailError(" ".join(str(exc) for exc in errors))
    seen: set[tuple[str, ...]] = set()
    merged: list[dict[str, Any]] = []
    for item in items:
        keys = _dedupe_keys(item)
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        merged.append(item)
    merged.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
    return merged, [str(exc) for exc in errors]


async def _get_submission(company_id: int, submission_id: str) -> dict[str, Any]:
    try:
        item = await _alerts_get(company_id, _SUBMISSIONS_URL + "/" + quote(submission_id, safe=""))
    except m365_service.M365Error as exc:
        if exc.http_status == 404:
            raise LookupError("Reported email not found") from exc
        raise _failure(exc, _SUBMISSION_PERMISSION_HINT) from exc
    return summarise_submission(item)


async def get_reported_alert(company_id: int, alert_id: str) -> dict[str, Any]:
    if not _ALERT_ID_PATTERN.fullmatch(alert_id or ""):
        raise LookupError("Alert not found")
    if alert_id.startswith(AUDIT_PREFIX):
        return await _get_audit_record(company_id, alert_id[len(AUDIT_PREFIX):])
    if alert_id.startswith(SUBMISSION_PREFIX):
        submission_id = alert_id[len(SUBMISSION_PREFIX):]
        if not submission_id:
            raise LookupError("Reported email not found")
        return await _get_submission(company_id, submission_id)
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
