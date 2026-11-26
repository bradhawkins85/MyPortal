"""Customer satisfaction (CSAT) surveys for resolved tickets.

Once a ticket has been resolved or closed for the configured delay, a
background job emails its requester a one-question survey ("How satisfied
were you?", 1-5). Every way a ticket can be resolved (technician pages, the
API, bulk actions, automations, email) is covered because the job looks for
resolved tickets rather than hooking each path. Each ticket gets at most one
survey, and only tickets resolved after surveys were switched on are sent one.

The email's rating links open a page where the requester confirms the rating
and may add a comment. Opening a link never records a rating on its own, so
mail scanners that follow links cannot answer the survey. Only the SHA-256
hash of each link's token is stored.

A response is posted to the ticket as an internal note and fires the
``tickets.csat_received`` automation event. A rating at or below the low-score
threshold opens a follow-up on the CSAT page until a technician marks it done.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from app.core.config import get_settings as get_app_settings
from app.repositories import csat as csat_repo

SURVEY_TEMPLATE_SLUG = "ticket-csat-survey"
SURVEY_SUBJECT_TEMPLATE_SLUG = "ticket-csat-survey-subject"
EVENT_NAME = "tickets.csat_received"

RATING_LABELS: dict[int, str] = {
    5: "Very satisfied",
    4: "Satisfied",
    3: "Neutral",
    2: "Dissatisfied",
    1: "Very dissatisfied",
}
SATISFIED_MIN_RATING = 4
MAX_COMMENT_LENGTH = 2000

DEFAULT_DELAY_MINUTES = 60
MAX_DELAY_MINUTES = 7 * 24 * 60
DEFAULT_EXPIRY_DAYS = 14
MAX_EXPIRY_DAYS = 90
DEFAULT_LOW_SCORE_THRESHOLD = 2
# Tickets resolved longer ago than this are never surveyed, so turning surveys
# on (or recovering from an outage) does not email a backlog of old tickets.
SEND_WINDOW = timedelta(days=7)
BATCH_SIZE = 50
JOB_INTERVAL_SECONDS = 300
JOB_STARTUP_DELAY_SECONDS = 90

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,64}")

DEFAULT_SUBJECT = "How did we do? Ticket {{ ticket.number }}: {{ ticket.subject }}"
DEFAULT_BODY = (
    "<p>Hi {{ recipient.first_name }},</p><p>We recently resolved ticket {{ ticket.number }}: "
    "<strong>{{ ticket.subject }}</strong>. How satisfied were you with the support you received?</p>"
    '<p><a href="{{ survey.link_5 }}">Very satisfied</a> &middot; <a href="{{ survey.link_4 }}">Satisfied</a> '
    '&middot; <a href="{{ survey.link_3 }}">Neutral</a> &middot; <a href="{{ survey.link_2 }}">Dissatisfied</a> '
    '&middot; <a href="{{ survey.link_1 }}">Very dissatisfied</a></p>'
    "<p>It takes a few seconds, and you can add a comment on the next page. "
    "This link works until {{ survey.expires }}.</p><p>Thank you,<br>{{ app.name }}</p>"
)


class SurveyUnavailable(Exception):
    """The survey link is unknown, expired or already answered (``str(exc)`` is the state)."""


@dataclass(frozen=True)
class CsatSettings:
    enabled: bool = False
    delay_minutes: int = DEFAULT_DELAY_MINUTES
    low_score_threshold: int = DEFAULT_LOW_SCORE_THRESHOLD
    expiry_days: int = DEFAULT_EXPIRY_DAYS
    enabled_at: datetime | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str) and value:
        try:
            return _as_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _clamp(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def survey_url(token: str, rating: int | None = None) -> str:
    base = str(get_app_settings().portal_url or "").rstrip("/")
    url = f"{base}/csat/{token}"
    return f"{url}?rating={rating}" if rating else url


def parse_rating(value: Any) -> int | None:
    try:
        rating = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return rating if rating in RATING_LABELS else None


def is_satisfied(rating: int | None) -> bool:
    return rating is not None and rating >= SATISFIED_MIN_RATING


def csat_percent(satisfied: int, responses: int) -> int | None:
    """Share of responses rated 4 or 5, the usual CSAT score."""

    if responses <= 0:
        return None
    return round(100 * satisfied / responses)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def load_settings() -> CsatSettings:
    row = await csat_repo.get_settings()
    if not row:
        return CsatSettings()
    return CsatSettings(
        enabled=bool(row.get("enabled")),
        delay_minutes=_clamp(row.get("delay_minutes"), default=DEFAULT_DELAY_MINUTES, low=0, high=MAX_DELAY_MINUTES),
        low_score_threshold=_clamp(
            row.get("low_score_threshold"), default=DEFAULT_LOW_SCORE_THRESHOLD, low=0, high=4
        ),
        expiry_days=_clamp(row.get("expiry_days"), default=DEFAULT_EXPIRY_DAYS, low=1, high=MAX_EXPIRY_DAYS),
        enabled_at=_as_naive_utc(row.get("enabled_at")),
    )


def parse_settings_form(form: Mapping[str, Any], current: CsatSettings) -> CsatSettings:
    enabled = str(form.get("enabled") or "").lower() in {"1", "true", "on", "yes"}
    enabled_at = current.enabled_at if current.enabled else None
    if enabled and enabled_at is None:
        enabled_at = _utcnow()
    return CsatSettings(
        enabled=enabled,
        delay_minutes=_clamp(form.get("delay_minutes"), default=current.delay_minutes, low=0, high=MAX_DELAY_MINUTES),
        low_score_threshold=_clamp(
            form.get("low_score_threshold"), default=current.low_score_threshold, low=0, high=4
        ),
        expiry_days=_clamp(form.get("expiry_days"), default=current.expiry_days, low=1, high=MAX_EXPIRY_DAYS),
        enabled_at=enabled_at if enabled else None,
    )


async def save_settings(settings: CsatSettings, excluded_company_ids: list[int]) -> None:
    await csat_repo.save_settings(
        enabled=settings.enabled,
        delay_minutes=settings.delay_minutes,
        low_score_threshold=settings.low_score_threshold,
        expiry_days=settings.expiry_days,
        enabled_at=settings.enabled_at,
    )
    await csat_repo.replace_excluded_companies(excluded_company_ids)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


async def _requester_contact(ticket: Mapping[str, Any]) -> dict[str, str] | None:
    from app.repositories import staff as staff_repo
    from app.repositories import users as users_repo

    if ticket.get("requester_id") is not None:
        user = await users_repo.get_user_by_id(int(ticket["requester_id"]))
        if user and user.get("email"):
            return {"email": str(user["email"]), "first_name": str(user.get("first_name") or "").strip()}
    if ticket.get("requester_staff_id") is not None:
        staff = await staff_repo.get_staff_by_id(int(ticket["requester_staff_id"]))
        if staff and staff.get("email"):
            return {"email": str(staff["email"]), "first_name": str(staff.get("first_name") or "").strip()}
    return None


async def _technician_name(user_id: Any) -> str:
    from app.repositories import users as users_repo

    if user_id is None:
        return ""
    user = await users_repo.get_user_by_id(int(user_id))
    if not user:
        return ""
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return name.strip() or str(user.get("email") or "")


def _format_date(value: datetime) -> str:
    return value.strftime("%d %b %Y")


def survey_context(
    ticket: Mapping[str, Any],
    *,
    token: str,
    recipient: Mapping[str, str],
    technician_name: str,
    expires_at: datetime,
) -> dict[str, Any]:
    app_settings = get_app_settings()
    survey: dict[str, Any] = {"link": survey_url(token), "expires": _format_date(expires_at)}
    for rating in RATING_LABELS:
        survey[f"link_{rating}"] = survey_url(token, rating)
    return {
        "recipient": {"first_name": recipient.get("first_name") or "there", "email": recipient.get("email")},
        "ticket": {
            "id": ticket.get("id"),
            "number": ticket.get("ticket_number") or ticket.get("id"),
            "subject": ticket.get("subject") or "",
        },
        "technician": {"name": technician_name or "our team"},
        "survey": survey,
        "app": {"name": getattr(app_settings, "app_name", None) or "MyPortal"},
        "portal": {"url": str(app_settings.portal_url or "").rstrip("/")},
    }


async def render_survey_email(context: Mapping[str, Any]) -> tuple[str, str]:
    from app.services import message_templates as message_templates_service

    subject, _ = await message_templates_service.render_template_content(
        SURVEY_SUBJECT_TEMPLATE_SLUG, context, default_content=DEFAULT_SUBJECT, default_content_type="text/plain"
    )
    subject = " ".join(subject.split())[:255] or "How did we do?"
    body, content_type = await message_templates_service.render_template_content(
        SURVEY_TEMPLATE_SLUG, context, default_content=DEFAULT_BODY, default_content_type="text/html"
    )
    if content_type != "text/html":
        import html as html_lib

        body = "".join(
            "<p>" + "<br>".join(html_lib.escape(line) for line in part.split("\n")) + "</p>"
            for part in body.split("\n\n")
            if part.strip()
        )
    return subject, body


async def send_survey(ticket: Mapping[str, Any], settings: CsatSettings) -> str:
    """Create and email the survey for one ticket. Returns the survey's status."""

    from app.services import email as email_service

    ticket_id = int(ticket["id"])
    recipient = await _requester_contact(ticket)
    survey_id = await csat_repo.claim_ticket(
        ticket_id=ticket_id,
        company_id=ticket.get("company_id"),
        technician_user_id=ticket.get("assigned_user_id"),
        recipient_email=(recipient or {}).get("email"),
    )
    if survey_id is None:
        return "claimed"
    if not recipient:
        await csat_repo.mark_skipped(survey_id, "The ticket has no requester with an email address.")
        return "skipped"
    token = generate_token()
    now = _utcnow()
    expires_at = now + timedelta(days=settings.expiry_days)
    context = survey_context(
        ticket,
        token=token,
        recipient=recipient,
        technician_name=await _technician_name(ticket.get("assigned_user_id")),
        expires_at=expires_at,
    )
    try:
        subject, html_body = await render_survey_email(context)
        sent, _ = await email_service.send_email(
            subject=subject, recipients=[recipient["email"]], html_body=html_body
        )
    except Exception as exc:  # pragma: no cover - mail transport failures
        logger.warning("Failed to send CSAT survey for ticket {}: {}", ticket_id, exc)
        await csat_repo.mark_failed(survey_id, "The survey email could not be sent.")
        return "failed"
    if not sent:
        await csat_repo.mark_failed(survey_id, "The survey email could not be sent. Check the email settings.")
        return "failed"
    await csat_repo.mark_sent(survey_id, token_hash=hash_token(token), sent_at=now, expires_at=expires_at)
    return "sent"


