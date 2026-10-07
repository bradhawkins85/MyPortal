"""Public client onboarding form backed by single-use magic links.

An admin creates an onboarding link for a prospective client. The link token
is only shown once (the database keeps its SHA-256 hash). The client fills in
their company details, each site's address, primary contact and business
hours, and a billing contact. Submitting the form creates the company with
invoice prepay payment terms due in 7 days, its sites and contacts, and a
support ticket in the ``New Client`` status so the team can follow up.

The company is created pending approval with its contacts disabled. An admin
reviews and edits the details on the company page, then approves it, which
activates the company and enables its contacts.
"""

from __future__ import annotations

import hashlib
import html
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.repositories import billing_contacts as billing_contacts_repo
from app.repositories import business_hours as business_hours_repo
from app.repositories import client_onboarding as onboarding_repo
from app.repositories import company_addresses as company_addresses_repo
from app.repositories import companies as company_repo
from app.repositories import staff as staff_repo
from app.services import business_hours as business_hours_service
from app.services import client_onboarding_questions as questions_service

INVOICE_DUE_DAYS = 7
PAYMENT_METHOD = "invoice_prepay"
NEW_CLIENT_STATUS = "new_client"
DEFAULT_EXPIRY_DAYS = 30
MAX_EXPIRY_DAYS = 90
MAX_SITES = 20

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,64}")
_SITE_INDEX_PATTERN = re.compile(r"\d{1,3}")


class OnboardingUnavailable(Exception):
    """The link is unknown, expired, revoked or already used."""


@dataclass
class Contact:
    first_name: str
    last_name: str
    email: str
    phone: str | None

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class Site:
    name: str
    street: str
    city: str | None
    state: str | None
    postcode: str | None
    country: str | None
    phone: str | None
    contact: Contact
    timezone_name: str
    weekly_hours: dict[str, list[dict[str, str]]]
    custom_answers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Submission:
    client_name: str
    phone: str | None
    email: str | None
    website: str | None
    notes: str | None
    billing_contact: Contact
    sites: list[Site] = field(default_factory=list)
    custom_answers: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        def contact(value: Contact) -> dict[str, Any]:
            return {
                "first_name": value.first_name,
                "last_name": value.last_name,
                "email": value.email,
                "phone": value.phone,
            }

        return {
            "client_name": self.client_name,
            "phone": self.phone,
            "email": self.email,
            "website": self.website,
            "notes": self.notes,
            "billing_contact": contact(self.billing_contact),
            "custom_answers": self.custom_answers,
            "sites": [
                {
                    "name": site.name,
                    "street": site.street,
                    "city": site.city,
                    "state": site.state,
                    "postcode": site.postcode,
                    "country": site.country,
                    "phone": site.phone,
                    "primary_contact": contact(site.contact),
                    "timezone": site.timezone_name,
                    "weekly_hours": site.weekly_hours,
                    "custom_answers": site.custom_answers,
                }
                for site in self.sites
            ],
        }


# ---------------------------------------------------------------------------
# Tokens and links
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def onboarding_url(token: str) -> str:
    base = str(get_settings().portal_url or "").rstrip("/")
    return f"{base}/onboarding/{token}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _clamp_expiry_days(value: Any) -> int:
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY_DAYS
    return min(max(days, 1), MAX_EXPIRY_DAYS)


def effective_status(record: Mapping[str, Any], now: datetime | None = None) -> str:
    status = str(record.get("status") or "pending")
    expires_at = record.get("expires_at")
    if status == "pending" and isinstance(expires_at, datetime):
        if expires_at.replace(tzinfo=None) <= (now or _utcnow()):
            return "expired"
    return status


MAX_INVITE_MESSAGE = 2000


def validate_link_details(
    *, client_name: str | None, contact_name: str | None, recipient_email: str | None
) -> str | None:
    """Return an error for the New onboarding link form, or ``None`` when valid."""

    if not (client_name or "").strip() and not (contact_name or "").strip():
        return "Enter the client's company name or contact name so we know who the link is for."
    raw_email = (recipient_email or "").strip()
    if raw_email and not normalise_email(raw_email):
        return "Enter a valid email address for the client."
    return None


