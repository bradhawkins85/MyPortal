import asyncio
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.m365_out_of_office import OutOfOfficeCreate, OutOfOfficeDisable
from app.services import m365_out_of_office


def anyio_backend():
    return "asyncio"


def _fake_batch(handler, calls=None):
    """Return a fake ``_graph_post`` that answers Graph $batch calls.

    ``handler(method, url, body)`` returns ``(status, body)`` for each request.
    """

    async def fake_post(token, url, payload):
        assert token == "token"
        assert url == "https://graph.microsoft.com/v1.0/$batch"
        assert len(payload["requests"]) <= 20
        if calls is not None:
            calls.append(payload["requests"])
        responses = []
        for request in payload["requests"]:
            status, body = handler(request["method"], request["url"], request.get("body"))
            responses.append({"id": request["id"], "status": status, "body": body, "headers": {}})
        return {"responses": list(reversed(responses))}

    return fake_post


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
        _payload(same_message=False, external_audience="contactsOnly")


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

    def handler(method, url, body):
        calls.append((method, url, body))
        return 204, None

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(
        m365_out_of_office.companies_repo,
        "get_email_domains_for_company",
        lambda company_id: _async_value(["example.com"]),
    )
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", _fake_batch(handler))

    result = await m365_out_of_office.set_automatic_replies(
        7, _payload(same_message=False, external_message="External reply")
    )

    assert result == [{"mailbox": "one@example.com", "success": True, "error": None}]
    assert calls[0][:2] == ("PATCH", "/users/one%40example.com/mailboxSettings")
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

    def fake_get(method, url, body):
        assert method == "GET"
        if "two%40example.com" in url:
            return 403, {"error": {"code": "ErrorAccessDenied", "message": "denied"}}
        return 200, {"automaticRepliesSetting": {"status": "scheduled", "externalAudience": "contactsOnly"}}

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(m365_out_of_office.companies_repo, "get_email_domains_for_company", lambda company_id: _async_value(["example.com"]))
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", lambda *args, **kwargs: _async_value("token"))
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", _fake_batch(fake_get))

    result = await m365_out_of_office.get_automatic_replies(7)
    assert result[0]["setting"]["externalAudience"] == "contactsOnly"
    assert result[1] == {"mailbox": "two@example.com", "success": False, "setting": None, "error": "Microsoft Graph request failed (403): denied", "no_mailbox": False}


@pytest.mark.anyio
async def test_disable_only_changes_status_and_keeps_partial_results(monkeypatch):
    async def fake_mailboxes(company_id, mailbox_type):
        return [{"user_principal_name": "one@example.com"}, {"user_principal_name": "two@example.com"}]

    calls = []
    def fake_patch(method, url, body):
        assert method == "PATCH"
        calls.append(body)
        if "two%40example.com" in url:
            return 500, {"error": {"message": "write failed"}}
        return 200, {}

    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", fake_mailboxes)
    monkeypatch.setattr(m365_out_of_office.companies_repo, "get_email_domains_for_company", lambda company_id: _async_value(["example.com"]))
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", lambda *args, **kwargs: _async_value("token"))
    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", _fake_batch(fake_patch))

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


def test_external_message_optional_when_no_external_audience():
    payload = _payload(same_message=False, external_audience="none")
    assert payload.external_message == "We are closed."
    with pytest.raises(ValidationError, match="External message is required"):
        _payload(same_message=False, external_audience="all")


def test_duplicate_mailboxes_are_removed_case_insensitively():
    payload = _payload(mailboxes=["One@example.com", "one@example.com", "two@example.com"])
    assert [str(item).casefold() for item in payload.mailboxes] == [
        "one@example.com", "two@example.com"
    ]
    disable = OutOfOfficeDisable(mailboxes=["A@example.com", "a@example.com"])
    assert len(disable.mailboxes) == 1


def test_plain_text_replies_keep_line_breaks_as_html():
    assert m365_out_of_office._as_reply_html("Closed <today> & tomorrow\r\nBack Monday") == (
        "Closed &lt;today&gt; &amp; tomorrow<br>\nBack Monday"
    )
    existing = "<html><body><div>Closed</div></body></html>"
    assert m365_out_of_office._as_reply_html(existing) == existing


def test_known_html_heading_tag_is_preserved():
    existing = "<h2>Closed for holidays</h2>"
    assert m365_out_of_office._as_reply_html(existing) == existing


