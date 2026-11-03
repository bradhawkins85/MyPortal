"""Technicians can choose whether a reply is emailed to the requester and/or watchers."""

from pathlib import Path

import pytest

from app.features.tickets import admin_routes
from app.services import modules
from app.services import tickets as tickets_service


TICKET = {
    "requester_email": "Requester@Example.com",
    "watcher_emails": ["watcher1@example.com", "watcher2@example.com"],
}


def test_excluded_recipients_default_to_nobody():
    assert tickets_service.reply_email_excluded_recipients(TICKET, {}) == []
    assert (
        tickets_service.reply_email_excluded_recipients(
            TICKET, {"email_requester": True, "email_watchers": True}
        )
        == []
    )


def test_excluded_recipients_watchers_unticked():
    assert tickets_service.reply_email_excluded_recipients(
        TICKET, {"email_requester": True, "email_watchers": False}
    ) == ["watcher1@example.com", "watcher2@example.com"]


def test_excluded_recipients_requester_unticked():
    assert tickets_service.reply_email_excluded_recipients(
        TICKET, {"email_requester": False, "email_watchers": True}
    ) == ["requester@example.com"]


def test_requester_who_is_also_a_watcher_is_still_emailed():
    ticket = {
        "requester_email": "requester@example.com",
        "watcher_emails": ["requester@example.com", "watcher@example.com"],
    }
    assert tickets_service.reply_email_excluded_recipients(
        ticket, {"email_requester": True, "email_watchers": False}
    ) == ["watcher@example.com"]


class _Form(dict):
    def get(self, key, default=None):  # mimic starlette FormData.get
        return super().get(key, default)


def test_reply_email_choice_defaults_to_send_without_marker():
    assert admin_routes._reply_email_choice(_Form(), "emailWatchers") is True


def test_reply_email_choice_reads_checkbox_when_marker_present():
    form = _Form({"replyEmailChoices": "1", "emailRequester": "true"})
    assert admin_routes._reply_email_choice(form, "emailRequester") is True
    assert admin_routes._reply_email_choice(form, "emailWatchers") is False


def _context(excluded):
    return {"reply": {"id": 5, "email_excluded_recipients": excluded}}


def test_filter_drops_watchers_from_cc():
    to, cc, bcc = modules._filter_reply_email_recipients(
        {"context": _context(["watcher1@example.com", "watcher2@example.com"])},
        ["requester@example.com"],
        ["Watcher One <Watcher1@example.com>", "watcher2@example.com"],
        ["audit@example.com"],
    )
    assert to == ["requester@example.com"]
    assert cc == []
    assert bcc == ["audit@example.com"]


def test_filter_promotes_cc_when_requester_excluded():
    to, cc, _ = modules._filter_reply_email_recipients(
        {"context": _context(["requester@example.com"])},
        ["requester@example.com"],
        ["watcher1@example.com"],
    )
    assert to == ["watcher1@example.com"]
    assert cc == []


def test_filter_is_noop_without_exclusions():
    to, cc, bcc = modules._filter_reply_email_recipients(
        {"context": {"reply": {"id": 5}}}, ["a@example.com"], ["b@example.com"]
    )
    assert (to, cc, bcc) == (["a@example.com"], ["b@example.com"], [])


@pytest.fixture
def captured_smtp2go(monkeypatch):
    captured: dict = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {"email_id": "id", "error_code": "SUCCESS"}

    async def fake_noop(**kwargs):
        return None

    async def fake_event(**kwargs):
        return {"id": 1, "status": "pending"}

    async def fake_success(event_id, **kwargs):
        return {"id": event_id, "status": "succeeded"}

    async def fake_no_attachments(payload):
        return []

    from app.services import smtp2go, webhook_monitor

    monkeypatch.setattr(smtp2go, "send_email_via_api", fake_send)
    monkeypatch.setattr(smtp2go, "record_email_sent", fake_noop)
    monkeypatch.setattr(smtp2go, "record_smtp2go_message_id", fake_noop, raising=False)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", fake_event)
    monkeypatch.setattr(modules, "_record_success", fake_success)
    monkeypatch.setattr(modules, "_load_ticket_email_attachments", fake_no_attachments)
    return captured


@pytest.mark.asyncio
async def test_smtp2go_skips_unticked_watchers(captured_smtp2go):
    payload = {
        "to": ["requester@example.com"],
        "cc": ["watcher1@example.com, watcher2@example.com"],
        "subject": "RE: Ticket",
        "html_body": "<p>Reply</p>",
        "context": _context(["watcher1@example.com", "watcher2@example.com"]),
    }

    await modules._invoke_smtp2go({"api_key": "k", "enable_tracking": False}, payload)

    assert captured_smtp2go["to"] == ["requester@example.com"]
    assert not captured_smtp2go.get("cc")


@pytest.mark.asyncio
async def test_smtp2go_skips_send_when_everyone_excluded(captured_smtp2go):
    payload = {
        "to": ["requester@example.com"],
        "subject": "RE: Ticket",
        "html_body": "<p>Reply</p>",
        "context": _context(["requester@example.com"]),
    }

    result = await modules._invoke_smtp2go({"api_key": "k"}, payload)

    assert result["status"] == "skipped"
    assert captured_smtp2go == {}


@pytest.mark.asyncio
async def test_smtp_skips_send_when_everyone_excluded(monkeypatch):
    async def fail_send(**kwargs):  # pragma: no cover - must not be called
        raise AssertionError("send_email should not be called")

    monkeypatch.setattr(modules._email_service(), "send_email", fail_send)
    result = await modules._invoke_smtp(
        {"default_recipients": "admin@example.com"},
        {"to": ["watcher1@example.com"], "context": _context(["watcher1@example.com"])},
    )
    assert result["status"] == "skipped"


def test_reply_composer_is_not_capped_to_viewport_height():
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    start = css.index("The reply composer grows with its content")
    block = css[start : css.index("}", start)]
    assert "max-height" not in block
    assert "max-height: calc(100vh - 2rem);\n    overflow-y: auto;" not in css


def test_reply_composer_offers_requester_and_watcher_choices():
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")
    assert 'name="replyEmailChoices"' in template
    assert 'name="emailRequester" value="true" checked' in template
    assert 'name="emailWatchers" value="true" checked' in template
