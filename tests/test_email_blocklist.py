import pytest

from app.repositories import email_blocklist


class FakeDb:
    def __init__(self):
        self.rows = [{"email": "blocked@example.com"}]
        self.last_query = None
        self.last_params = None

    async def fetch_all(self, query, params=None):
        self.last_query = query
        self.last_params = params or {}
        wanted = set(self.last_params.values())
        return [row for row in self.rows if row["email"] in wanted]


@pytest.mark.anyio
async def test_filter_allowed_removes_blocklisted_addresses(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(email_blocklist, "db", fake_db)

    allowed, blocked = await email_blocklist.filter_allowed([
        "Allowed@Example.com",
        "blocked@example.com",
        "allowed@example.com",
        "BLOCKED@example.com",
    ])

    assert allowed == ["Allowed@Example.com"]
    assert blocked == ["blocked@example.com"]


def test_normalize_email_rejects_invalid_address():
    with pytest.raises(ValueError):
        email_blocklist.normalize_email("not-an-email")
