from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import FormData

from app.repositories import marketing_campaigns as campaign_repo
from app.services import business_hours as bh
from app.services import marketing_campaigns as campaigns
from app.services import imap as imap_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _portal_url(monkeypatch):
    monkeypatch.setattr(bh, "default_timezone_name", lambda: "Australia/Brisbane")
    monkeypatch.setattr(campaigns, "_portal_url", lambda: "https://portal.example.com")
    monkeypatch.setattr(campaigns, "default_sender", lambda: "news@example.com")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _campaign(**overrides):
    campaign = {
        "id": 7,
        "name": "Bitdefender renewal",
        "category": "updates",
        "subject": "Changes for {{ company.name }}",
        "message_template_id": None,
        "body_html": "<p>Hi {{ contact.first_name }},</p><p>Your {{ company.name }} plan is changing.</p>",
        "sender_email": None,
        "sender_name": "Support",
        "reply_to": "help@example.com",
        "audience": {},
        "business_hours_source": "company",
        "status": "sending",
    }
    campaign.update(overrides)
    return campaign


CONTACT = {
    "staff_id": 3,
    "company_id": 11,
    "company_name": "Acme <Ltd>",
    "first_name": "Jo",
    "last_name": "Bloggs",
    "email": "jo@acme.com.au",
    "job_title": "Director",
}


def test_audience_from_form_parses_multi_values_and_terms():
    form = FormData(
        [
            ("company_mode", "selected"),
            ("company_ids", "4"),
            ("company_ids", "9"),
            ("company_ids", "bogus"),
            ("asset_field_ids", "2"),
            ("contact_scope", "all"),
            ("job_titles", "Director, manager ,director"),
            ("include_emails", "Me@Example.com\nnot-an-email"),
            ("vip_only", "1"),
        ]
    )
    audience = campaigns.audience_from_form(form)
    assert audience["company_mode"] == "selected"
    assert audience["company_ids"] == [4, 9]
    assert audience["asset_field_ids"] == [2]
    assert audience["contact_scope"] == "all"
    assert audience["job_titles"] == ["Director", "manager"]
    assert audience["include_emails"] == ["me@example.com"]
    assert audience["vip_only"] is True


def test_audience_defaults_to_billing_contacts_at_all_companies():
    audience = campaigns.normalise_audience({"contact_scope": "nonsense"})
    assert audience["company_mode"] == "all"
    assert audience["contact_scope"] == "billing"


def test_campaign_form_requires_a_body_source():
    form = FormData([("name", "x"), ("subject", "y"), ("category", "updates")])
    with pytest.raises(campaigns.CampaignError):
        campaigns.campaign_fields_from_form(form)


def test_campaign_form_converts_scheduled_time_to_utc():
    form = FormData(
        [
            ("name", "x"),
            ("subject", "y"),
            ("category", "sales"),
            ("body_html", "<p>hi</p>"),
            ("scheduled_for", "2026-10-01T09:00"),
            ("tz_offset", "-600"),  # Brisbane is 600 minutes ahead of UTC
        ]
    )
    fields = campaigns.campaign_fields_from_form(form)
    assert fields["category"] == "sales"
    assert fields["scheduled_for"] == utc(2026, 9, 30, 23, 0)


@pytest.mark.anyio
async def test_audience_sql_applies_every_filter(monkeypatch):
    captured = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(campaign_repo.db, "fetch_all", fake_fetch_all)
    audience = campaigns.normalise_audience(
        {
            "company_mode": "selected",
            "company_ids": [1, 2],
            "exclude_company_ids": [3],
            "asset_field_ids": [5],
            "product_ids": [8, 9],
            "contact_scope": "billing",
            "job_titles": ["Director"],
            "departments": ["Finance", "Accounts"],
        }
    )
    await campaign_repo.find_audience_contacts(audience)
    sql = captured["sql"]
    assert "s.company_id IN (%s, %s)" in sql
    assert "s.company_id NOT IN (%s)" in sql
    assert "asset_custom_field_values" in sql
    assert "subscriptions sub" in sql
    assert "billing_contacts bc2" in sql
    assert "s.department LIKE %s OR s.department LIKE %s" in sql
    assert captured["params"] == (1, 2, 3, 5, 8, 9, "%Director%", "%Finance%", "%Accounts%")


