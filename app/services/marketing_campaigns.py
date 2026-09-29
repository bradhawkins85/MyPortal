"""Marketing email campaigns.

A campaign renders a subject and body for each contact picked by its audience
filters, queues one message per recipient, and sends them only while the
recipient's company is inside business hours. Sales campaigns carry an
unsubscribe link; replies are matched back to the recipient so the first reply
opens a ticket that technicians can follow up.
"""

from __future__ import annotations

import html
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from email.utils import formataddr, parseaddr
from typing import Any
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email
from loguru import logger

from app.core.config import get_settings
from app.repositories import marketing_campaigns as campaign_repo
from app.services import business_hours as business_hours_service
from app.services import message_templates as message_templates_service

CATEGORY_UPDATES = "updates"
CATEGORY_SALES = "sales"
CATEGORIES = (CATEGORY_UPDATES, CATEGORY_SALES)
CONTACT_SCOPES = ("billing", "all")
COMPANY_MODES = ("all", "selected")
BUSINESS_HOURS_SOURCES = ("company", "global")
REPLY_MATCH_WINDOW = timedelta(days=90)
SEND_BATCH_LIMIT = 100

_MESSAGE_ID_PATTERN = re.compile(r"^<?mkt-([0-9a-f]{32})@", re.IGNORECASE)
_REPLY_PREFIX_PATTERN = re.compile(r"^\s*((re|fw|fwd|aw|sv|antw)\s*(\[\d+\])?\s*:\s*)+", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^>]+>")

VARIABLES = (
    ("contact.first_name", "Contact first name"),
    ("contact.last_name", "Contact last name"),
    ("contact.full_name", "Contact full name"),
    ("contact.email", "Contact email"),
    ("contact.job_title", "Contact job title"),
    ("contact.department", "Contact department"),
    ("company.name", "Company name"),
    ("campaign.name", "Campaign name"),
    ("portal.url", "Portal address"),
    ("unsubscribe_url", "Sales unsubscribe link (sales emails only)"),
)


class CampaignError(ValueError):
    """Raised when a campaign cannot be saved or sent as requested."""


# ---------------------------------------------------------------------------
# Form parsing
# ---------------------------------------------------------------------------


def _int_list(values: Sequence[Any]) -> list[int]:
    result: list[int] = []
    for value in values:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if number > 0 and number not in result:
            result.append(number)
    return result