async def send_due_surveys(now: datetime | None = None) -> dict[str, int]:
    """Send surveys for tickets resolved at least ``delay_minutes`` ago."""

    settings = await load_settings()
    counts: dict[str, int] = {}
    if not settings.enabled:
        return counts
    now = now or _utcnow()
    resolved_after = now - SEND_WINDOW
    if settings.enabled_at and settings.enabled_at > resolved_after:
        resolved_after = settings.enabled_at
    tickets = await csat_repo.list_due_tickets(
        resolved_before=now - timedelta(minutes=settings.delay_minutes),
        resolved_after=resolved_after,
        limit=BATCH_SIZE,
    )
    for ticket in tickets:
        outcome = await send_survey(ticket, settings)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


async def survey_job() -> None:
    """Background loop: send due surveys every few minutes on one instance."""

    from app.services.singleton_jobs import singleton_run

    @singleton_run("csat_surveys", ttl_seconds=JOB_INTERVAL_SECONDS)
    async def _run_once() -> None:
        await send_due_surveys()

    await asyncio.sleep(JOB_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await _run_once()
        except Exception as exc:  # pragma: no cover - retried on the next pass
            logger.warning("CSAT survey job failed: {}", exc)
        await asyncio.sleep(JOB_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Responding
# ---------------------------------------------------------------------------


async def get_open_survey(token: str | None, *, now: datetime | None = None) -> dict[str, Any]:
    if not token or not _TOKEN_PATTERN.fullmatch(token):
        raise SurveyUnavailable("invalid")
    survey = await csat_repo.get_by_token_hash(hash_token(token))
    if not survey:
        raise SurveyUnavailable("invalid")
    if survey.get("status") == "responded":
        raise SurveyUnavailable("answered")
    if survey.get("status") != "sent":
        raise SurveyUnavailable("invalid")
    expires_at = _as_naive_utc(survey.get("expires_at"))
    if expires_at is not None and expires_at < (now or _utcnow()):
        raise SurveyUnavailable("expired")
    return survey


def clean_comment(value: Any) -> str | None:
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:MAX_COMMENT_LENGTH] or None


def response_note(rating: int, comment: str | None) -> str:
    import html as html_lib

    lines = [f"<p><strong>Customer satisfaction:</strong> {rating}/5 ({RATING_LABELS[rating]})</p>"]
    if comment:
        lines.append("<blockquote>" + "<br>".join(html_lib.escape(line) for line in comment.split("\n")) + "</blockquote>")
    return "".join(lines)


async def submit_response(survey: Mapping[str, Any], *, rating: int, comment: str | None) -> bool:
    """Record the requester's rating; returns ``False`` if it was already answered."""

    settings = await load_settings()
    is_low = rating <= settings.low_score_threshold
    recorded = await csat_repo.record_response(
        int(survey["id"]),
        rating=rating,
        comment=comment,
        responded_at=_utcnow(),
        follow_up_status="open" if is_low else None,
    )
    if not recorded:
        return False
    await _after_response(survey, rating=rating, comment=comment, is_low=is_low)
    return True


async def _after_response(survey: Mapping[str, Any], *, rating: int, comment: str | None, is_low: bool) -> None:
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    ticket_id = int(survey["ticket_id"])
    try:
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=None,
            body=response_note(rating, comment),
            is_internal=True,
            author_display_name="Customer satisfaction survey",
        )
        await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
    except Exception as exc:  # pragma: no cover - the rating is already stored
        logger.warning("Failed to post CSAT response to ticket {}: {}", ticket_id, exc)
    try:
        await tickets_service.emit_ticket_csat_received_event(
            ticket_id,
            csat={
                "rating": rating,
                "label": RATING_LABELS[rating],
                "comment": comment or "",
                "is_low": is_low,
                "is_satisfied": is_satisfied(rating),
            },
        )
    except Exception as exc:  # pragma: no cover - automations must not lose the response
        logger.warning("Failed to fire CSAT automation event for ticket {}: {}", ticket_id, exc)