@pytest.mark.anyio
async def test_selected_companies_with_none_picked_matches_nobody(monkeypatch):
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(campaign_repo.db, "fetch_all", fetch)
    result = await campaign_repo.find_audience_contacts(campaigns.normalise_audience({"company_mode": "selected"}))
    assert result == []
    fetch.assert_not_called()


@pytest.mark.anyio
async def test_resolve_audience_dedupes_and_reports_exclusions(monkeypatch):
    rows = [
        dict(CONTACT),
        {**CONTACT, "staff_id": 4, "email": "JO@acme.com.au"},
        {**CONTACT, "staff_id": 5, "email": "optout@acme.com.au"},
        {**CONTACT, "staff_id": 6, "email": "skip@acme.com.au"},
        {**CONTACT, "staff_id": 7, "email": "broken"},
    ]
    monkeypatch.setattr(campaign_repo, "find_audience_contacts", AsyncMock(return_value=rows))
    monkeypatch.setattr(campaign_repo, "find_staff_by_emails", AsyncMock(return_value=[]))
    monkeypatch.setattr(campaign_repo, "list_opted_out", AsyncMock(return_value={"optout@acme.com.au"}))
    from app.repositories import email_blocklist

    monkeypatch.setattr(email_blocklist, "filter_allowed", AsyncMock(return_value=([], [])))
    campaign = _campaign(
        category="sales",
        audience={"exclude_emails": ["skip@acme.com.au"], "include_emails": ["extra@else.com.au"]},
    )
    resolved = await campaigns.resolve_audience(campaign)
    assert [r["email"] for r in resolved["recipients"]] == ["jo@acme.com.au", "extra@else.com.au"]
    reasons = {r["email"]: r["reason"] for r in resolved["excluded"]}
    assert reasons["optout@acme.com.au"] == "Unsubscribed from sales emails"
    assert reasons["skip@acme.com.au"] == "Excluded by address"
    assert reasons["broken"] == "Invalid email address"


@pytest.mark.anyio
async def test_render_email_substitutes_and_escapes_variables():
    rendered = await campaigns.render_email(_campaign(), CONTACT, token="a" * 32)
    assert rendered["subject"] == "Changes for Acme <Ltd>"
    assert "Hi Jo," in rendered["html"]
    assert "Acme &lt;Ltd&gt; plan" in rendered["html"]
    assert "Unsubscribe" not in rendered["html"]


@pytest.mark.anyio
async def test_sales_email_has_unsubscribe_footer():
    rendered = await campaigns.render_email(_campaign(category="sales"), CONTACT, token="b" * 32)
    assert "https://portal.example.com/marketing/unsubscribe/" + "b" * 32 in rendered["html"]
    assert "Unsubscribe from sales emails" in rendered["text"]


@pytest.mark.anyio
async def test_plain_text_template_body_is_used(monkeypatch):
    monkeypatch.setattr(
        campaigns.message_templates_service,
        "get_template",
        AsyncMock(return_value={"content": "Hello {{ contact.full_name }}\nBye", "content_type": "text/plain"}),
    )
    rendered = await campaigns.render_email(_campaign(message_template_id=2), CONTACT, token="c" * 32)
    assert "<div>Hello Jo Bloggs<br>Bye</div>" in rendered["html"]
    assert "critical service notice" in rendered["html"]


def test_message_id_round_trips_to_token():
    token = "d" * 32
    message_id = campaigns.message_id_for(token, "news@example.com")
    assert message_id == f"<mkt-{token}@example.com>"
    assert campaigns.token_from_message_ids(["<other@x>", message_id]) == token


def test_normalise_subject_strips_reply_prefixes():
    assert campaigns.normalise_subject("RE: Fwd:  Changes for Acme") == "changes for acme"


def _recipient(**overrides):
    recipient = {
        "id": 21,
        "campaign_id": 7,
        "staff_id": 3,
        "company_id": 11,
        "email": "jo@acme.com.au",
        "token": "e" * 32,
        "status": "sending",
    }
    recipient.update(overrides)
    return recipient


