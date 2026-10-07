"""Tests for the public client onboarding form."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import FormData

from app.core.database import db
from app.features.client_onboarding import PACK
from app.repositories import billing_contacts as billing_contacts_repo
from app.repositories import business_hours as business_hours_repo
from app.repositories import client_onboarding as onboarding_repo
from app.repositories import company_addresses as company_addresses_repo
from app.repositories import companies as company_repo
from app.repositories import staff as staff_repo
from app.services import client_onboarding as onboarding


def _site_fields(index: str, **overrides: str) -> list[tuple[str, str]]:
    values = {
        "name": "Head office",
        "street": "1 Main St",
        "city": "Brisbane",
        "state": "QLD",
        "postcode": "4000",
        "country": "Australia",
        "phone": "07 3000 0000",
        "contact_first_name": "Jo",
        "contact_last_name": "Bloggs",
        "contact_email": "Jo@Acme.com.au",
        "contact_phone": "0400 000 000",
        "timezone": "Australia/Brisbane",
        "day_mon_open": "1",
        "day_mon_start": "08:00",
        "day_mon_end": "17:00",
        "day_tue_open": "1",
        "day_tue_start": "08:00",
        "day_tue_end": "12:00",
        "day_tue_start2": "13:00",
        "day_tue_end2": "17:00",
    }
    values.update(overrides)
    return [("site_index", index)] + [(f"site-{index}-{key}", value) for key, value in values.items()]


def _form(*sites: list[tuple[str, str]], **overrides: str) -> FormData:
    fields = {
        "client_name": "Acme Pty Ltd",
        "company_phone": "07 3000 0001",
        "company_email": "hello@acme.com.au",
        "billing_first_name": "Sam",
        "billing_last_name": "Smith",
        "billing_email": "accounts@acme.com.au",
        "billing_phone": "",
        "confirm_details": "1",
    }
    fields.update(overrides)
    items = list(fields.items())
    for site in sites or (_site_fields("0"),):
        items.extend(site)
    return FormData(items)


def test_pack_registers_admin_and_public_routes():
    routes = {
        (method, route.path)
        for router in PACK.routers
        for route in router.routes
        for method in route.methods
    }
    assert ("GET", "/onboarding/{token}") in routes
    assert ("POST", "/onboarding/{token}") in routes
    assert ("GET", "/admin/client-onboarding") in routes
    assert ("POST", "/admin/client-onboarding") in routes
    assert ("POST", "/admin/client-onboarding/{onboarding_id}/revoke") in routes
    assert ("POST", "/admin/client-onboarding/{onboarding_id}/regenerate") in routes


def test_parse_submission_collects_sites_contacts_and_hours():
    submission, errors = onboarding.parse_submission(
        _form(_site_fields("0"), _site_fields("3", name="Warehouse", contact_email="wh@acme.com.au"))
    )
    assert errors == []
    assert submission.client_name == "Acme Pty Ltd"
    assert [site.name for site in submission.sites] == ["Head office", "Warehouse"]
    head_office = submission.sites[0]
    assert head_office.contact.email == "jo@acme.com.au"
    assert head_office.timezone_name == "Australia/Brisbane"
    assert head_office.weekly_hours["mon"] == [{"start": "08:00", "end": "17:00"}]
    assert head_office.weekly_hours["tue"] == [
        {"start": "08:00", "end": "12:00"},
        {"start": "13:00", "end": "17:00"},
    ]
    assert head_office.weekly_hours["sat"] == []
    assert submission.billing_contact.email == "accounts@acme.com.au"


def test_billing_can_reuse_first_site_contact():
    submission, errors = onboarding.parse_submission(
        _form(billing_same_as_primary="1", billing_first_name="", billing_email="")
    )
    assert errors == []
    assert submission.billing_contact is submission.sites[0].contact


def test_parse_submission_reports_every_problem():
    submission, errors = onboarding.parse_submission(
        _form(
            _site_fields("0", contact_email="not-an-email", timezone="Mars/Base"),
            _site_fields("1", name="head office", street="", day_mon_end="07:00"),
            client_name="",
            billing_email="",
        )
    )
    assert submission is None
    text = " ".join(errors)
    assert "business name" in text
    assert "Site 1 (Head office) primary contact: enter a valid email address." in errors
    assert "Site 1 (Head office): select a valid time zone." in errors
    assert "each site needs a different name" in text
    assert "enter the street address" in text
    assert "Monday: enter a start time before the end time." in text
    assert "Billing contact: enter a first name, last name and email address." in errors


def test_parse_submission_requires_a_site():
    submission, errors = onboarding.parse_submission(
        FormData([("client_name", "Acme"), ("billing_same_as_primary", "1")])
    )
    assert submission is None
    assert "Add at least one site." in errors


def test_form_state_round_trips_entered_values():
    state = onboarding.form_state(_form(_site_fields("4", name="Depot")))
    assert state["client_name"] == "Acme Pty Ltd"
    assert state["sites"][0]["index"] == "4"
    assert state["sites"][0]["values"]["name"] == "Depot"
    tuesday = state["sites"][0]["hours"][1]
    assert tuesday["open"] and tuesday["start2"] == "13:00"


@pytest.mark.anyio
async def test_open_onboarding_rejects_bad_expired_and_used_links(monkeypatch):
    with pytest.raises(onboarding.OnboardingUnavailable):
        await onboarding.get_open_onboarding("short")

    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(onboarding_repo, "get_by_token_hash", lookup)
    token = "a" * 43
    with pytest.raises(onboarding.OnboardingUnavailable, match="invalid"):
        await onboarding.get_open_onboarding(token)
    lookup.assert_awaited_once_with(onboarding.hash_token(token))

    past = datetime.utcnow() - timedelta(minutes=1)
    lookup.return_value = {"id": 1, "status": "pending", "expires_at": past}
    with pytest.raises(onboarding.OnboardingUnavailable, match="expired"):
        await onboarding.get_open_onboarding(token)

    future = datetime.utcnow() + timedelta(days=1)
    lookup.return_value = {"id": 1, "status": "submitted", "expires_at": future}
    with pytest.raises(onboarding.OnboardingUnavailable, match="submitted"):
        await onboarding.get_open_onboarding(token)

    lookup.return_value = {"id": 1, "status": "pending", "expires_at": future}
    assert (await onboarding.get_open_onboarding(token))["id"] == 1


@pytest.mark.anyio
async def test_create_link_stores_only_the_token_hash(monkeypatch):
    create = AsyncMock(return_value={"id": 5})
    monkeypatch.setattr(onboarding_repo, "create", create)
    record, token = await onboarding.create_link(
        client_name=" Acme ",
        contact_name=" Jo Bloggs ",
        recipient_email="Jo@Acme.com.au",
        invite_message="  Great to chat today!  ",
        expiry_days="500",
        created_by_user_id=2,
    )
    kwargs = create.await_args.kwargs
    assert kwargs["token_hash"] == onboarding.hash_token(token)
    assert token not in kwargs.values()
    assert kwargs["client_name"] == "Acme"
    assert kwargs["contact_name"] == "Jo Bloggs"
    assert kwargs["invite_message"] == "Great to chat today!"
    assert kwargs["recipient_email"] == "jo@acme.com.au"
    assert kwargs["expires_at"] - datetime.utcnow() <= timedelta(days=onboarding.MAX_EXPIRY_DAYS)


def _patch_completion(monkeypatch):
    calls: dict[str, AsyncMock] = {
        "get_company_by_name": AsyncMock(return_value=None),
        "claim": AsyncMock(return_value=True),
        "create_company": AsyncMock(return_value={"id": 42}),
        "create_address": AsyncMock(side_effect=[{"id": 100}, {"id": 101}]),
        "create_staff": AsyncMock(side_effect=[{"id": 7}, {"id": 8}, {"id": 9}]),
        "save_site_profile": AsyncMock(),
        "add_billing_contact": AsyncMock(),
        "save_schedule": AsyncMock(),
        "ensure_status": AsyncMock(),
        "create_ticket": AsyncMock(return_value={"id": 555}),
        "mark_submitted": AsyncMock(),
        "mark_failed": AsyncMock(),
    }
    from app.services import tickets as tickets_service

    monkeypatch.setattr(company_repo, "get_company_by_name", calls["get_company_by_name"])
    monkeypatch.setattr(onboarding_repo, "claim_for_processing", calls["claim"])
    monkeypatch.setattr(company_repo, "create_company", calls["create_company"])
    monkeypatch.setattr(company_addresses_repo, "create", calls["create_address"])
    monkeypatch.setattr(staff_repo, "create_staff", calls["create_staff"])
    monkeypatch.setattr(onboarding_repo, "save_site_profile", calls["save_site_profile"])
    monkeypatch.setattr(billing_contacts_repo, "add_billing_contact", calls["add_billing_contact"])
    monkeypatch.setattr(business_hours_repo, "save_schedule", calls["save_schedule"])
    monkeypatch.setattr(onboarding_repo, "ensure_new_client_status", calls["ensure_status"])
    monkeypatch.setattr(tickets_service, "create_ticket", calls["create_ticket"])
    monkeypatch.setattr(onboarding_repo, "mark_submitted", calls["mark_submitted"])
    monkeypatch.setattr(onboarding_repo, "mark_failed", calls["mark_failed"])
    return calls


@pytest.mark.anyio
async def test_complete_onboarding_creates_company_sites_contacts_and_ticket(monkeypatch):
    calls = _patch_completion(monkeypatch)
    submission, _ = onboarding.parse_submission(
        _form(_site_fields("0"), _site_fields("1", name="Warehouse", contact_email="wh@acme.com.au"))
    )

    result = await onboarding.complete_onboarding({"id": 3}, submission)

    assert result == {"company_id": 42, "ticket_id": 555}
    company = calls["create_company"].await_args.kwargs
    assert company["name"] == "Acme Pty Ltd"
    assert company["invoice_due_days"] == 7
    assert company["payment_method"] == "invoice_prepay"
    assert company["pending_approval"] == 1
    assert all(c.kwargs["enabled"] is False for c in calls["create_staff"].await_args_list)
    assert company["address"] == "1 Main St, Brisbane QLD 4000, Australia"
    assert [c.kwargs["label"] for c in calls["create_address"].await_args_list] == ["Head office", "Warehouse"]
    emails = [c.kwargs["email"] for c in calls["create_staff"].await_args_list]
    assert emails == ["jo@acme.com.au", "wh@acme.com.au", "accounts@acme.com.au"]
    profiles = [c.kwargs for c in calls["save_site_profile"].await_args_list]
    assert [(p["address_id"], p["primary_contact_staff_id"]) for p in profiles] == [(100, 7), (101, 8)]
    calls["add_billing_contact"].assert_awaited_once_with(42, 9)
    schedule = calls["save_schedule"].await_args
    assert schedule.args == (42,)
    assert schedule.kwargs["timezone_name"] == "Australia/Brisbane"
    ticket = calls["create_ticket"].await_args.kwargs
    assert ticket["status"] == "new_client"
    assert ticket["company_id"] == 42
    assert ticket["requester_staff_id"] == 7
    assert "Acme Pty Ltd" in ticket["subject"]
    assert "Warehouse" in ticket["description"]
    assert "pending approval" in ticket["description"]
    assert "/admin/companies/42/edit" in ticket["description"]
    calls["ensure_status"].assert_awaited_once()
    calls["mark_submitted"].assert_awaited_once()
    assert calls["mark_submitted"].await_args.kwargs["ticket_id"] == 555
    calls["mark_failed"].assert_not_called()


@pytest.mark.anyio
async def test_shared_contact_is_created_once(monkeypatch):
    calls = _patch_completion(monkeypatch)
    submission, _ = onboarding.parse_submission(_form(billing_same_as_primary="1"))
    await onboarding.complete_onboarding({"id": 3}, submission)
    assert calls["create_staff"].await_count == 1
    calls["add_billing_contact"].assert_awaited_once_with(42, 7)


@pytest.mark.anyio
async def test_duplicate_company_name_is_rejected_before_claiming(monkeypatch):
    calls = _patch_completion(monkeypatch)
    calls["get_company_by_name"].return_value = {"id": 1}
    submission, _ = onboarding.parse_submission(_form())
    with pytest.raises(onboarding.DuplicateCompany):
        await onboarding.complete_onboarding({"id": 3}, submission)
    calls["claim"].assert_not_called()
    calls["create_company"].assert_not_called()


@pytest.mark.anyio
async def test_concurrent_submission_does_not_create_twice(monkeypatch):
    calls = _patch_completion(monkeypatch)
    calls["claim"].return_value = False
    submission, _ = onboarding.parse_submission(_form())
    with pytest.raises(onboarding.OnboardingUnavailable):
        await onboarding.complete_onboarding({"id": 3}, submission)
    calls["create_company"].assert_not_called()


@pytest.mark.anyio
async def test_failure_after_company_created_is_recorded(monkeypatch):
    calls = _patch_completion(monkeypatch)
    calls["create_ticket"].side_effect = RuntimeError("ticket store down")
    submission, _ = onboarding.parse_submission(_form())
    with pytest.raises(RuntimeError):
        await onboarding.complete_onboarding({"id": 3}, submission)
    calls["mark_failed"].assert_awaited_once_with(3, error_message="ticket store down", company_id=42)
    calls["mark_submitted"].assert_not_called()


def test_ticket_description_escapes_client_input():
    submission, _ = onboarding.parse_submission(_form(client_name="<script>x</script>", notes="<b>hi</b>"))
    description = onboarding.ticket_description(submission)
    assert "<script>" not in description
    assert "&lt;script&gt;" in description
    assert "&lt;b&gt;hi&lt;/b&gt;" in description


def test_public_form_template_renders():
    import app.main as main_module
    from app.services import business_hours as business_hours_service

    template = main_module.templates.env.get_template("onboarding/client_form.html")
    html = template.render(
        app_name="MyPortal",
        token="t" * 43,
        state="open",
        form=onboarding.form_state(None, client_name="Acme"),
        errors=["Enter your business name."],
        weekdays=business_hours_service.WEEKDAYS,
        timezones=["Australia/Brisbane", "UTC"],
        default_timezone="Australia/Brisbane",
        max_sites=onboarding.MAX_SITES,
        blank_hours=business_hours_service.weekly_form_rows(None),
    )
    assert 'action="/onboarding/' + "t" * 43 + '"' in html
    assert 'name="site-0-contact_email"' in html
    assert 'name="site-__INDEX__-day_mon_open"' in html
    assert 'value="Acme"' in html


def test_migration_runs_on_sqlite():
    migration = Path("migrations/461_client_onboarding.sql").read_text(encoding="utf-8")
    adapted = db._adapt_sql_for_sqlite(migration)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO companies (id) VALUES (1)")
    connection.execute("CREATE TABLE company_addresses (id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE ticket_statuses (id INTEGER PRIMARY KEY, tech_status TEXT UNIQUE, "
        "tech_label TEXT, public_status TEXT)"
    )
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement)
    row = connection.execute(
        "SELECT tech_label FROM ticket_statuses WHERE tech_status = 'new_client'"
    ).fetchone()
    assert row == ("New Client",)
    connection.execute(
        "INSERT INTO client_onboardings (token_hash, expires_at) VALUES ('x', '2026-01-01')"
    )
    assert connection.execute("SELECT status FROM client_onboardings").fetchone() == ("pending",)
    assert connection.execute("SELECT pending_approval FROM companies").fetchone() == (0,)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Jo@Acme.com.au ", "jo@acme.com.au"),
        ("jo@acme", None),
        ("jo@@acme.com", None),
        ("@acme.com", None),
        ("jo@acme..com", None),
        ("jo @acme.com", None),
        ("jo@" + "!" * 5000 + ".com", None),
    ],
)
def test_normalise_email(value, expected):
    assert onboarding.normalise_email(value) == expected


@pytest.mark.anyio
async def test_approve_company_activates_company_and_contacts(monkeypatch):
    approve = AsyncMock(return_value=True)
    enable = AsyncMock(return_value=2)
    mark = AsyncMock()
    monkeypatch.setattr(onboarding_repo, "approve_company", approve)
    monkeypatch.setattr(onboarding_repo, "enable_onboarding_contacts", enable)
    monkeypatch.setattr(onboarding_repo, "mark_approved", mark)

    assert await onboarding.approve_company(42, approved_by_user_id=3) is True
    approve.assert_awaited_once_with(42)
    enable.assert_awaited_once_with(42)
    assert mark.await_args.args == (42,)
    assert mark.await_args.kwargs["approved_by_user_id"] == 3


@pytest.mark.anyio
async def test_approve_company_ignores_companies_not_pending(monkeypatch):
    monkeypatch.setattr(onboarding_repo, "approve_company", AsyncMock(return_value=False))
    enable = AsyncMock()
    monkeypatch.setattr(onboarding_repo, "enable_onboarding_contacts", enable)
    assert await onboarding.approve_company(42, approved_by_user_id=3) is False
    enable.assert_not_called()


@pytest.mark.anyio
async def test_approval_queries_only_touch_pending_company_and_form_contacts(monkeypatch):
    calls = []

    async def execute_rowcount(sql, params):
        calls.append((sql, params))
        return 1

    monkeypatch.setattr(db, "execute_rowcount", execute_rowcount)
    assert await onboarding_repo.approve_company(42) is True
    await onboarding_repo.enable_onboarding_contacts(42)
    approve_sql, approve_params = calls[0]
    assert "pending_approval = 0" in approve_sql and "pending_approval = 1" in approve_sql
    assert approve_params == (42,)
    enable_sql, enable_params = calls[1]
    assert "source = %s" in enable_sql
    assert enable_params == (42, "client_onboarding")


def test_pack_registers_approve_route():
    routes = {
        (method, route.path)
        for router in PACK.routers
        for route in router.routes
        for method in route.methods
    }
    assert ("POST", "/admin/companies/{company_id}/approve") in routes


def test_company_repository_accepts_pending_approval():
    company_repo._validate_company_fields({"name": "Acme", "pending_approval": 1})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"company_phone": ""}, "Enter your main phone number."),
        ({"company_email": ""}, "Enter your general email address."),
        ({"company_email": "hello@acme"}, "Enter a valid general email address."),
    ],
)
def test_main_phone_and_general_email_are_required(overrides, message):
    submission, errors = onboarding.parse_submission(_form(**overrides))
    assert submission is None
    assert message in errors


@pytest.mark.anyio
async def test_company_phone_is_the_main_phone(monkeypatch):
    calls = _patch_completion(monkeypatch)
    submission, _ = onboarding.parse_submission(_form())
    await onboarding.complete_onboarding({"id": 3}, submission)
    assert calls["create_company"].await_args.kwargs["phone"] == "07 3000 0001"



def test_link_needs_a_company_or_contact_name():
    assert onboarding.validate_link_details(client_name="", contact_name=" ", recipient_email="") is not None
    assert onboarding.validate_link_details(client_name="Acme", contact_name="", recipient_email="") is None
    assert onboarding.validate_link_details(client_name="", contact_name="Jo", recipient_email="") is None
    assert "valid email" in onboarding.validate_link_details(
        client_name="Acme", contact_name="", recipient_email="jo@acme"
    )


def _invite_record(**overrides):
    record = {
        "client_name": "Acme Pty Ltd",
        "contact_name": "Jo Bloggs",
        "recipient_email": "jo@acme.com.au",
        "invite_message": "Great to chat today!\n\nSee you <soon> & thanks.",
        "expires_at": datetime.utcnow() + timedelta(days=30),
    }
    record.update(overrides)
    return record


@pytest.fixture
def default_templates(monkeypatch):
    from app.services import message_templates as message_templates_service

    monkeypatch.setattr(message_templates_service, "get_template_by_slug", AsyncMock(return_value=None))
    return message_templates_service


@pytest.mark.anyio
async def test_invitation_uses_every_modal_value(default_templates):
    sender = {"first_name": "Sam", "last_name": "Tech", "email": "sam@msp.example"}
    subject, html, text = await onboarding.render_invitation(_invite_record(), "t" * 43, sender)
    assert "Jo" in subject
    assert "Hi Jo," in html
    assert "Acme Pty Ltd" in html
    assert "/onboarding/" + "t" * 43 in html
    assert "<p>Great to chat today!</p><p>See you &lt;soon&gt; &amp; thanks.</p>" in html
    assert "Sam Tech" in html
    assert "\u0000" not in html and "\u0000" not in text
    assert "/onboarding/" + "t" * 43 in text
    assert "See you <soon> & thanks." in text


@pytest.mark.anyio
async def test_invitation_without_contact_or_message_greets_the_company(default_templates):
    subject, html, _ = await onboarding.render_invitation(
        _invite_record(contact_name=None, invite_message=None), "t" * 43, None
    )
    assert "Hi Acme Pty Ltd," in html
    assert "Acme Pty Ltd" in subject
    assert "\u0000" not in html


@pytest.mark.anyio
async def test_invitation_uses_the_stored_message_template(monkeypatch):
    from app.services import message_templates as message_templates_service

    templates = {
        onboarding.INVITATION_SUBJECT_TEMPLATE_SLUG: {"content": "Onboarding for {{ company.name }}", "content_type": "text/plain"},
        onboarding.INVITATION_TEMPLATE_SLUG: {
            "content": "Hello {{ contact.name }}\n\n{{ onboarding.message }}\n\n{{ onboarding.link }}",
            "content_type": "text/plain",
        },
    }
    monkeypatch.setattr(
        message_templates_service, "get_template_by_slug", AsyncMock(side_effect=lambda slug: templates.get(slug))
    )
    subject, html, text = await onboarding.render_invitation(
        _invite_record(invite_message="Line one"), "t" * 43, None
    )
    assert subject == "Onboarding for Acme Pty Ltd"
    assert text.startswith("Hello Jo Bloggs\n\nLine one\n\n")
    assert "<p>Line one</p>" in html


@pytest.mark.anyio
async def test_link_email_is_sent_from_the_template(monkeypatch, default_templates):
    from app.services import email as email_service

    send = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(email_service, "send_email", send)
    assert await onboarding.send_link_email(_invite_record(), "t" * 43, {"email": "sam@msp.example"}) is True
    kwargs = send.await_args.kwargs
    assert kwargs["recipients"] == ["jo@acme.com.au"]
    assert kwargs["reply_to"] == "sam@msp.example"
    assert "Great to chat today!" in kwargs["html_body"]


def test_fresh_form_greets_and_prefills_the_contact():
    state = onboarding.form_state(None, client_name="Acme", contact_name="Jo van Bloggs")
    assert state["greeting_name"] == "Jo"
    values = state["sites"][0]["values"]
    assert values["contact_first_name"] == "Jo"
    assert values["contact_last_name"] == "van Bloggs"


def test_invitation_migration_seeds_editable_templates():
    sql = Path("migrations/462_client_onboarding_invitation.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS contact_name" in sql
    assert "ADD COLUMN IF NOT EXISTS invite_message" in sql
    assert f"'{onboarding.INVITATION_TEMPLATE_SLUG}'" in sql
    assert f"'{onboarding.INVITATION_SUBJECT_TEMPLATE_SLUG}'" in sql
    assert "{{ onboarding.message }}" in sql


def test_invitation_context_includes_environment_company_name(monkeypatch):
    from app.core.config import get_settings
    from app.services import message_templates

    monkeypatch.setattr(get_settings(), "company_name", "Our Company")
    context = onboarding.invitation_context(_invite_record(), "t" * 43)
    assert message_templates.render_content("{{env.company.name}}", context) == "Our Company"
    assert context["company"]["name"] == _invite_record()["client_name"]
