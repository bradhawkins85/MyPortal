"""The admin reply form warns technicians about blocklisted recipients."""

from pathlib import Path

import pytest

from app.repositories import email_blocklist
from app.services import tickets as tickets_service


class FakeDb:
    def __init__(self, blocked: list[str]):
        self.rows = [{"email": email} for email in blocked]
        self.calls = 0

    async def fetch_all(self, query, params=None):
        self.calls += 1
        wanted = set((params or {}).values())
        return [row for row in self.rows if row["email"] in wanted]


WATCHERS = [
    {"user_id": 7, "email": None, "user": {"email": "Tech@Example.com"}},
    {"user_id": None, "email": "client@example.com", "user": None},
    {"user_id": None, "email": "ok@example.com", "user": None},
]


@pytest.mark.anyio
async def test_blocked_requester_and_watchers_are_reported(monkeypatch):
    monkeypatch.setattr(
        email_blocklist,
        "db",
        FakeDb(["requester@example.com", "tech@example.com", "client@example.com"]),
    )

    result = await tickets_service.blocked_reply_recipients("Requester@Example.com", WATCHERS)

    assert result == [
        {"email": "Requester@Example.com", "role": "requester"},
        {"email": "Tech@Example.com", "role": "watcher", "user_id": 7, "watcher_email": None},
        {"email": "client@example.com", "role": "watcher", "user_id": None, "watcher_email": "client@example.com"},
    ]


@pytest.mark.anyio
async def test_no_warning_when_nobody_is_blocked(monkeypatch):
    monkeypatch.setattr(email_blocklist, "db", FakeDb(["someone-else@example.com"]))

    assert await tickets_service.blocked_reply_recipients("requester@example.com", WATCHERS) == []


@pytest.mark.anyio
async def test_no_lookup_without_recipients(monkeypatch):
    fake_db = FakeDb(["requester@example.com"])
    monkeypatch.setattr(email_blocklist, "db", fake_db)

    assert await tickets_service.blocked_reply_recipients(None, []) == []
    assert fake_db.calls == 0


@pytest.mark.anyio
async def test_lookup_failure_does_not_break_rendering(monkeypatch):
    class BrokenDb:
        async def fetch_all(self, query, params=None):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(email_blocklist, "db", BrokenDb())

    assert await tickets_service.blocked_reply_recipients("requester@example.com", WATCHERS) == []


def test_reply_form_renders_blocklist_banner():
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")
    banner_index = template.index("data-reply-blocklist-warning")
    assert template.index("data-ticket-reply-error") < banner_index < template.index("data-reply-email-recipients")
    assert "{% if ticket_blocked_reply_recipients %}" in template
    assert 'data-recipient-role="{{ recipient.role }}"' in template


def test_reply_form_script_tracks_blocklist_banner():
    script = Path("app/static/js/ticket_detail.js").read_text(encoding="utf-8")
    assert "[data-reply-blocklist-warning]" in script
    assert "ticket-watcher-removed" in script