@pytest.mark.anyio
async def test_send_waits_for_company_business_hours(monkeypatch):
    schedule = bh.build_schedule(
        {"timezone": "Australia/Brisbane", "weekly_hours": bh.DEFAULT_WEEKLY_HOURS}, set()
    )
    get_schedule = AsyncMock(return_value=schedule)
    monkeypatch.setattr(campaigns.business_hours_service, "get_schedule", get_schedule)
    defer = AsyncMock()
    monkeypatch.setattr(campaign_repo, "defer_recipient", defer)
    dispatch = AsyncMock()
    monkeypatch.setattr(campaigns, "_dispatch", dispatch)

    # Tuesday 18:00 in Brisbane: closed until Wednesday 08:30 (Tuesday 22:30 UTC).
    outcome = await campaigns.send_recipient(_campaign(), _recipient(), utc(2026, 9, 29, 8, 0))
    assert outcome == "deferred"
    get_schedule.assert_awaited_once_with(11)
    defer.assert_awaited_once_with(21, utc(2026, 9, 29, 22, 30))
    dispatch.assert_not_called()


@pytest.mark.anyio
async def test_send_uses_global_hours_when_campaign_asks(monkeypatch):
    get_schedule = AsyncMock(return_value=None)
    monkeypatch.setattr(campaigns.business_hours_service, "get_schedule", get_schedule)
    monkeypatch.setattr(campaign_repo, "get_contact", AsyncMock(return_value=dict(CONTACT)))
    monkeypatch.setattr(campaigns, "_dispatch", AsyncMock(return_value=(True, {"id": "sm-1", "provider": "smtp2go"})))
    sent = AsyncMock()
    monkeypatch.setattr(campaign_repo, "mark_recipient_sent", sent)

    outcome = await campaigns.send_recipient(
        _campaign(business_hours_source="global"), _recipient(), utc(2026, 9, 29, 1, 0)
    )
    assert outcome == "sent"
    get_schedule.assert_awaited_once_with(None)
    kwargs = sent.await_args.kwargs
    assert kwargs["smtp2go_message_id"] == "sm-1"
    assert kwargs["message_id"] == "<mkt-" + "e" * 32 + "@example.com>"
    assert kwargs["subject"] == "Changes for Acme <Ltd>"


@pytest.mark.anyio
async def test_sales_send_skips_contacts_who_unsubscribed_after_queueing(monkeypatch):
    monkeypatch.setattr(campaigns.business_hours_service, "get_schedule", AsyncMock(return_value=None))
    monkeypatch.setattr(campaign_repo, "list_opted_out", AsyncMock(return_value={"jo@acme.com.au"}))
    skip = AsyncMock()
    monkeypatch.setattr(campaign_repo, "skip_recipient", skip)
    dispatch = AsyncMock()
    monkeypatch.setattr(campaigns, "_dispatch", dispatch)
    outcome = await campaigns.send_recipient(_campaign(category="sales"), _recipient(), utc(2026, 9, 29, 1, 0))
    assert outcome == "skipped"
    skip.assert_awaited_once_with(21, "unsubscribed")
    dispatch.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_sets_reply_headers_and_list_unsubscribe(monkeypatch):
    from app.services import email as email_service

    send = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(email_service, "send_email", send)
    rendered = {"subject": "s", "html": "<p>h</p>", "text": "h"}
    await campaigns._dispatch(_campaign(category="sales"), to="jo@acme.com.au", rendered=rendered, token="f" * 32)
    kwargs = send.await_args.kwargs
    assert kwargs["recipients"] == ["jo@acme.com.au"]
    assert kwargs["reply_to"] == "help@example.com"
    assert kwargs["sender"] == "Support <news@example.com>"
    headers = kwargs["headers"]
    assert headers["Message-ID"] == "<mkt-" + "f" * 32 + "@example.com>"
    assert headers["List-Unsubscribe"] == "<https://portal.example.com/marketing/unsubscribe/" + "f" * 32 + ">"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


@pytest.mark.anyio
async def test_process_due_sends_only_sends_claimed_recipients(monkeypatch):
    monkeypatch.setattr(campaign_repo, "list_due_recipients", AsyncMock(return_value=[_recipient(), _recipient(id=22)]))
    monkeypatch.setattr(campaign_repo, "get_campaign", AsyncMock(return_value=_campaign()))
    monkeypatch.setattr(campaign_repo, "claim_recipient", AsyncMock(side_effect=[True, False]))
    send = AsyncMock(return_value="sent")
    monkeypatch.setattr(campaigns, "send_recipient", send)
    complete = AsyncMock()
    monkeypatch.setattr(campaign_repo, "complete_finished_campaigns", complete)
    counts = await campaigns.process_due_sends()
    assert counts["sent"] == 1
    assert send.await_count == 1
    complete.assert_awaited_once()