async def close_follow_up(survey_id: int, *, user_id: int) -> bool:
    return await csat_repo.set_follow_up(survey_id, status="done", user_id=user_id, when=_utcnow())


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


PERIOD_DAYS: dict[str, int] = {"30": 30, "90": 90, "365": 365}
DEFAULT_PERIOD = "90"


def period_start(period: str | None, *, now: datetime | None = None) -> tuple[str, datetime]:
    key = period if period in PERIOD_DAYS else DEFAULT_PERIOD
    return key, (now or _utcnow()) - timedelta(days=PERIOD_DAYS[key])


def _with_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        responses = int(row.get("responses") or 0)
        satisfied = int(row.get("satisfied") or 0)
        row["responses"] = responses
        row["satisfied"] = satisfied
        row["csat_percent"] = csat_percent(satisfied, responses)
        average = row.get("average")
        row["average"] = round(float(average), 1) if average is not None else None
    return rows


async def build_report(*, period: str | None, company_id: int | None = None) -> dict[str, Any]:
    key, since = period_start(period)
    counts = await csat_repo.summary_counts(since=since, company_id=company_id)
    technicians = _with_scores(await csat_repo.breakdown_by_technician(since=since, company_id=company_id))
    for row in technicians:
        name = " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part).strip()
        row["name"] = name or row.get("email") or "Unassigned"
    companies = [] if company_id is not None else _with_scores(await csat_repo.breakdown_by_company(since=since))
    responses = await csat_repo.list_responses(since=since, company_id=company_id)
    return {
        "period": key,
        "since": since,
        "counts": counts,
        "csat_percent": csat_percent(counts["satisfied"], counts["responded"]),
        "response_rate": csat_percent(counts["responded"], counts["sent"]),
        "technicians": technicians,
        "companies": companies,
        "responses": responses,
        "follow_ups": await csat_repo.list_responses(
            since=datetime(2000, 1, 1), company_id=company_id, follow_up_only=True
        ),
    }