def _term_list(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = re.split(r"[,\n]", str(raw or ""))
    terms: list[str] = []
    for item in items:
        term = item.strip()[:100]
        if term and term.lower() not in {existing.lower() for existing in terms}:
            terms.append(term)
    return terms


def _email_list(raw: Any) -> list[str]:
    emails: list[str] = []
    for item in _term_list(raw if isinstance(raw, (list, tuple)) else re.split(r"[,;\s]+", str(raw or ""))):
        normalised = normalise_email(item)
        if normalised and normalised not in emails:
            emails.append(normalised)
    return emails


def normalise_email(value: Any) -> str | None:
    candidate = parseaddr(str(value or ""))[1].strip()
    if not candidate:
        return None
    try:
        return validate_email(candidate, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def normalise_audience(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    company_mode = str(raw.get("company_mode") or "all")
    contact_scope = str(raw.get("contact_scope") or "billing")
    return {
        "company_mode": company_mode if company_mode in COMPANY_MODES else "all",
        "company_ids": _int_list(raw.get("company_ids") or []),
        "exclude_company_ids": _int_list(raw.get("exclude_company_ids") or []),
        "vip_only": bool(raw.get("vip_only")),
        "asset_field_ids": _int_list(raw.get("asset_field_ids") or []),
        "product_ids": _int_list(raw.get("product_ids") or []),
        "contact_scope": contact_scope if contact_scope in CONTACT_SCOPES else "billing",
        "job_titles": _term_list(raw.get("job_titles") or []),
        "departments": _term_list(raw.get("departments") or []),
        "include_emails": _email_list(raw.get("include_emails") or []),
        "exclude_emails": _email_list(raw.get("exclude_emails") or []),
    }


def _form_values(form: Any, key: str) -> list[Any]:
    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        return list(getlist(key))
    value = form.get(key)
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _form_flag(form: Any, key: str) -> bool:
    value = form.get(key)
    return str(value or "").strip().lower() not in {"", "0", "false", "off"}


def audience_from_form(form: Any) -> dict[str, Any]:
    return normalise_audience(
        {
            "company_mode": form.get("company_mode"),
            "company_ids": _form_values(form, "company_ids"),
            "exclude_company_ids": _form_values(form, "exclude_company_ids"),
            "vip_only": _form_flag(form, "vip_only"),
            "asset_field_ids": _form_values(form, "asset_field_ids"),
            "product_ids": _form_values(form, "product_ids"),
            "contact_scope": form.get("contact_scope"),
            "job_titles": form.get("job_titles"),
            "departments": form.get("departments"),
            "include_emails": form.get("include_emails"),
            "exclude_emails": form.get("exclude_emails"),
        }
    )


def parse_local_datetime(value: Any, tz_offset_minutes: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CampaignError("Enter a valid date and time for the scheduled send.") from exc
    if moment.tzinfo is None:
        try:
            offset = int(str(tz_offset_minutes or "0").strip() or 0)
        except ValueError:
            offset = 0
        # ``Date.getTimezoneOffset`` is minutes *behind* UTC.
        moment = moment.replace(tzinfo=timezone.utc) + timedelta(minutes=offset)
    return moment.astimezone(timezone.utc)


def campaign_fields_from_form(form: Any) -> dict[str, Any]:
    name = str(form.get("name") or "").strip()
    if not name:
        raise CampaignError("Enter a campaign name.")
    if len(name) > 160:
        raise CampaignError("Campaign name must be 160 characters or fewer.")
    subject = str(form.get("subject") or "").strip()
    if not subject:
        raise CampaignError("Enter an email subject.")
    if len(subject) > 255:
        raise CampaignError("Subject must be 255 characters or fewer.")
    category = str(form.get("category") or CATEGORY_UPDATES).strip().lower()
    if category not in CATEGORIES:
        raise CampaignError("Choose whether this is an Updates or Sales email.")
    template_id: int | None = None
    raw_template = str(form.get("message_template_id") or "").strip()
    if raw_template:
        try:
            template_id = int(raw_template)
        except ValueError as exc:
            raise CampaignError("Choose a valid message template.") from exc
    body_html = str(form.get("body_html") or "").strip() or None
    if template_id is None and not body_html:
        raise CampaignError("Choose a message template or write the email body.")
    sender_email: str | None = None
    raw_sender = str(form.get("sender_email") or "").strip()
    if raw_sender:
        sender_email = normalise_email(raw_sender)
        if not sender_email:
            raise CampaignError("Enter a valid sender email address.")
    reply_to: str | None = None
    raw_reply_to = str(form.get("reply_to") or "").strip()
    if raw_reply_to:
        reply_to = normalise_email(raw_reply_to)
        if not reply_to:
            raise CampaignError("Enter a valid reply-to email address.")
    source = str(form.get("business_hours_source") or "company").strip().lower()
    return {
        "name": name,
        "category": category,
        "subject": subject,
        "message_template_id": template_id,
        "body_html": body_html,
        "sender_email": sender_email,
        "sender_name": str(form.get("sender_name") or "").strip()[:160] or None,
        "reply_to": reply_to,
        "audience": audience_from_form(form),
        "business_hours_source": source if source in BUSINESS_HOURS_SOURCES else "company",
        "scheduled_for": parse_local_datetime(form.get("scheduled_for"), form.get("tz_offset")),
    }


# ---------------------------------------------------------------------------
# Audience resolution
# ---------------------------------------------------------------------------


def _full_name(contact: Mapping[str, Any]) -> str:
    return " ".join(
        part for part in (str(contact.get("first_name") or "").strip(), str(contact.get("last_name") or "").strip()) if part
    )


async def resolve_audience(campaign: Mapping[str, Any]) -> dict[str, Any]:
    """Return the recipients a campaign would send to, plus who was left out and why."""

    audience = normalise_audience(campaign.get("audience"))
    contacts = await campaign_repo.find_audience_contacts(audience)
    include_emails = audience["include_emails"]
    if include_emails:
        found = await campaign_repo.find_staff_by_emails(include_emails)
        found_emails = {str(row.get("email") or "").lower() for row in found}
        contacts.extend(found)
        contacts.extend(
            {"staff_id": None, "company_id": None, "email": email, "company_name": None}
            for email in include_emails
            if email not in found_emails
        )

    exclude_emails = set(audience["exclude_emails"])
    recipients: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for contact in contacts:
        email = normalise_email(contact.get("email"))
        entry = {
            "staff_id": contact.get("staff_id"),
            "company_id": contact.get("company_id"),
            "company_name": contact.get("company_name"),
            "email": email or str(contact.get("email") or ""),
            "name": _full_name(contact) or None,
            "job_title": contact.get("job_title"),
            "is_billing_contact": bool(contact.get("is_billing_contact")),
        }
        if not email:
            excluded.append({**entry, "reason": "Invalid email address"})
            continue
        if email in seen:
            continue
        seen.add(email)
        if email in exclude_emails:
            excluded.append({**entry, "reason": "Excluded by address"})
            continue
        recipients.append(entry)

    emails = [recipient["email"] for recipient in recipients]
    opted_out: set[str] = set()
    if campaign.get("category") == CATEGORY_SALES:
        opted_out = await campaign_repo.list_opted_out(emails, CATEGORY_SALES)
    blocked: set[str] = set()
    try:
        from app.repositories import email_blocklist as email_blocklist_repo

        _, blocked_list = await email_blocklist_repo.filter_allowed(emails)
        blocked = {address.lower() for address in blocked_list}
    except Exception as exc:  # pragma: no cover - blocklist must not break previews
        logger.warning("Marketing audience blocklist check failed", error=str(exc))

    final: list[dict[str, Any]] = []
    for recipient in recipients:
        if recipient["email"] in opted_out:
            excluded.append({**recipient, "reason": "Unsubscribed from sales emails"})
        elif recipient["email"] in blocked:
            excluded.append({**recipient, "reason": "On the email blocklist"})
        else:
            final.append(recipient)
    return {"recipients": final, "excluded": excluded}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _portal_url() -> str:
    return str(get_settings().portal_url or "").rstrip("/")


def unsubscribe_url(token: str) -> str:
    return f"{_portal_url()}/marketing/unsubscribe/{token}"


def default_sender() -> str | None:
    settings = get_settings()
    return normalise_email(settings.smtp_from or settings.smtp_user)


def message_id_for(token: str, sender: str | None) -> str:
    domain = ""
    if sender and "@" in sender:
        domain = sender.rsplit("@", 1)[1]
    if not domain:
        domain = urlparse(_portal_url()).hostname or "myportal.local"
    return f"<mkt-{token}@{domain}>"


def token_from_message_ids(message_ids: Sequence[str] | None) -> str | None:
    for message_id in message_ids or []:
        match = _MESSAGE_ID_PATTERN.match(str(message_id or "").strip())
        if match:
            return match.group(1).lower()
    return None


def normalise_subject(subject: str | None) -> str:
    stripped = _REPLY_PREFIX_PATTERN.sub("", str(subject or ""))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def build_context(
    campaign: Mapping[str, Any],
    contact: Mapping[str, Any],
    *,
    token: str,
) -> dict[str, Any]:
    first_name = str(contact.get("first_name") or "").strip()
    last_name = str(contact.get("last_name") or "").strip()
    full_name = _full_name(contact) or str(contact.get("name") or "").strip()
    if not first_name and full_name:
        first_name = full_name.split(" ", 1)[0]
    contact_values = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": str(contact.get("email") or ""),
        "job_title": str(contact.get("job_title") or ""),
        "department": str(contact.get("department") or ""),
    }
    context: dict[str, Any] = {
        "contact": contact_values,
        "staff": contact_values,
        "company": {"id": contact.get("company_id"), "name": str(contact.get("company_name") or "")},
        "campaign": {"id": campaign.get("id"), "name": str(campaign.get("name") or "")},
        "portal": {"url": _portal_url()},
        "unsubscribe_url": unsubscribe_url(token) if campaign.get("category") == CATEGORY_SALES else "",
    }
    return context


async def _body_source(campaign: Mapping[str, Any]) -> tuple[str, str]:
    template_id = campaign.get("message_template_id")
    if template_id:
        template = await message_templates_service.get_template(int(template_id))
        if template and str(template.get("content") or "").strip():
            return str(template["content"]), str(template.get("content_type") or "text/html")
    return str(campaign.get("body_html") or ""), "text/html"


def _sales_footer(url: str) -> str:
    safe_url = html.escape(url, quote=True)
    return (
        '<p style="margin-top:24px;font-size:12px;color:#6b7280;">'
        "You are receiving this email because you are a contact of one of our customers. "
        f'<a href="{safe_url}">Unsubscribe from sales emails</a>. '
        "You will still receive service and account updates."
        "</p>"
    )


def html_to_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", value)
    text = html.unescape(_TAG_PATTERN.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


async def render_email(
    campaign: Mapping[str, Any],
    contact: Mapping[str, Any],
    *,
    token: str,
) -> dict[str, str]:
    context = build_context(campaign, contact, token=token)
    subject = message_templates_service.render_content(str(campaign.get("subject") or ""), context)
    subject = re.sub(r"[\r\n]+", " ", subject).strip()
    content, content_type = await _body_source(campaign)
    if content_type == "text/plain":
        body = message_templates_service.render_content(content, context, escape_html=True)
        body_html = "<div>" + body.replace("\n", "<br>") + "</div>"
    else:
        body_html = message_templates_service.render_content(content, context, escape_html=True)
    if campaign.get("category") == CATEGORY_SALES:
        body_html += _sales_footer(context["unsubscribe_url"])
    return {"subject": subject, "html": body_html, "text": html_to_text(body_html)}


def _sender_header(campaign: Mapping[str, Any]) -> str | None:
    sender = campaign.get("sender_email") or default_sender()
    if not sender:
        return None
    name = str(campaign.get("sender_name") or "").strip()
    return formataddr((name, sender)) if name else sender


def _headers(campaign: Mapping[str, Any], token: str) -> dict[str, str]:
    sender = campaign.get("sender_email") or default_sender()
    headers = {"Message-ID": message_id_for(token, sender), "X-MyPortal-Campaign": str(campaign.get("id") or "")}
    if campaign.get("category") == CATEGORY_SALES:
        headers["List-Unsubscribe"] = f"<{unsubscribe_url(token)}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return headers


async def _dispatch(
    campaign: Mapping[str, Any],
    *,
    to: str,
    rendered: Mapping[str, str],
    token: str,
) -> tuple[bool, dict[str, Any] | None]:
    from app.services import email as email_service

    return await email_service.send_email(
        subject=rendered["subject"],
        recipients=[to],
        html_body=rendered["html"],
        text_body=rendered["text"],
        sender=_sender_header(campaign),
        reply_to=campaign.get("reply_to") or None,
        headers=_headers(campaign, token),
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _validate_ready(campaign: Mapping[str, Any]) -> None:
    if campaign.get("category") == CATEGORY_SALES and not _portal_url():
        raise CampaignError("Set PORTAL_URL before sending sales emails so the unsubscribe link works.")
    if not (campaign.get("sender_email") or default_sender()):
        raise CampaignError("Set a sender address on the campaign or configure SMTP_FROM.")


async def send_test(campaign: Mapping[str, Any], to_email: str) -> None:
    address = normalise_email(to_email)
    if not address:
        raise CampaignError("Your account does not have a valid email address for the test send.")
    _validate_ready(campaign)
    resolved = await resolve_audience(campaign)
    sample = resolved["recipients"][0] if resolved["recipients"] else {"email": address}
    contact = await contact_for(sample)
    token = "0" * 32
    rendered = dict(await render_email(campaign, contact, token=token))
    rendered["subject"] = "[TEST] " + rendered["subject"]
    sent, _ = await _dispatch(campaign, to=address, rendered=rendered, token=token)
    if not sent:
        raise CampaignError("The test email was not sent. Check the email settings and blocklist.")


async def queue_campaign(campaign_id: int) -> int:
    campaign = await campaign_repo.get_campaign(campaign_id)
    if not campaign:
        raise CampaignError("Campaign not found.")
    if campaign.get("status") != "draft":
        raise CampaignError("Only draft campaigns can be sent.")
    _validate_ready(campaign)
    resolved = await resolve_audience(campaign)
    if not resolved["recipients"]:
        raise CampaignError("No recipients match this audience.")
    now = datetime.now(timezone.utc)
    if not await campaign_repo.mark_campaign_sending(campaign_id, queued_at=now):
        raise CampaignError("Campaign has already been queued.")
    send_after = campaign.get("scheduled_for")
    rows = [
        {
            "staff_id": recipient.get("staff_id"),
            "company_id": recipient.get("company_id"),
            "email": recipient["email"],
            "name": recipient.get("name"),
            "token": secrets.token_hex(16),
            "send_after": send_after,
        }
        for recipient in resolved["recipients"]
    ]
    await campaign_repo.insert_recipients(campaign_id, rows)
    return len(rows)


async def contact_for(recipient: Mapping[str, Any]) -> dict[str, Any]:
    staff_id = recipient.get("staff_id")
    if staff_id:
        contact = await campaign_repo.get_contact(int(staff_id))
        if contact:
            return contact
    return {
        "email": recipient.get("email"),
        "name": recipient.get("name"),
        "company_id": recipient.get("company_id"),
        "company_name": recipient.get("company_name"),
    }


async def _business_hours_delay(
    campaign: Mapping[str, Any],
    recipient: Mapping[str, Any],
    now: datetime,
) -> tuple[bool, datetime | None]:
    """Return ``(may_send_now, next_open)`` for the recipient's business hours."""

    company_id = recipient.get("company_id") if campaign.get("business_hours_source") != "global" else None
    schedule = await business_hours_service.get_schedule(company_id)
    if business_hours_service.is_open(schedule, now):
        return True, None
    return False, business_hours_service.next_open(schedule, now)


async def send_recipient(campaign: Mapping[str, Any], recipient: Mapping[str, Any], now: datetime) -> str:
    recipient_id = int(recipient["id"])
    may_send, next_open = await _business_hours_delay(campaign, recipient, now)
    if not may_send:
        if next_open is None:
            await campaign_repo.skip_recipient(recipient_id, "no_business_hours")
            return "skipped"
        await campaign_repo.defer_recipient(recipient_id, next_open)
        return "deferred"
    email = str(recipient["email"])
    if campaign.get("category") == CATEGORY_SALES and await campaign_repo.list_opted_out([email], CATEGORY_SALES):
        await campaign_repo.skip_recipient(recipient_id, "unsubscribed")
        return "skipped"
    contact = await contact_for(recipient)
    contact["email"] = email
    token = str(recipient["token"])
    try:
        rendered = await render_email(campaign, contact, token=token)
        sent, result = await _dispatch(campaign, to=email, rendered=rendered, token=token)
    except Exception as exc:
        logger.warning("Marketing campaign email failed", campaign_id=campaign.get("id"), recipient_id=recipient_id, error=str(exc))
        await campaign_repo.mark_recipient_failed(recipient_id, str(exc) or exc.__class__.__name__)
        return "failed"
    if not sent:
        await campaign_repo.skip_recipient(recipient_id, "not_delivered")
        return "skipped"
    result = result or {}
    provider = str(result.get("provider") or "smtp")
    await campaign_repo.mark_recipient_sent(
        recipient_id,
        sent_at=datetime.now(timezone.utc),
        subject=rendered["subject"],
        message_id=message_id_for(token, campaign.get("sender_email") or default_sender()),
        smtp2go_message_id=str(result["id"]) if provider == "smtp2go" and result.get("id") else None,
        provider=provider,
    )
    return "sent"


async def process_due_sends(limit: int = SEND_BATCH_LIMIT) -> dict[str, int]:
    """Send queued campaign emails whose recipients are inside business hours."""

    now = datetime.now(timezone.utc)
    counts = {"sent": 0, "deferred": 0, "skipped": 0, "failed": 0}
    try:
        due = await campaign_repo.list_due_recipients(now, limit=limit)
    except Exception as exc:
        logger.warning("Failed to load due marketing campaign recipients", error=str(exc))
        return counts
    campaigns: dict[int, dict[str, Any] | None] = {}
    for recipient in due:
        campaign_id = int(recipient["campaign_id"])
        if campaign_id not in campaigns:
            campaigns[campaign_id] = await campaign_repo.get_campaign(campaign_id)
        campaign = campaigns[campaign_id]
        if not campaign or campaign.get("status") != "sending":
            continue
        if not await campaign_repo.claim_recipient(int(recipient["id"])):
            continue
        try:
            outcome = await send_recipient(campaign, recipient, now)
        except Exception as exc:  # pragma: no cover - keep one bad row from stalling the batch
            logger.warning("Marketing campaign recipient failed", recipient_id=recipient.get("id"), error=str(exc))
            await campaign_repo.mark_recipient_failed(int(recipient["id"]), str(exc) or exc.__class__.__name__)
            outcome = "failed"
        counts[outcome] = counts.get(outcome, 0) + 1
    await campaign_repo.complete_finished_campaigns(datetime.now(timezone.utc))
    return counts


async def cancel_campaign(campaign_id: int) -> None:
    if not await campaign_repo.cancel_campaign(campaign_id, cancelled_at=datetime.now(timezone.utc)):
        raise CampaignError("Only campaigns that are sending can be cancelled.")


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------


async def get_unsubscribe_recipient(token: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        return None
    return await campaign_repo.get_recipient_by_token(token)


async def unsubscribe(token: str) -> dict[str, Any] | None:
    recipient = await get_unsubscribe_recipient(token)
    if not recipient:
        return None
    await campaign_repo.add_opt_out(str(recipient["email"]).lower(), CATEGORY_SALES, recipient.get("campaign_id"))
    return recipient


# ---------------------------------------------------------------------------
# SMTP2Go engagement
# ---------------------------------------------------------------------------


async def record_smtp2go_event(
    smtp2go_message_id: str | None,
    event_type: str,
    occurred_at: datetime,
) -> str | None:
    """Update the campaign recipient for an SMTP2Go webhook event.

    Returns the tracking identifier to store with the event, or ``None`` when
    the message did not come from a campaign.
    """

    if not smtp2go_message_id:
        return None
    recipient = await campaign_repo.get_recipient_by_smtp2go_message_id(str(smtp2go_message_id))
    if not recipient:
        return None
    await campaign_repo.record_engagement(int(recipient["id"]), event_type, occurred_at)
    return f"mkt-{recipient['token']}"


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------


async def match_inbound_reply(
    *,
    subject: str | None,
    from_email: str | None,
    related_message_ids: Sequence[str] | None,
) -> dict[str, Any] | None:
    """Return the campaign recipient an inbound email is replying to, if any."""

    token = token_from_message_ids(related_message_ids)
    if token:
        recipient = await campaign_repo.get_recipient_by_token(token)
        if recipient:
            return recipient
    sender = normalise_email(from_email)
    wanted = normalise_subject(subject)
    if not sender or not wanted:
        return None
    since = datetime.now(timezone.utc) - REPLY_MATCH_WINDOW
    for candidate in await campaign_repo.list_recent_sent_to(sender, since):
        if normalise_subject(candidate.get("subject_rendered")) == wanted:
            return candidate
    return None


async def link_reply(recipient: Mapping[str, Any], ticket_id: int, *, new_ticket: bool) -> None:
    now = datetime.now(timezone.utc)
    recipient_id = int(recipient["id"])
    if not new_ticket:
        await campaign_repo.touch_replied(recipient_id, now)
        return
    await campaign_repo.link_reply_ticket(recipient_id, ticket_id, now)
    campaign = await campaign_repo.get_campaign(int(recipient["campaign_id"]))
    campaign_name = html.escape(str((campaign or {}).get("name") or "a marketing campaign"))
    sent_at = recipient.get("sent_at")
    sent_text = f" on {sent_at:%d %b %Y}" if isinstance(sent_at, datetime) else ""
    note = (
        f"<p>This ticket was created from a reply to the marketing campaign "
        f"<a href=\"/admin/marketing/campaigns/{int(recipient['campaign_id'])}\">{campaign_name}</a>, "
        f"sent to {html.escape(str(recipient.get('email') or ''))}{sent_text}.</p>"
    )
    try:
        from app.repositories import tickets as tickets_repo

        await tickets_repo.create_reply(ticket_id=ticket_id, author_id=None, body=note, is_internal=True)
    except Exception as exc:  # pragma: no cover - the link itself is already stored
        logger.warning("Failed to add marketing campaign note to ticket", ticket_id=ticket_id, error=str(exc))