@pytest.mark.anyio
async def test_queue_campaign_snapshots_recipients(monkeypatch):
    monkeypatch.setattr(campaign_repo, "get_campaign", AsyncMock(return_value=_campaign(status="draft", scheduled_for=utc(2026, 10, 1))))
    monkeypatch.setattr(
        campaigns,
        "resolve_audience",
        AsyncMock(return_value={"recipients": [{"email": "jo@acme.com.au", "staff_id": 3, "company_id": 11}], "excluded": []}),
    )
    monkeypatch.setattr(campaign_repo, "mark_campaign_sending", AsyncMock(return_value=True))
    insert = AsyncMock()
    monkeypatch.setattr(campaign_repo, "insert_recipients", insert)
    assert await campaigns.queue_campaign(7) == 1
    rows = insert.await_args.args[1]
    assert rows[0]["send_after"] == utc(2026, 10, 1)
    assert len(rows[0]["token"]) == 32


@pytest.mark.anyio
async def test_sales_campaign_needs_portal_url(monkeypatch):
    monkeypatch.setattr(campaigns, "_portal_url", lambda: "")
    monkeypatch.setattr(campaign_repo, "get_campaign", AsyncMock(return_value=_campaign(status="draft", category="sales")))
    with pytest.raises(campaigns.CampaignError):
        await campaigns.queue_campaign(7)


@pytest.mark.anyio
async def test_unsubscribe_records_sales_opt_out(monkeypatch):
    token = "1" * 32
    monkeypatch.setattr(
        campaign_repo,
        "get_recipient_by_token",
        AsyncMock(return_value={"email": "Jo@acme.com.au", "campaign_id": 7}),
    )
    add = AsyncMock()
    monkeypatch.setattr(campaign_repo, "add_opt_out", add)
    assert await campaigns.unsubscribe(token)
    add.assert_awaited_once_with("jo@acme.com.au", "sales", 7)
    assert await campaigns.unsubscribe("../etc") is None


@pytest.mark.anyio
async def test_smtp2go_event_updates_campaign_recipient(monkeypatch):
    monkeypatch.setattr(
        campaign_repo,
        "get_recipient_by_smtp2go_message_id",
        AsyncMock(return_value={"id": 21, "token": "2" * 32}),
    )
    record = AsyncMock()
    monkeypatch.setattr(campaign_repo, "record_engagement", record)
    moment = utc(2026, 9, 29, 1, 0)
    assert await campaigns.record_smtp2go_event("sm-1", "open", moment) == "mkt-" + "2" * 32
    record.assert_awaited_once_with(21, "open", moment)


@pytest.mark.anyio
async def test_reply_matches_by_message_id_then_subject(monkeypatch):
    by_token = AsyncMock(return_value={"id": 21})
    monkeypatch.setattr(campaign_repo, "get_recipient_by_token", by_token)
    match = await campaigns.match_inbound_reply(
        subject="whatever",
        from_email="jo@acme.com.au",
        related_message_ids=["<mkt-" + "3" * 32 + "@example.com>"],
    )
    assert match == {"id": 21}

    recent = AsyncMock(
        return_value=[
            {"id": 30, "subject_rendered": "Something else"},
            {"id": 31, "subject_rendered": "Changes for Acme"},
        ]
    )
    monkeypatch.setattr(campaign_repo, "list_recent_sent_to", recent)
    match = await campaigns.match_inbound_reply(
        subject="RE: Changes for Acme", from_email="Jo Bloggs <JO@acme.com.au>", related_message_ids=[]
    )
    assert match["id"] == 31
    assert recent.await_args.args[0] == "jo@acme.com.au"


@pytest.mark.anyio
async def test_first_reply_links_ticket_and_adds_internal_note(monkeypatch):
    link = AsyncMock()
    monkeypatch.setattr(campaign_repo, "link_reply_ticket", link)
    monkeypatch.setattr(campaign_repo, "get_campaign", AsyncMock(return_value=_campaign()))
    from app.repositories import tickets as tickets_repo

    create_reply = AsyncMock()
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)
    recipient = {"id": 21, "campaign_id": 7, "email": "jo@acme.com.au", "sent_at": utc(2026, 9, 28)}
    await campaigns.link_reply(recipient, 99, new_ticket=True)
    assert link.await_args.args[:2] == (21, 99)
    note = create_reply.await_args.kwargs
    assert note["is_internal"] is True
    assert "/admin/marketing/campaigns/7" in note["body"]