def test_large_angle_bracket_input_without_markup_is_escaped():
    message = "<" + (" " * 4000) + ("a" * 4000)
    rendered = m365_out_of_office._as_reply_html(message)
    assert rendered.startswith("&lt;")
    assert "<br>" not in rendered


def _many_mailboxes(count):
    async def fake_mailboxes(company_id, mailbox_type):
        return [{"user_principal_name": f"user{index}@example.com"} for index in range(count)]
    return fake_mailboxes


def _patch_common(monkeypatch, mailboxes):
    monkeypatch.setattr(m365_out_of_office.m365_repo, "get_mailboxes", mailboxes)
    monkeypatch.setattr(m365_out_of_office.companies_repo, "get_email_domains_for_company", lambda company_id: _async_value(["example.com"]))
    monkeypatch.setattr(m365_out_of_office.m365_service, "acquire_access_token", lambda *args, **kwargs: _async_value("token"))


@pytest.mark.anyio
async def test_reads_many_mailboxes_in_batches_of_twenty_preserving_order(monkeypatch):
    _patch_common(monkeypatch, _many_mailboxes(45))
    calls = []

    def handler(method, url, body):
        mailbox = url.split("/")[2].replace("%40", "@")
        return 200, {"automaticRepliesSetting": {"status": "disabled", "who": mailbox}}

    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", _fake_batch(handler, calls))

    result = await m365_out_of_office.get_automatic_replies(7)

    assert sorted(len(batch) for batch in calls) == [5, 20, 20]
    assert [item["mailbox"] for item in result] == [f"user{index}@example.com" for index in range(45)]
    assert all(item["setting"]["who"] == item["mailbox"] for item in result)


@pytest.mark.anyio
async def test_throttled_batch_items_are_retried_once(monkeypatch):
    _patch_common(monkeypatch, _many_mailboxes(2))
    attempts = {}

    def handler(method, url, body):
        attempts[url] = attempts.get(url, 0) + 1
        if "user1" in url and attempts[url] == 1:
            return 429, {"error": {"message": "throttled"}}
        return 200, {"automaticRepliesSetting": {"status": "alwaysEnabled"}}

    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", _fake_batch(handler))
    monkeypatch.setattr(m365_out_of_office, "_MAX_RETRY_DELAY_SECONDS", 0)

    result = await m365_out_of_office.get_automatic_replies(7)

    assert [item["success"] for item in result] == [True, True]
    assert sorted(attempts.values()) == [1, 2]


@pytest.mark.anyio
async def test_failed_batch_call_marks_only_its_mailboxes_failed(monkeypatch):
    _patch_common(monkeypatch, _many_mailboxes(25))
    ok = _fake_batch(lambda method, url, body: (200, {"automaticRepliesSetting": {}}))

    async def fake_post(token, url, payload):
        if any("user0%40" in request["url"] for request in payload["requests"]):
            raise m365_out_of_office.m365_service.M365Error("batch down")
        return await ok(token, url, payload)

    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", fake_post)

    result = await m365_out_of_office.get_automatic_replies(7)

    assert [item["success"] for item in result] == [False] * 20 + [True] * 5
    assert result[0]["error"] == "batch down"


@pytest.mark.anyio
async def test_slow_reads_stop_at_time_budget_so_page_still_renders(monkeypatch):
    _patch_common(monkeypatch, _many_mailboxes(3))

    async def slow_post(token, url, payload):
        await asyncio.sleep(5)
        return {"responses": []}

    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", slow_post)
    monkeypatch.setattr(m365_out_of_office, "_READ_TIME_BUDGET_SECONDS", 0.05)

    result = await asyncio.wait_for(m365_out_of_office.get_automatic_replies(7), timeout=2)

    assert [item["success"] for item in result] == [False] * 3
    assert result[0]["error"] == m365_out_of_office._READ_TIMEOUT_ERROR


@pytest.mark.anyio
async def test_accounts_without_mailbox_are_flagged(monkeypatch):
    _patch_common(monkeypatch, _many_mailboxes(2))

    def handler(method, url, body):
        if "user1" in url:
            return 404, {"error": {"message": "The mailbox is either inactive, soft-deleted, or is hosted on-premise."}}
        return 200, {"automaticRepliesSetting": {"status": "disabled"}}

    monkeypatch.setattr(m365_out_of_office.m365_service, "_graph_post", _fake_batch(handler))

    result = await m365_out_of_office.get_automatic_replies(7)

    assert [item["no_mailbox"] for item in result] == [False, True]
    assert result[1]["success"] is False