async def create_link(
    *,
    client_name: str | None,
    recipient_email: str | None,
    expiry_days: Any,
    created_by_user_id: int | None,
    contact_name: str | None = None,
    invite_message: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Create an onboarding link and return the record and its one-time token."""

    token = generate_token()
    record = await onboarding_repo.create(
        token_hash=hash_token(token),
        client_name=(client_name or "").strip()[:255] or None,
        contact_name=(contact_name or "").strip()[:255] or None,
        recipient_email=normalise_email(recipient_email),
        invite_message=(invite_message or "").strip()[:MAX_INVITE_MESSAGE] or None,
        expires_at=_utcnow() + timedelta(days=_clamp_expiry_days(expiry_days)),
        created_by_user_id=created_by_user_id,
    )
    return record, token


async def regenerate_link(onboarding_id: int, expiry_days: Any) -> str | None:
    """Replace a pending link's token (the old link stops working)."""

    token = generate_token()
    rotated = await onboarding_repo.rotate_token(
        onboarding_id,
        token_hash=hash_token(token),
        expires_at=_utcnow() + timedelta(days=_clamp_expiry_days(expiry_days)),
    )
    return token if rotated else None


async def get_open_onboarding(token: str | None) -> dict[str, Any]:
    """Return the pending onboarding for ``token`` or raise :class:`OnboardingUnavailable`."""

    if not token or not _TOKEN_PATTERN.fullmatch(token):
        raise OnboardingUnavailable("invalid")
    record = await onboarding_repo.get_by_token_hash(hash_token(token))
    if not record:
        raise OnboardingUnavailable("invalid")
    status = effective_status(record)
    if status != "pending":
        raise OnboardingUnavailable(status)
    return record


INVITATION_TEMPLATE_SLUG = "client-onboarding-invitation"
INVITATION_SUBJECT_TEMPLATE_SLUG = "client-onboarding-invitation-subject"

DEFAULT_INVITATION_SUBJECT = "Welcome to {{ app.name }} - let's get {{ recipient.greeting_name }} set up"
DEFAULT_INVITATION = (
    "<p>Hi {{ recipient.greeting_name }},</p>"
    "<p>Welcome to {{ app.name }}! To set up support for {{ company.name }}, please tell us about "
    "your business, your sites and who should receive invoices. It takes about five minutes.</p>"
    "{{ onboarding.message }}"
    '<p><a href="{{ onboarding.link }}">Complete your onboarding form</a></p>'
    "<p>This link is unique to you and works until {{ onboarding.expires }}. "
    "It can only be submitted once.</p>"
    "<p>Kind regards,<br>{{ sender.name }}<br>{{ app.name }}</p>"
)

# Stands in for the personal message while the template is rendered, so the
# message can be inserted as escaped paragraphs afterwards.
_MESSAGE_PLACEHOLDER = "\u0000client-onboarding-message\u0000"


def _person_name(person: Mapping[str, Any] | None) -> str:
    if not person:
        return ""
    full = " ".join(
        str(person.get(key) or "").strip() for key in ("first_name", "last_name")
    ).strip()
    return full or str(person.get("display_name") or person.get("name") or person.get("email") or "").strip()


def _format_expiry(expires_at: Any) -> str:
    if not isinstance(expires_at, datetime):
        return ""
    aware = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
    local = aware.astimezone(business_hours_service.resolve_zone(None))
    return f"{local.day} {local:%B %Y}"


def invitation_context(
    record: Mapping[str, Any], token: str, sender: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Variables available to the invitation subject and body templates."""

    settings = get_settings()
    contact_name = str(record.get("contact_name") or "").strip()
    company_name = str(record.get("client_name") or "").strip()
    contact_first = contact_name.split()[0] if contact_name else ""
    expires_at = record.get("expires_at")
    days_left = ""
    if isinstance(expires_at, datetime):
        remaining = expires_at.replace(tzinfo=None) - _utcnow()
        days_left = str(max(0, remaining.days + (1 if remaining.seconds else 0)))
    portal_url = str(settings.portal_url or "").rstrip("/")
    return {
        "app": {"name": settings.app_name or "MyPortal"},
        "portal": {"url": portal_url},
        "recipient": {"greeting_name": contact_first or company_name or "there"},
        "contact": {
            "name": contact_name,
            "first_name": contact_first,
            "email": str(record.get("recipient_email") or ""),
        },
        "company": {"name": company_name or "your business"},
        "onboarding": {
            "link": onboarding_url(token),
            "expires": _format_expiry(expires_at),
            "expires_days": days_left,
            "message": _MESSAGE_PLACEHOLDER,
        },
        "sender": {
            "name": _person_name(sender) or (settings.app_name or "MyPortal"),
            "email": str((sender or {}).get("email") or ""),
        },
    }


def _message_html(message: str) -> str:
    paragraphs = [part.strip() for part in message.replace("\r\n", "\n").split("\n\n") if part.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in part.split("\n")) + "</p>"
        for part in paragraphs
    )


class _TextExtractor(HTMLParser):
    """Plain-text version of a simple HTML email (paragraphs, line breaks, links, lists)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "a":
            self._href = dict(attrs).get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.parts.append(f": {self._href}")
            self._href = None
        elif tag in {"p", "div", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(markup: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    lines = [line.strip() for line in "".join(extractor.parts).split("\n")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


async def render_invitation(
    record: Mapping[str, Any], token: str, sender: Mapping[str, Any] | None = None
) -> tuple[str, str, str]:
    """Render the invitation email as (subject, html, text) from its message templates."""

    from app.services import message_templates as message_templates_service

    context = invitation_context(record, token, sender)
    message = str(record.get("invite_message") or "").strip()

    subject, _ = await message_templates_service.render_template_content(
        INVITATION_SUBJECT_TEMPLATE_SLUG,
        {**context, "onboarding": {**context["onboarding"], "message": message}},
        default_content=DEFAULT_INVITATION_SUBJECT,
        default_content_type="text/plain",
    )
    subject = " ".join(subject.split())[:255] or "Complete your client onboarding"

    body, content_type = await message_templates_service.render_template_content(
        INVITATION_TEMPLATE_SLUG,
        context,
        default_content=DEFAULT_INVITATION,
        default_content_type="text/html",
    )
    if content_type == "text/html":
        html_body = body.replace(_MESSAGE_PLACEHOLDER, _message_html(message) if message else "")
        return subject, html_body, _html_to_text(html_body)
    text_body = body.replace(_MESSAGE_PLACEHOLDER, message)
    paragraphs = [part for part in text_body.split("\n\n")]
    html_body = "".join(
        "<p>" + "<br>".join(html.escape(line) for line in part.split("\n")) + "</p>" for part in paragraphs if part.strip()
    )
    return subject, html_body, text_body


async def send_link_email(
    record: Mapping[str, Any], token: str, sender: Mapping[str, Any] | None = None
) -> bool:
    from app.services import email as email_service

    recipient = record.get("recipient_email")
    if not recipient:
        return False
    try:
        subject, html_body, text_body = await render_invitation(record, token, sender)
        sent, _ = await email_service.send_email(
            subject=subject,
            recipients=[str(recipient)],
            html_body=html_body,
            text_body=text_body,
            reply_to=str((sender or {}).get("email") or "") or None,
        )
    except Exception as exc:  # pragma: no cover - mail transport failures
        logger.warning("Failed to send client onboarding email: {}", exc)
        return False
    return bool(sent)


# ---------------------------------------------------------------------------
# Form parsing
# ---------------------------------------------------------------------------


def normalise_email(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text or len(text) > 255 or any(char.isspace() for char in text):
        return None
    local, sep, domain = text.partition("@")
    if not sep or not local or "@" in domain:
        return None
    labels = domain.split(".")
    if len(labels) < 2 or not all(labels):
        return None
    return text


def _text(form: Mapping[str, Any], key: str, limit: int) -> str | None:
    value = str(form.get(key) or "").strip()
    return value[:limit] or None


def _getlist(form: Any, key: str) -> list[str]:
    if hasattr(form, "getlist"):
        return [str(value) for value in form.getlist(key)]
    value = form.get(key)
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else [str(value)]


def site_indexes(form: Any) -> list[str]:
    """The ``site_index`` values in submission order, de-duplicated."""

    indexes: list[str] = []
    for value in _getlist(form, "site_index"):
        value = value.strip()
        if _SITE_INDEX_PATTERN.fullmatch(value) and value not in indexes:
            indexes.append(value)
    return indexes


def _site_hours_form(form: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    stripped: dict[str, Any] = {}
    for key, _label in business_hours_service.WEEKDAYS:
        for suffix in ("open", "start", "end", "start2", "end2"):
            name = f"day_{key}_{suffix}"
            value = form.get(f"{prefix}{name}")
            if value is not None:
                stripped[name] = value
    return stripped


def _parse_contact(
    form: Mapping[str, Any], prefix: str, label: str, errors: list[str]
) -> Contact | None:
    first_name = _text(form, f"{prefix}first_name", 100)
    last_name = _text(form, f"{prefix}last_name", 100)
    raw_email = str(form.get(f"{prefix}email") or "").strip()
    email = normalise_email(raw_email)
    phone = _text(form, f"{prefix}phone", 50)
    missing = not first_name or not last_name or not raw_email
    if missing:
        errors.append(f"{label}: enter a first name, last name and email address.")
    elif not email:
        errors.append(f"{label}: enter a valid email address.")
    if missing or not email:
        return None
    return Contact(first_name=first_name, last_name=last_name, email=email, phone=phone)


def parse_submission(
    form: Any, questions: list[dict[str, Any]] | None = None
) -> tuple[Submission | None, list[str]]:
    """Validate the public form. Returns the submission or a list of errors."""

    errors: list[str] = []
    custom_answers, custom_errors = questions_service.parse_answers(
        form, questions or [], sections=("business", "sites", "billing", "review")
    )
    errors.extend(custom_errors)
    client_name = _text(form, "client_name", 255)
    if not client_name:
        errors.append("Enter your business name.")
    company_phone = _text(form, "company_phone", 50)
    if not company_phone:
        errors.append("Enter your main phone number.")
    raw_company_email = str(form.get("company_email") or "").strip()
    company_email = normalise_email(raw_company_email)
    if not raw_company_email:
        errors.append("Enter your general email address.")
    elif not company_email:
        errors.append("Enter a valid general email address.")

    indexes = site_indexes(form)
    if not indexes:
        errors.append("Add at least one site.")
    if len(indexes) > MAX_SITES:
        errors.append(f"Add no more than {MAX_SITES} sites.")
        indexes = indexes[:MAX_SITES]

    sites: list[Site] = []
    seen_names: set[str] = set()
    for position, index in enumerate(indexes, start=1):
        prefix = f"site-{index}-"
        name = _text(form, f"{prefix}name", 100)
        label = f"Site {position}" + (f" ({name})" if name else "")
        site_answers, site_errors = questions_service.parse_answers(
            form,
            questions or [],
            sections=("site_address", "site_contact", "site_hours"),
            prefix=prefix,
        )
        errors.extend(f"{label} {error}" for error in site_errors)
        street = _text(form, f"{prefix}street", 255)
        if not name:
            errors.append(f"Site {position}: enter a site name.")
        elif name.lower() in seen_names:
            errors.append(f"{label}: each site needs a different name.")
        else:
            seen_names.add(name.lower())
        if not street:
            errors.append(f"{label}: enter the street address.")
        contact = _parse_contact(form, f"{prefix}contact_", f"{label} primary contact", errors)
        timezone_name = str(form.get(f"{prefix}timezone") or "").strip()
        if not business_hours_service.is_valid_timezone(timezone_name):
            errors.append(f"{label}: select a valid time zone.")
        weekly, hours_error = business_hours_service.parse_weekly_form(
            _site_hours_form(form, prefix)
        )
        if hours_error:
            errors.append(f"{label} business hours: {hours_error}")
        if name and street and contact and not hours_error:
            sites.append(
                Site(
                    name=name,
                    street=street,
                    city=_text(form, f"{prefix}city", 100),
                    state=_text(form, f"{prefix}state", 100),
                    postcode=_text(form, f"{prefix}postcode", 20),
                    country=_text(form, f"{prefix}country", 100),
                    phone=_text(form, f"{prefix}phone", 50),
                    contact=contact,
                    timezone_name=timezone_name,
                    weekly_hours=weekly,
                    custom_answers=site_answers,
                )
            )

    if form.get("billing_same_as_primary") and sites:
        billing = sites[0].contact
    else:
        billing = _parse_contact(form, "billing_", "Billing contact", errors)

    if not form.get("confirm_details"):
        errors.append("Confirm that your details are correct before sending them.")

    if errors or not client_name or billing is None:
        return None, errors
    return (
        Submission(
            client_name=client_name,
            phone=company_phone,
            email=company_email,
            website=_text(form, "website", 255),
            notes=_text(form, "notes", 4000),
            billing_contact=billing,
            sites=sites,
            custom_answers=custom_answers,
        ),
        [],
    )


def form_state(
    form: Any | None,
    *,
    client_name: str | None = None,
    contact_name: str | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Values for (re-)rendering the public form, including per-site hour rows.

    A fresh form greets the contact by first name and pre-fills them as the
    main site's primary contact.
    """

    default_tz = business_hours_service.default_timezone_name()
    if form is None:
        first_site = _blank_site("0", default_tz)
        contact_parts = (contact_name or "").split(maxsplit=1)
        if contact_parts:
            first_site["values"]["contact_first_name"] = contact_parts[0]
            first_site["values"]["contact_last_name"] = contact_parts[1] if len(contact_parts) > 1 else ""
        return {
            "client_name": client_name or "",
            "greeting_name": contact_parts[0] if contact_parts else (client_name or ""),
            "sites": [first_site],
            "values": {},
            "custom_answers": {},
            "billing_same_as_primary": True,
            "confirm_details": False,
        }
    sites = []
    for index in site_indexes(form)[:MAX_SITES] or ["0"]:
        prefix = f"site-{index}-"
        values = {
            key: str(form.get(f"{prefix}{key}") or "")
            for key in (
                "name", "street", "city", "state", "postcode", "country", "phone",
                "contact_first_name", "contact_last_name", "contact_email", "contact_phone",
                "timezone",
            )
        }
        sites.append(
            {
                "index": index,
                "values": values,
                "hours": _hours_rows_from_form(form, prefix),
                "custom_answers": questions_service.answer_state(
                    form, questions or [], prefix=prefix
                ),
            }
        )
    return {
        "client_name": str(form.get("client_name") or ""),
        "custom_answers": questions_service.answer_state(form, questions or []),
        "sites": sites,
        "values": {
            key: str(form.get(key) or "")
            for key in (
                "company_phone", "company_email", "website", "notes",
                "billing_first_name", "billing_last_name", "billing_email", "billing_phone",
            )
        },
        "billing_same_as_primary": bool(form.get("billing_same_as_primary")),
        "confirm_details": bool(form.get("confirm_details")),
    }


def _blank_site(index: str, timezone_name: str) -> dict[str, Any]:
    return {
        "index": index,
        "values": {"timezone": timezone_name},
        "hours": business_hours_service.weekly_form_rows(None),
        "custom_answers": {},
    }


def _hours_rows_from_form(form: Mapping[str, Any], prefix: str) -> list[dict[str, Any]]:
    rows = []
    for key, label in business_hours_service.WEEKDAYS:
        rows.append(
            {
                "key": key,
                "label": label,
                "open": bool(form.get(f"{prefix}day_{key}_open")),
                "start": str(form.get(f"{prefix}day_{key}_start") or ""),
                "end": str(form.get(f"{prefix}day_{key}_end") or ""),
                "start2": str(form.get(f"{prefix}day_{key}_start2") or ""),
                "end2": str(form.get(f"{prefix}day_{key}_end2") or ""),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Completing an onboarding
# ---------------------------------------------------------------------------


class DuplicateCompany(Exception):
    pass


def _format_address(site: Site) -> str:
    locality = " ".join(part for part in (site.city, site.state, site.postcode) if part)
    return ", ".join(part for part in (site.street, locality, site.country) if part)


async def _create_contact(
    company_id: int, contact: Contact, created: dict[str, int], *, site: Site | None = None
) -> int:
    existing = created.get(contact.email)
    if existing:
        return existing
    record = await staff_repo.create_staff(
        company_id=company_id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        email=contact.email,
        mobile_phone=contact.phone,
        street=site.street if site else None,
        city=site.city if site else None,
        state=site.state if site else None,
        postcode=site.postcode if site else None,
        country=site.country if site else None,
        enabled=False,
        source="client_onboarding",
        onboarding_status="approved",
        approval_status="approved",
        approved_at=datetime.now(timezone.utc),
    )
    staff_id = int(record["id"])
    created[contact.email] = staff_id
    return staff_id


def _hours_summary(weekly: Mapping[str, Any]) -> str:
    parts = []
    for key, label in business_hours_service.WEEKDAYS:
        windows = weekly.get(key) or []
        text = ", ".join(f"{w['start']}–{w['end']}" for w in windows) if windows else "Closed"
        parts.append(f"{label[:3]} {text}")
    return "; ".join(parts)


def ticket_description(submission: Submission, *, company_id: int | None = None) -> str:
    e = html.escape

    def contact_line(contact: Contact) -> str:
        phone = f", {e(contact.phone)}" if contact.phone else ""
        return f"{e(contact.display_name)} &lt;{e(contact.email)}&gt;{phone}"

    def answer_lines(answers: list[dict[str, Any]]) -> str:
        return "".join(
            f"<li>{e(questions_service.SECTIONS.get(answer['section'], answer['section']))} — "
            f"{e(answer['label'])}: {e(answer['display_value']).replace(chr(10), '<br>')}</li>"
            for answer in answers
        )

    lines = [
        f"<p><strong>{e(submission.client_name)}</strong> has completed the client onboarding form.</p>",
        "<ul>",
        f"<li>Phone: {e(submission.phone or '—')}</li>",
        f"<li>Email: {e(submission.email or '—')}</li>",
        f"<li>Website: {e(submission.website or '—')}</li>",
        f"<li>Billing contact: {contact_line(submission.billing_contact)}</li>",
        f"<li>Payment terms: Invoice prepay, due in {INVOICE_DUE_DAYS} days</li>",
        "</ul>",
        "<p><strong>Sites</strong></p>",
        "<ul>",
    ]
    for site in submission.sites:
        phone = f" ({e(site.phone)})" if site.phone else ""
        lines.append(
            f"<li><strong>{e(site.name)}</strong>{phone}: {e(_format_address(site))}<br>"
            f"Primary contact: {contact_line(site.contact)}<br>"
            f"Business hours ({e(site.timezone_name)}): {e(_hours_summary(site.weekly_hours))}</li>"
        )
        if site.custom_answers:
            lines.append(
                f"<li>Custom questions for {e(site.name)}"
                f"<ul>{answer_lines(site.custom_answers)}</ul></li>"
            )
    lines.append("</ul>")
    if submission.custom_answers:
        lines.append(
            "<p><strong>Custom onboarding questions</strong></p><ul>"
            + answer_lines(submission.custom_answers)
            + "</ul>"
        )
    if submission.notes:
        lines.append(f"<p><strong>Notes from the client</strong></p><p>{e(submission.notes)}</p>")
    review = (
        f'<a href="/admin/companies/{int(company_id)}/edit">company page</a>'
        if company_id is not None
        else "company page"
    )
    lines.append(
        "<p>The company is <strong>pending approval</strong> and its contacts are disabled. "
        f"Check and edit the details on the {review}, then approve it to activate the company. "
        "Use this ticket to send the client their onboarding information and track setup.</p>"
    )
    return "".join(lines)


async def complete_onboarding(record: Mapping[str, Any], submission: Submission) -> dict[str, Any]:
    """Create the company, sites, contacts and New Client ticket for a submission.

    Raises :class:`DuplicateCompany` when the business name is already in use
    and :class:`OnboardingUnavailable` when the link was submitted concurrently.
    """

    from app.services import tickets as tickets_service

    if await company_repo.get_company_by_name(submission.client_name):
        raise DuplicateCompany(submission.client_name)
    onboarding_id = int(record["id"])
    if not await onboarding_repo.claim_for_processing(onboarding_id, submission.to_json()):
        raise OnboardingUnavailable("submitted")

    company_id: int | None = None
    try:
        primary_site = submission.sites[0]
        company = await company_repo.create_company(
            name=submission.client_name,
            phone=submission.phone,
            address=_format_address(primary_site),
            invoice_due_days=INVOICE_DUE_DAYS,
            payment_method=PAYMENT_METHOD,
            pending_approval=1,
        )
        company_id = int(company["id"])

        contacts: dict[str, int] = {}
        for site in submission.sites:
            address = await company_addresses_repo.create(
                company_id,
                label=site.name,
                street=site.street,
                city=site.city,
                state=site.state,
                postcode=site.postcode,
                country=site.country,
            )
            contact_id = await _create_contact(company_id, site.contact, contacts, site=site)
            await onboarding_repo.save_site_profile(
                address_id=int(address["id"]),
                company_id=company_id,
                phone=site.phone,
                primary_contact_staff_id=contact_id,
                timezone_name=site.timezone_name,
                weekly_hours=site.weekly_hours,
            )

        billing_id = await _create_contact(company_id, submission.billing_contact, contacts)
        await billing_contacts_repo.add_billing_contact(company_id, billing_id)

        # The company schedule drives SLAs and automations; it follows the primary site.
        await business_hours_repo.save_schedule(
            company_id,
            timezone_name=primary_site.timezone_name,
            weekly_hours=primary_site.weekly_hours,
        )
        business_hours_service.invalidate_cache(company_id)

        await onboarding_repo.ensure_new_client_status()
        requester_id = contacts[primary_site.contact.email]
        ticket = await tickets_service.create_ticket(
            subject=f"New client onboarding: {submission.client_name}",
            description=ticket_description(submission, company_id=company_id),
            requester_id=None,
            requester_staff_id=requester_id,
            requester_email=primary_site.contact.email,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status=NEW_CLIENT_STATUS,
            category=None,
            module_slug=None,
            external_reference=None,
            send_creation_notification=False,
        )
        ticket_id = int(ticket["id"]) if ticket and ticket.get("id") is not None else None
        await onboarding_repo.mark_submitted(
            onboarding_id,
            company_id=company_id,
            ticket_id=ticket_id,
            submitted_at=_utcnow(),
        )
    except Exception as exc:
        logger.exception("Client onboarding {} failed", onboarding_id)
        await onboarding_repo.mark_failed(
            onboarding_id, error_message=str(exc) or exc.__class__.__name__, company_id=company_id
        )
        raise
    return {"company_id": company_id, "ticket_id": ticket_id}


async def approve_company(company_id: int, *, approved_by_user_id: int) -> bool:
    """Activate a pending company and enable the contacts created by the form.

    Returns ``False`` when the company was not pending approval.
    """

    if not await onboarding_repo.approve_company(company_id):
        return False
    await onboarding_repo.enable_onboarding_contacts(company_id)
    await onboarding_repo.mark_approved(
        company_id, approved_by_user_id=approved_by_user_id, approved_at=_utcnow()
    )
    return True