@pytest.mark.anyio
async def test_imap_reuses_open_reply_ticket_but_not_closed_one(monkeypatch):
    recipient = {"id": 21, "reply_ticket_id": 99}
    monkeypatch.setattr(campaigns, "match_inbound_reply", AsyncMock(return_value=recipient))
    from app.repositories import tickets as tickets_repo

    monkeypatch.setattr(tickets_repo, "get_ticket", AsyncMock(return_value={"id": 99, "status": "open"}))
    ticket, matched = await imap_service._match_marketing_campaign_reply(
        subject="RE: x", from_email="jo@acme.com.au", related_message_ids=[]
    )
    assert ticket["id"] == 99 and matched is recipient

    monkeypatch.setattr(tickets_repo, "get_ticket", AsyncMock(return_value={"id": 99, "status": "closed"}))
    ticket, matched = await imap_service._match_marketing_campaign_reply(
        subject="RE: x", from_email="jo@acme.com.au", related_message_ids=[]
    )
    assert ticket is None and matched is recipient


@pytest.mark.anyio
async def test_no_saved_hours_uses_default_weekday_hours_in_cron_timezone(monkeypatch):
    monkeypatch.setattr(campaigns.business_hours_service, "get_schedule", AsyncMock(return_value=None))
    defer = AsyncMock()
    monkeypatch.setattr(campaign_repo, "defer_recipient", defer)
    dispatch = AsyncMock()
    monkeypatch.setattr(campaigns, "_dispatch", dispatch)

    # 20:00 UTC Monday is 06:00 Tuesday in Brisbane: wait for 08:30 local (22:30 UTC).
    outcome = await campaigns.send_recipient(_campaign(), _recipient(), utc(2026, 9, 28, 20, 0))
    assert outcome == "deferred"
    defer.assert_awaited_once_with(21, utc(2026, 9, 28, 22, 30))
    dispatch.assert_not_called()


@pytest.mark.anyio
async def test_global_schedule_is_read_in_cron_timezone(monkeypatch):
    bh.invalidate_cache()
    from app.repositories import business_hours as bh_repo

    monkeypatch.setattr(
        bh_repo,
        "get_schedule",
        AsyncMock(return_value={"timezone": "UTC", "weekly_hours": bh.DEFAULT_WEEKLY_HOURS, "company_id": None}),
    )
    monkeypatch.setattr(bh_repo, "list_closures", AsyncMock(return_value=[]))
    try:
        schedule = await bh.get_schedule(None)
    finally:
        bh.invalidate_cache()
    assert schedule.timezone_name == "Australia/Brisbane"
    assert bh.is_open(schedule, utc(2026, 9, 28, 23, 0))  # Tue 09:00 Brisbane
    assert not bh.is_open(schedule, utc(2026, 9, 29, 9, 0))  # Tue 19:00 Brisbane


# ---------------------------------------------------------------------------
# Opt-out routes: profile toggle, admin add, case-insensitive matching,
# updates ignoring opt-outs, and updates footer.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_profile_opt_out_adds_and_removes_sales_opt_out(monkeypatch):
    """Turning the profile toggle off adds an opt-out; turning it on removes it."""
    from app.services import marketing_campaigns as svc

    email = svc.normalise_email("Jo@Example.com.au")
    assert email == "jo@example.com.au"

    add = AsyncMock()
    monkeypatch.setattr(campaign_repo, "add_opt_out", add)
    await campaign_repo.add_opt_out(email, svc.CATEGORY_SALES, None)
    add.assert_awaited_once_with("jo@example.com.au", "sales", None)

    remove = AsyncMock()
    monkeypatch.setattr(campaign_repo, "remove_opt_out", remove)
    await campaign_repo.remove_opt_out(email, svc.CATEGORY_SALES)
    remove.assert_awaited_once_with("jo@example.com.au", "sales")


@pytest.mark.anyio
async def test_admin_opt_out_add_normalises_email(monkeypatch):
    """Admin opt-out add normalises the email to lower-case."""
    add = AsyncMock()
    monkeypatch.setattr(campaign_repo, "add_opt_out", add)
    email = campaigns.normalise_email("  Admin@ACME.COM.au  ")
    assert email == "admin@acme.com.au"
    await campaign_repo.add_opt_out(email, campaigns.CATEGORY_SALES, None)
    add.assert_awaited_once_with("admin@acme.com.au", "sales", None)


