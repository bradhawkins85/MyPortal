from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.m365_out_of_office import OutOfOfficeCreate
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
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_patch", fake_patch)

    result = await m365_out_of_office.set_automatic_replies(
        7, _payload(same_message=False, external_message="External reply")
    )

    assert result == [{"mailbox": "one@example.com", "success": True, "error": None}]
    setting = calls[0][2]["automaticRepliesSetting"]
    assert setting["internalReplyMessage"] == "We are closed."
    assert setting["externalReplyMessage"] == "External reply"
    assert setting["scheduledStartDateTime"] == {
        "dateTime": "2026-12-20T05:00:00", "timeZone": "UTC"
    }


@pytest.mark.anyio
async def test_rejects_mailbox_not_in_company_cache(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        return []

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    with pytest.raises(ValueError, match="Unknown user mailbox"):
        await m365_out_of_office.set_automatic_replies(7, _payload())
