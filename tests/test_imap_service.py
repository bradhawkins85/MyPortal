from __future__ import annotations

import pytest

from app.services import imap


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_resolve_ticket_entities_matches_company_and_staff(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        assert domain == "example.com"
        return {"id": 5}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        assert company_id == 5
        assert email == "user@example.com"
        return {"id": 42}

    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities("User <user@example.com>")

    assert company_id == 5
    assert requester_id == 42


async def test_resolve_ticket_entities_matches_company_without_staff(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        assert domain == "example.com"
        return {"id": "7"}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        assert company_id == 7
        assert email == "sender@example.com"
        return None

    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities("Sender <sender@example.com>")

    assert company_id == 7
    assert requester_id is None


async def test_resolve_ticket_entities_falls_back_to_account_company(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        return None

    checked: list[tuple[int, str]] = []

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        checked.append((company_id, email))
        if email == "help@tenant.com":
            return {"id": "81"}
        return None

    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities(
        "Support <help@tenant.com>",
        default_company_id="11",
    )

    assert company_id == 11
    assert requester_id == 81
    assert (11, "help@tenant.com") in checked