@pytest.mark.anyio
async def test_opt_out_matching_is_case_insensitive(monkeypatch):
    """list_opted_out lower-cases returned emails so matching is case-insensitive."""
    captured = {}

    async def fake_fetch_all(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return [{"email": "JO@acme.com.au"}]

    monkeypatch.setattr(campaign_repo.db, "fetch_all", fake_fetch_all)
    result = await campaign_repo.list_opted_out(["jo@acme.com.au"], campaigns.CATEGORY_SALES)
    assert result == {"jo@acme.com.au"}


@pytest.mark.anyio
async def test_updates_campaign_ignores_opt_outs(monkeypatch):
    """Send re-check only skips sales campaigns; updates always send."""
    monkeypatch.setattr(campaigns.business_hours_service, "get_schedule", AsyncMock(return_value=None))
    monkeypatch.setattr(campaign_repo, "list_opted_out", AsyncMock(return_value={"jo@acme.com.au"}))
    dispatch = AsyncMock(return_value=(True, {}))
    monkeypatch.setattr(campaigns, "_dispatch", dispatch)
    monkeypatch.setattr(campaigns, "contact_for", AsyncMock(return_value=dict(CONTACT)))
    monkeypatch.setattr(campaigns, "render_email", AsyncMock(return_value={"subject": "s", "html": "h", "text": "t"}))
    monkeypatch.setattr(campaign_repo, "mark_recipient_sent", AsyncMock())

    outcome = await campaigns.send_recipient(_campaign(category="updates"), _recipient(), utc(2026, 9, 28, 2, 0))
    assert outcome == "sent"
    dispatch.assert_awaited_once()


@pytest.mark.anyio
async def test_sales_campaign_skips_opted_out_recipient(monkeypatch):
    """Sales campaigns re-check opt-outs before sending and skip."""
    monkeypatch.setattr(campaigns.business_hours_service, "get_schedule", AsyncMock(return_value=None))
    monkeypatch.setattr(campaign_repo, "list_opted_out", AsyncMock(return_value={"jo@acme.com.au"}))
    skip = AsyncMock()
    monkeypatch.setattr(campaign_repo, "skip_recipient", skip)

    outcome = await campaigns.send_recipient(_campaign(category="sales"), _recipient(), utc(2026, 9, 28, 2, 0))
    assert outcome == "skipped"
    skip.assert_awaited_once_with(21, "unsubscribed")


@pytest.mark.anyio
async def test_updates_email_has_critical_notice_footer():
    """Updates-category emails include a critical service notice in the body."""
    rendered = await campaigns.render_email(
        _campaign(category="updates", body_html="<p>Body</p>"),
        CONTACT,
        token="a" * 32,
    )
    assert "critical service notice" in rendered["html"]
    assert "Unsubscribe" not in rendered["html"]


@pytest.mark.anyio
async def test_sales_email_has_unsubscribe_footer_and_headers():
    """Sales-category emails include an unsubscribe link and List-Unsubscribe headers."""
    rendered = await campaigns.render_email(
        _campaign(category="sales", body_html="<p>Body</p>"),
        CONTACT,
        token="a" * 32,
    )
    assert "Unsubscribe from sales emails" in rendered["html"]

    headers = campaigns._headers(_campaign(category="sales"), "a" * 32)
    assert "List-Unsubscribe" in headers
    assert "List-Unsubscribe-Post" in headers
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


@pytest.mark.anyio
async def test_all_opt_out_paths_write_to_same_table(monkeypatch):
    """Every opt-out source calls add_opt_out with the same (email, category) pair."""
    add = AsyncMock()
    monkeypatch.setattr(campaign_repo, "add_opt_out", add)

    # 1. Unsubscribe link path
    token = "1" * 32
    monkeypatch.setattr(
        campaign_repo,
        "get_recipient_by_token",
        AsyncMock(return_value={"email": "Jo@acme.com.au", "campaign_id": 7}),
    )
    await campaigns.unsubscribe(token)
    add.assert_awaited_once_with("jo@acme.com.au", "sales", 7)
    add.reset_mock()

    # 2. Profile toggle path (off)
    await campaign_repo.add_opt_out("jo@acme.com.au", "sales", None)
    add.assert_awaited_once_with("jo@acme.com.au", "sales", None)
    add.reset_mock()

    # 3. Admin opt-out add path
    email = campaigns.normalise_email("JO@ACME.com.au")
    await campaign_repo.add_opt_out(email, "sales", None)
    add.assert_awaited_once_with("jo@acme.com.au", "sales", None)
