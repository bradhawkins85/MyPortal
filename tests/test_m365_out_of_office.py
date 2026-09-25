from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.m365_out_of_office import OutOfOfficeCreate, OutOfOfficeDisable
from app.services import m365_out_of_office


def anyio_backend():
    return "asyncio"


def _payload(**overrides):
    values = {
        "mailboxes": ["one@example.com"],
        "start_time": datetime(2026, 12, 20, 5, tzinfo=timezone.utc),
        "end_time": datetime(2027, 1, 5, 13, tzinfo=timezone.utc),
        "internal_message": "We are closed.",
        "same_message": True,
    }
    values.update(overrides)
    return OutOfOfficeCreate(**values)


def test_payload_requires_aware_ordered_times_and_external_message():
    with pytest.raises(ValidationError, match="include a timezone"):
        _payload(start_time=datetime(2026, 12, 20, 5))
    with pytest.raises(ValidationError, match="after start time"):
        _payload(end_time=datetime(2026, 12, 20, 4, tzinfo=timezone.utc))
    with pytest.raises(ValidationError, match="External message is required"):
        _payload(same_message=False)


@pytest.mark.anyio
async def test_sets_separate_messages_for_only_cached_user_mailboxes(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        assert (company_id, mailbox_type) == (7, "UserMailbox")
        return [{"user_principal_name": "one@example.com"}]

    async def fake_token(company_id, *, force_client_credentials=False):
        assert company_id == 7
        assert force_client_credentials is True
        return "token"

    calls = []

    async def fake_patch(token, url, body):
        calls.append((token, url, body))
        return {}

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(
        m365_out_of_office.companies_repo,
        "get_email_domains_for_company",
        lambda company_id: _async_value(["example.com"]),
    )
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_patch", fake_patch)

    result = await m365_out_of_office.set_automatic_replies(
        7, _payload(same_message=False, external_message="External reply")
    )

    assert result == [{"mailbox": "one@example.com", "success": True, "error": None}]
    setting = calls[0][2]["automaticRepliesSetting"]
    assert setting["internalReplyMessage"] == "We are closed."
    assert setting["externalReplyMessage"] == "External reply"
    assert setting["externalAudience"] == "none"
    assert setting["scheduledStartDateTime"] == {
        "dateTime": "2026-12-20T05:00:00", "timeZone": "UTC"
    }


@pytest.mark.anyio
async def test_rejects_mailbox_not_in_company_cache(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        return []

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(
        m365_out_of_office.companies_repo,
        "get_email_domains_for_company",
        lambda company_id: _async_value(["example.com"]),
    )
    with pytest.raises(ValueError, match="Unknown user mailbox"):
        await m365_out_of_office.set_automatic_replies(7, _payload())


@pytest.mark.anyio
async def test_reads_state_and_retains_per_mailbox_failure(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        return [{"user_principal_name": "one@example.com"}, {"user_principal_name": "two@example.com"}]

    async def fake_get(token, url):
        if "two%40example.com" in url:
            raise m365_out_of_office.m365_service.M365Error("denied")
        return {"automaticRepliesSetting": {"status": "scheduled", "externalAudience": "contactsOnly"}}

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(m365_out_of_office.companies_repo, "get_email_domains_for_company", lambda company_id: _async_value(["example.com"]))
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", lambda *args, **kwargs: _async_value("token"))
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_get", fake_get)

    result = await m365_out_of_office.get_automatic_replies(7)
    assert result[0]["setting"]["externalAudience"] == "contactsOnly"
    assert result[1] == {"mailbox": "two@example.com", "success": False, "setting": None, "error": "denied"}


@pytest.mark.anyio
async def test_disable_only_changes_status_and_keeps_partial_results(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        return [{"user_principal_name": "one@example.com"}, {"user_principal_name": "two@example.com"}]

    calls = []
    async def fake_patch(token, url, body):
        calls.append(body)
        if "two%40example.com" in url:
            raise m365_out_of_office.m365_service.M365Error("write failed")

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(m365_out_of_office.companies_repo, "get_email_domains_for_company", lambda company_id: _async_value(["example.com"]))
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", lambda *args, **kwargs: _async_value("token"))
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_patch", fake_patch)

    result = await m365_out_of_office.disable_automatic_replies(7, OutOfOfficeDisable(mailboxes=["one@example.com", "two@example.com"]))
    assert calls == [{"automaticRepliesSetting": {"status": "disabled"}}] * 2
    assert [item["success"] for item in result] == [True, False]


def test_non_utc_input_is_normalized_across_daylight_saving_offset():
    from zoneinfo import ZoneInfo
    payload = _payload(
        start_time=datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("Europe/London")),
        end_time=datetime(2026, 3, 29, 3, 30, tzinfo=ZoneInfo("Europe/London")),
    )
    assert payload.start_time.isoformat() == "2026-03-29T01:30:00+00:00"
    assert payload.end_time.isoformat() == "2026-03-29T02:30:00+00:00"


async def _async_value(value):
    return value


@pytest.mark.anyio
async def test_selectable_mailboxes_only_include_company_email_domains(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        assert (company_id, mailbox_type) == (7, "UserMailbox")
        return [
            {"display_name": "Allowed", "user_principal_name": "allowed@Example.com"},
            {"display_name": "Other tenant", "user_principal_name": "user@other.com"},
            {"display_name": "Invalid", "user_principal_name": "not-an-email"},
        ]

    async def fake_domains(company_id):
        assert company_id == 7
        return ["example.com"]

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(
        m365_out_of_office.companies_repo, "get_email_domains_for_company", fake_domains
    )

    result = await m365_out_of_office.get_selectable_mailboxes(7)

    assert result == [
        {"display_name": "Allowed", "user_principal_name": "allowed@Example.com"}
    ]


@pytest.mark.anyio
async def test_rejects_cached_mailbox_outside_company_email_domains(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        return [{"user_principal_name": "one@example.com"}]

    async def fake_domains(company_id):
        return ["different.example"]

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(
        m365_out_of_office.companies_repo, "get_email_domains_for_company", fake_domains
    )

    with pytest.raises(ValueError, match="Unknown user mailbox"):
        await m365_out_of_office.set_automatic_replies(7, _payload())
