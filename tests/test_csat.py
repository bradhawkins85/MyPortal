"""Tests for customer satisfaction (CSAT) surveys."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.database import db
from app.features.csat import PACK
from app.repositories import csat as csat_repo
from app.services import csat as csat_service

MIGRATION = Path("migrations/578_ticket_csat_surveys.sql")


def _settings(**overrides):
    values = {"enabled": True, "delay_minutes": 60, "low_score_threshold": 2, "expiry_days": 14, "enabled_at": None}
    values.update(overrides)
    return csat_service.CsatSettings(**values)


def test_pack_registers_admin_public_and_api_routes():
    paths = {route.path for router in PACK.routers for route in router.routes}
    assert {
        "/admin/csat",
        "/admin/csat/settings",
        "/admin/csat/{survey_id:int}/follow-up",
        "/csat/{token}",
        "/api/csat/summary",
        "/api/csat/responses",
        "/api/csat/settings",
        "/api/csat/tickets/{ticket_id}",
    } <= paths
    assert PACK.background_jobs == (csat_service.survey_job,)


def test_migration_runs_on_sqlite():
    adapted = db._adapt_sql_for_sqlite(MIGRATION.read_text(encoding="utf-8"))
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE message_templates (id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT, "
        "description TEXT, content_type TEXT, content TEXT)"
    )
    for statement in db._split_sql_statements(adapted):
        # MySQL's INSERT IGNORE is SQLite's INSERT OR IGNORE (as in the other migration tests).
        connection.execute(statement.replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO"))
    assert connection.execute("SELECT enabled, delay_minutes FROM csat_settings WHERE id = 1").fetchone() == (0, 60)
    slugs = {row[0] for row in connection.execute("SELECT slug FROM message_templates")}
    assert slugs == {csat_service.SURVEY_TEMPLATE_SLUG, csat_service.SURVEY_SUBJECT_TEMPLATE_SLUG}
    connection.execute("INSERT INTO tickets (id) VALUES (7)")
    connection.execute("INSERT INTO ticket_csat_surveys (ticket_id) VALUES (7)")
    assert connection.execute("SELECT status FROM ticket_csat_surveys").fetchone() == ("pending",)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO ticket_csat_surveys (ticket_id) VALUES (7)")


def test_migration_header_and_template_variables():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.startswith("-- phase: expand")
    for rating in range(1, 6):
        assert "{{ survey.link_%d }}" % rating in sql


@pytest.mark.parametrize(("value", "expected"), [("5", 5), (" 1 ", 1), ("0", None), ("6", None), ("x", None), (None, None)])
def test_parse_rating(value, expected):
    assert csat_service.parse_rating(value) == expected


def test_csat_percent_counts_four_and_five_as_satisfied():
    assert csat_service.csat_percent(3, 4) == 75
    assert csat_service.csat_percent(0, 0) is None
    assert csat_service.is_satisfied(4) and not csat_service.is_satisfied(3)


def test_settings_form_clamps_values_and_stamps_enabled_at():
    current = _settings(enabled=False)
    parsed = csat_service.parse_settings_form(
        {"enabled": "1", "delay_minutes": "-5", "low_score_threshold": "9", "expiry_days": "500"}, current
    )
    assert parsed.enabled and parsed.enabled_at is not None
    assert parsed.delay_minutes == 0
    assert parsed.low_score_threshold == 4
    assert parsed.expiry_days == csat_service.MAX_EXPIRY_DAYS
    stamped = datetime(2026, 1, 1)
    kept = csat_service.parse_settings_form({"enabled": "on"}, _settings(enabled_at=stamped))
    assert kept.enabled_at == stamped
    off = csat_service.parse_settings_form({}, _settings(enabled_at=stamped))
    assert not off.enabled and off.enabled_at is None


@pytest.mark.asyncio
async def test_due_surveys_do_nothing_while_disabled(monkeypatch):
    monkeypatch.setattr(csat_service, "load_settings", AsyncMock(return_value=_settings(enabled=False)))
    list_due = AsyncMock()
    monkeypatch.setattr(csat_repo, "list_due_tickets", list_due)
    assert await csat_service.send_due_surveys() == {}
    list_due.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_surveys_window_starts_when_surveys_were_enabled(monkeypatch):
    now = datetime(2026, 11, 26, 12, 0)
    enabled_at = now - timedelta(days=1)
    monkeypatch.setattr(
        csat_service, "load_settings", AsyncMock(return_value=_settings(delay_minutes=30, enabled_at=enabled_at))
    )
    list_due = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    monkeypatch.setattr(csat_repo, "list_due_tickets", list_due)
    monkeypatch.setattr(csat_service, "send_survey", AsyncMock(side_effect=["sent", "skipped"]))
    assert await csat_service.send_due_surveys(now) == {"sent": 1, "skipped": 1}
    kwargs = list_due.await_args.kwargs
    assert kwargs["resolved_before"] == now - timedelta(minutes=30)
    assert kwargs["resolved_after"] == enabled_at


@pytest.mark.asyncio
async def test_due_surveys_never_look_back_further_than_the_send_window(monkeypatch):
    now = datetime(2026, 11, 26, 12, 0)
    monkeypatch.setattr(
        csat_service, "load_settings", AsyncMock(return_value=_settings(enabled_at=now - timedelta(days=60)))
    )
    list_due = AsyncMock(return_value=[])
    monkeypatch.setattr(csat_repo, "list_due_tickets", list_due)
    await csat_service.send_due_surveys(now)
    assert list_due.await_args.kwargs["resolved_after"] == now - csat_service.SEND_WINDOW


@pytest.mark.asyncio
async def test_send_survey_emails_requester_and_stores_only_token_hash(monkeypatch):
    from app.services import email as email_service

    monkeypatch.setattr(csat_service, "_requester_contact", AsyncMock(return_value={"email": "jo@example.com", "first_name": "Jo"}))
    monkeypatch.setattr(csat_service, "_technician_name", AsyncMock(return_value="Sam Tech"))
    monkeypatch.setattr(csat_repo, "claim_ticket", AsyncMock(return_value=11))
    mark_sent = AsyncMock()
    monkeypatch.setattr(csat_repo, "mark_sent", mark_sent)
    monkeypatch.setattr(
        csat_service, "render_survey_email", AsyncMock(side_effect=lambda context: ("Subject", context["survey"]["link_5"]))
    )
    send_email = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(email_service, "send_email", send_email)

    outcome = await csat_service.send_survey(
        {"id": 42, "company_id": 3, "assigned_user_id": 9, "subject": "Printer", "ticket_number": "T42"}, _settings()
    )

    assert outcome == "sent"
    assert send_email.await_args.kwargs["recipients"] == ["jo@example.com"]
    link = send_email.await_args.kwargs["html_body"]
    token = link.split("/csat/")[1].split("?")[0]
    assert link.endswith("?rating=5")
    stored_hash = mark_sent.await_args.kwargs["token_hash"]
    assert stored_hash == csat_service.hash_token(token) and token not in stored_hash
    assert mark_sent.await_args.kwargs["expires_at"] - mark_sent.await_args.kwargs["sent_at"] == timedelta(days=14)


@pytest.mark.asyncio
async def test_send_survey_skips_tickets_without_requester_email(monkeypatch):
    monkeypatch.setattr(csat_service, "_requester_contact", AsyncMock(return_value=None))
    monkeypatch.setattr(csat_repo, "claim_ticket", AsyncMock(return_value=5))
    mark_skipped = AsyncMock()
    monkeypatch.setattr(csat_repo, "mark_skipped", mark_skipped)
    assert await csat_service.send_survey({"id": 1}, _settings()) == "skipped"
    mark_skipped.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_survey_does_not_resend_an_already_claimed_ticket(monkeypatch):
    from app.services import email as email_service

    monkeypatch.setattr(csat_service, "_requester_contact", AsyncMock(return_value={"email": "a@b.co", "first_name": ""}))
    monkeypatch.setattr(csat_repo, "claim_ticket", AsyncMock(return_value=None))
    send_email = AsyncMock()
    monkeypatch.setattr(email_service, "send_email", send_email)
    assert await csat_service.send_survey({"id": 1}, _settings()) == "claimed"
    send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_survey_records_mail_failures(monkeypatch):
    from app.services import email as email_service

    monkeypatch.setattr(csat_service, "_requester_contact", AsyncMock(return_value={"email": "a@b.co", "first_name": ""}))
    monkeypatch.setattr(csat_service, "_technician_name", AsyncMock(return_value=""))
    monkeypatch.setattr(csat_repo, "claim_ticket", AsyncMock(return_value=5))
    monkeypatch.setattr(csat_service, "render_survey_email", AsyncMock(return_value=("s", "b")))
    monkeypatch.setattr(email_service, "send_email", AsyncMock(return_value=(False, None)))
    mark_failed = AsyncMock()
    monkeypatch.setattr(csat_repo, "mark_failed", mark_failed)
    assert await csat_service.send_survey({"id": 1}, _settings()) == "failed"
    mark_failed.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "record", "state"),
    [
        ("short", None, "invalid"),
        ("a" * 43, None, "invalid"),
        ("a" * 43, {"status": "responded"}, "answered"),
        ("a" * 43, {"status": "failed"}, "invalid"),
        ("a" * 43, {"status": "sent", "expires_at": datetime(2020, 1, 1)}, "expired"),
    ],
)
async def test_open_survey_rejects_bad_answered_and_expired_links(monkeypatch, token, record, state):
    monkeypatch.setattr(csat_repo, "get_by_token_hash", AsyncMock(return_value=record))
    with pytest.raises(csat_service.SurveyUnavailable) as excinfo:
        await csat_service.get_open_survey(token)
    assert str(excinfo.value) == state


@pytest.mark.asyncio
async def test_open_survey_looks_up_by_hash(monkeypatch):
    record = {"id": 1, "status": "sent", "expires_at": datetime(2999, 1, 1)}
    lookup = AsyncMock(return_value=record)
    monkeypatch.setattr(csat_repo, "get_by_token_hash", lookup)
    assert await csat_service.get_open_survey("b" * 43) is record
    assert lookup.await_args.args[0] == csat_service.hash_token("b" * 43)


@pytest.mark.asyncio
@pytest.mark.parametrize(("rating", "follow_up"), [(1, "open"), (2, "open"), (3, None), (5, None)])
async def test_low_scores_open_a_follow_up(monkeypatch, rating, follow_up):
    monkeypatch.setattr(csat_service, "load_settings", AsyncMock(return_value=_settings(low_score_threshold=2)))
    record = AsyncMock(return_value=True)
    monkeypatch.setattr(csat_repo, "record_response", record)
    after = AsyncMock()
    monkeypatch.setattr(csat_service, "_after_response", after)
    assert await csat_service.submit_response({"id": 4, "ticket_id": 8}, rating=rating, comment=None)
    assert record.await_args.kwargs["follow_up_status"] == follow_up
    assert after.await_args.kwargs["is_low"] is (follow_up == "open")


@pytest.mark.asyncio
async def test_second_submission_is_ignored(monkeypatch):
    monkeypatch.setattr(csat_service, "load_settings", AsyncMock(return_value=_settings()))
    monkeypatch.setattr(csat_repo, "record_response", AsyncMock(return_value=False))
    after = AsyncMock()
    monkeypatch.setattr(csat_service, "_after_response", after)
    assert not await csat_service.submit_response({"id": 4, "ticket_id": 8}, rating=5, comment=None)
    after.assert_not_awaited()


@pytest.mark.asyncio
async def test_response_is_posted_as_internal_note_and_fires_automation(monkeypatch):
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    create_reply = AsyncMock()
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)
    monkeypatch.setattr(tickets_service, "broadcast_ticket_event", AsyncMock())
    emit = AsyncMock()
    monkeypatch.setattr(tickets_service, "emit_ticket_csat_received_event", emit)
    await csat_service._after_response({"ticket_id": 8}, rating=1, comment="<b>slow</b>", is_low=True)
    kwargs = create_reply.await_args.kwargs
    assert kwargs["is_internal"] is True and kwargs["ticket_id"] == 8
    assert "&lt;b&gt;slow&lt;/b&gt;" in kwargs["body"] and "<b>slow</b>" not in kwargs["body"]
    assert emit.await_args.kwargs["csat"]["is_low"] is True


def test_csat_event_is_an_automation_trigger():
    from app.services import automations

    assert csat_service.EVENT_NAME in {event["value"] for event in automations.list_trigger_events()}


def test_clean_comment_trims_and_limits():
    assert csat_service.clean_comment("  \r\n ") is None
    assert len(csat_service.clean_comment("x" * 5000)) == csat_service.MAX_COMMENT_LENGTH


def test_csat_paths_are_csrf_exempt_only_for_the_public_page():
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert '"/csat/",' in source
    assert '"/admin/csat' not in source


def _render(name: str, **context):
    import app.main as main_module

    template = main_module.templates.env.get_template(name)
    return template.render(app_name="MyPortal", static_url=lambda path: path, **context)


def test_public_survey_preselects_rating_and_posts_back():
    html = _render(
        "csat/survey.html",
        state="open",
        token="t" * 43,
        ticket_number="T42",
        ticket_subject="Printer <offline>",
        rating=4,
        comment="",
        error=None,
        rating_labels=csat_service.RATING_LABELS,
        max_comment_length=csat_service.MAX_COMMENT_LENGTH,
    )
    assert 'action="/csat/' + "t" * 43 + '"' in html
    assert 'value="4" required checked' in html
    assert "Printer &lt;offline&gt;" in html


@pytest.mark.parametrize("state", ["thanks", "answered", "expired", "invalid"])
def test_public_survey_end_states_render(state):
    html = _render("csat/survey.html", state=state, token="", rating_labels=csat_service.RATING_LABELS)
    assert 'role="status"' in html
    assert "<form" not in html


@pytest.mark.asyncio
async def test_public_get_does_not_record_a_rating(monkeypatch):
    from app.features.csat import routes

    monkeypatch.setattr(csat_service, "get_open_survey", AsyncMock(return_value={"id": 1, "ticket_id": 2}))
    submit = AsyncMock()
    monkeypatch.setattr(csat_service, "submit_response", submit)
    render = AsyncMock(return_value="page")
    monkeypatch.setattr(routes, "_render_public", render)
    await routes.public_survey_form("t" * 43, SimpleNamespace(), rating="5")
    submit.assert_not_awaited()
    assert render.await_args.kwargs["rating"] == 5


@pytest.mark.asyncio
async def test_public_post_requires_a_rating(monkeypatch):
    from app.features.csat import routes

    monkeypatch.setattr(csat_service, "get_open_survey", AsyncMock(return_value={"id": 1, "ticket_id": 2}))
    submit = AsyncMock()
    monkeypatch.setattr(csat_service, "submit_response", submit)
    render = AsyncMock(return_value="page")
    monkeypatch.setattr(routes, "_render_public", render)
    request = SimpleNamespace(form=AsyncMock(return_value={"comment": "hi"}))
    await routes.public_survey_submit("t" * 43, request)
    submit.assert_not_awaited()
    assert render.await_args.kwargs["status_code"] == 400


@pytest.mark.asyncio
async def test_settings_route_requires_super_admin(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.features.csat import routes

    redirect = RedirectResponse("/login")
    main = SimpleNamespace(_require_super_admin_page=AsyncMock(return_value=(None, redirect)))
    monkeypatch.setattr(routes, "_main", lambda: main)
    save = AsyncMock()
    monkeypatch.setattr(csat_service, "save_settings", save)
    assert await routes.csat_save_settings(SimpleNamespace()) is redirect
    save.assert_not_awaited()


def test_admin_page_renders_settings_only_for_managers():
    import app.main as main_module

    main_module.templates.env.get_template("admin/csat.html")  # compiles
    source = Path("app/templates/admin/csat.html").read_text(encoding="utf-8")
    assert "{% if can_manage %}" in source
    assert 'action="/admin/csat/settings"' in source
    assert source.count('{% include "partials/csrf.html" %}') == 2
    assert "onchange=" not in source


@pytest.mark.asyncio
async def test_repository_queries_bind_every_value(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    async def fake_fetch_all(sql, params=None):
        calls.append((sql, params))
        return []

    async def fake_fetch_one(sql, params=None):
        calls.append((sql, params))
        return {}

    monkeypatch.setattr(db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(db, "fetch_one", fake_fetch_one)
    since = datetime(2026, 1, 1)
    await csat_repo.list_responses(since=since, company_id=3, follow_up_only=True, limit=10)
    await csat_repo.summary_counts(since=since, company_id=3)
    await csat_repo.breakdown_by_technician(since=since, company_id=3)
    await csat_repo.list_due_tickets(resolved_before=since, resolved_after=since, limit=5)
    for sql, params in calls:
        assert sql.count("%s") == len(params)
