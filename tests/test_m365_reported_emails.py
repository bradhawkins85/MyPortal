from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

from app.services import m365 as m365_service
from app.services import m365_reported_emails as service


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _alert(**overrides):
    alert = {
        "id": "da638123456789_-123456",
        "title": "Email reported by user as malware or phish",
        "severity": "low",
        "status": "new",
        "createdDateTime": "2026-10-01T23:05:10.1234567Z",
        "firstActivityDateTime": "2026-10-01T23:04:00Z",
        "alertWebUrl": "https://security.microsoft.com/alerts/da638123456789_-123456",
        "evidence": [
            {"@odata.type": "#microsoft.graph.security.mailboxEvidence"},
            {
                "@odata.type": "#microsoft.graph.security.analyzedMessageEvidence",
                "p1Sender": {"emailAddress": "service.kJz@breaking87ddb171.tulsipurkhabar.com"},
                "p2Sender": {
                    "emailAddress": "email.DZ3lH@breaking87ddb171.tulsipurkhabar.com",
                    "displayName": "-Storage Service Notice-",
                },
                "subject": 'Your "storage" is full',
                "receivedDateTime": "2026-10-01T23:04:30.5Z",
                "recipientEmailAddress": "Scarlett@example.com.au",
                "networkMessageId": "170b3a24-d8dc-42d2-1a3b-08df20106c5e",
            },
        ],
    }
    alert.update(overrides)
    return alert


def test_summarise_alert_extracts_message_evidence_and_window():
    summary = service.summarise_alert(_alert())
    assert summary["kind"] == "phish"
    assert summary["report_label"] == "Malware or phish"
    assert summary["p1_sender"] == "service.kJz@breaking87ddb171.tulsipurkhabar.com"
    assert summary["p2_sender_name"] == "-Storage Service Notice-"
    assert summary["recipient"] == "Scarlett@example.com.au"
    assert summary["anchor_at"] == datetime(2026, 10, 1, 23, 4, 30, 500000)
    assert summary["window_start"] == datetime(2026, 10, 1, 22, 4, 30)
    assert summary["window_end"] == datetime(2026, 10, 2, 0, 4, 30)


def test_summarise_alert_falls_back_to_alert_time_without_received_time():
    alert = _alert()
    del alert["evidence"][1]["receivedDateTime"]
    summary = service.summarise_alert(alert)
    assert summary["anchor_at"] == datetime(2026, 10, 1, 23, 4)


def test_summarise_alert_drops_non_https_portal_links():
    assert service.summarise_alert(_alert(alertWebUrl="javascript:alert(1)"))["alert_url"] is None


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Email reported by user as malware or phish", "phish"),
        ("Email reported by user as junk", "junk"),
        ("Email reported by user as not junk", "other"),
    ],
)
def test_report_kind(title, kind):
    assert service.report_kind(title) == kind


def test_only_reported_email_titles_are_selected():
    assert service.is_reported_email_alert({"title": "Email reported by user as junk"})
    assert not service.is_reported_email_alert({"title": "eDiscovery search started or exported"})


def test_build_alert_query_uses_sender_subject_and_hour_window():
    query = service.build_alert_query(service.summarise_alert(_alert()))
    assert query == (
        "(Received>=2026-10-01T22:04:30 AND Received<=2026-10-02T00:04:30) AND "
        '(From:"service.kJz@breaking87ddb171.tulsipurkhabar.com") AND '
        '(Subject:"Your \\"storage\\" is full")'
    )


def test_build_alert_query_requires_sender_or_subject():
    alert = _alert(evidence=[])
    with pytest.raises(service.ReportedEmailError, match="no sender or subject"):
        service.build_alert_query(service.summarise_alert(alert))


@pytest.mark.anyio
async def test_list_reported_alerts_filters_titles_and_pages(monkeypatch):
    pages = {
        "first": {
            "value": [_alert(), _alert(id="other", title="eDiscovery search started or exported")],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/security/alerts_v2?$skiptoken=x",
        },
        "second": {"value": [_alert(id="junk", title="Email reported by user as junk",
                                    createdDateTime="2026-10-02T01:00:00Z")]},
    }
    urls: list[str] = []

    async def fake_token(company_id, *, force_client_credentials=False):
        assert force_client_credentials
        return "token"

    async def fake_get(token, url):
        urls.append(url)
        return pages["first"] if len(urls) == 1 else pages["second"]

    monkeypatch.setattr(m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    alerts = await service.list_reported_alerts(4, days=7)
    assert [alert["id"] for alert in alerts] == ["junk", "da638123456789_-123456"]
    assert urls[0].startswith("https://graph.microsoft.com/v1.0/security/alerts_v2?$filter=createdDateTime%20ge%20")


@pytest.mark.anyio
async def test_list_reported_alerts_explains_missing_permission(monkeypatch):
    async def fake_token(company_id, *, force_client_credentials=False, force_refresh=False):
        return "token"

    async def fake_get(token, url):
        raise m365_service.M365Error("Microsoft Graph request failed (403)", http_status=403)

    monkeypatch.setattr(m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    with pytest.raises(service.ReportedEmailError, match="SecurityAlert.Read.All"):
        await service.list_reported_alerts(4)


@pytest.mark.anyio
async def test_list_reported_alerts_retries_403_with_fresh_token(monkeypatch):
    """A role granted after the cached app token was issued needs a new token."""
    refreshes: list[bool] = []

    async def fake_token(company_id, *, force_client_credentials=False, force_refresh=False):
        refreshes.append(force_refresh)
        return "fresh" if force_refresh else "stale"

    async def fake_get(token, url):
        if token == "stale":
            raise m365_service.M365Error("Microsoft Graph request failed (403)", http_status=403)
        return {"value": [_alert()]}

    monkeypatch.setattr(m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    alerts = await service.list_reported_alerts(4)
    assert len(alerts) == 1
    assert refreshes == [False, True]


@pytest.mark.anyio
async def test_get_reported_alert_rejects_unsafe_ids():
    with pytest.raises(LookupError):
        await service.get_reported_alert(4, "../users")


@pytest.mark.anyio
async def test_create_search_from_alert_queues_search_but_never_purges(monkeypatch):
    created: dict = {}

    async def fake_find(company_id, alert_id):
        return None

    async def fake_get(company_id, alert_id):
        return service.summarise_alert(_alert())

    async def fake_create(company_id, user_id, data):
        created.update(data)
        return {"id": 11, "company_id": company_id}

    async def fake_start(request_id, company_id):
        return {"id": request_id, "search_status": "queued", "purge_status": "not_started",
                "content_match_query": created["query_override"]}

    async def fail_purge(*args, **kwargs):
        raise AssertionError("purge must not start")

    monkeypatch.setattr(service.purge_repo, "find_by_source_alert", fake_find)
    monkeypatch.setattr(service, "get_reported_alert", fake_get)
    monkeypatch.setattr(service.purge_service, "create_request", fake_create)
    monkeypatch.setattr(service.purge_service, "start_search", fake_start)
    monkeypatch.setattr(service.purge_service, "start_purge", fail_purge)

    request, was_created = await service.create_search_from_alert(4, 9, "da638123456789_-123456")
    assert was_created
    assert request["search_status"] == "queued"
    assert created["source_alert_id"] == "da638123456789_-123456"
    assert created["sender"] == "service.kJz@breaking87ddb171.tulsipurkhabar.com"
    assert created["received_from"].isoformat() == "2026-10-01"
    assert created["received_to"].isoformat() == "2026-10-02"
    assert created["query_override"].startswith("(Received>=2026-10-01T22:04:30")


@pytest.mark.anyio
async def test_create_search_from_alert_reuses_existing_request(monkeypatch):
    async def fake_find(company_id, alert_id):
        return {"id": 3, "source_alert_id": alert_id}

    async def fail_get(*args, **kwargs):
        raise AssertionError("existing search must be reused")

    monkeypatch.setattr(service.purge_repo, "find_by_source_alert", fake_find)
    monkeypatch.setattr(service, "get_reported_alert", fail_get)
    request, was_created = await service.create_search_from_alert(4, 9, "abc")
    assert request["id"] == 3
    assert not was_created


@pytest.mark.anyio
async def test_spam_purge_create_request_uses_query_override(monkeypatch):
    from app.services import m365_spam_purge as purge_service

    stored: dict = {}

    async def fake_create(data):
        stored.update(data)
        return data

    monkeypatch.setattr(purge_service.purge_repo, "create_request", fake_create)
    await purge_service.create_request(4, 9, {
        "sender": "a@example.com", "query_override": "(From:\"a@example.com\")",
        "source_alert_id": "abc",
    })
    assert stored["content_match_query"] == '(From:"a@example.com")'
    assert stored["source_alert_id"] == "abc"


def test_reported_emails_template_renders_alert_rows():
    stub_base = "{% block header_actions %}{% endblock %}{% block styles %}{% endblock %}{% block content %}{% endblock %}"
    env = Environment(
        loader=ChoiceLoader([DictLoader({"base.html": stub_base}), FileSystemLoader(TEMPLATES_DIR)]),
        autoescape=select_autoescape(("html",)),
    )
    template = env.get_template("m365/reported_emails.html")
    alert = service.summarise_alert(_alert())
    alert["search"] = None
    junk = service.summarise_alert(_alert(id="junk", title="Email reported by user as junk"))
    junk["search"] = {"search_status": "completed", "matched_items": 4, "purge_status": "not_started"}
    body = template.render(
        alerts=[alert, junk], load_error=None, kind="all", days=30, csrf_token="tok",
    )
    assert 'action="/m365/reported-emails/da638123456789_-123456/search"' in body
    assert "2026-10-01 22:04" in body and "2026-10-02 00:04" in body
    assert "4 matched" in body
    assert "Review search" in body


def _submission(**overrides):
    item = {
        "@odata.type": "#microsoft.graph.security.emailContentThreatSubmission",
        "id": "49c5ef5b-1f65-444a-e6b9-08d772ea2059",
        "category": "phishing",
        "source": "user",
        "status": "succeeded",
        "createdDateTime": "2026-10-01T23:05:00Z",
        "receivedDateTime": "2026-10-01T23:04:00Z",
        "recipientEmailAddress": "Scarlett@example.com.au",
        "sender": "Storage Notice <service.kJz@breaking87ddb171.tulsipurkhabar.com>",
        "subject": "Storage full",
        "internetMessageId": "<abc@mail.example>",
        "createdBy": {"user": {"email": "scarlett@example.com.au"}},
    }
    item.update(overrides)
    return item


def test_summarise_submission_maps_category_sender_and_window():
    summary = service.summarise_submission(_submission())
    assert summary["id"] == "submission:49c5ef5b-1f65-444a-e6b9-08d772ea2059"
    assert summary["source"] == "submission"
    assert summary["kind"] == "phish"
    assert summary["report_label"] == "Phish"
    assert summary["p1_sender"] == "service.kJz@breaking87ddb171.tulsipurkhabar.com"
    assert summary["window_start"] == datetime(2026, 10, 1, 22, 4)
    assert summary["window_end"] == datetime(2026, 10, 2, 0, 4)
    assert service.build_alert_query(summary).startswith(
        '(Received>=2026-10-01T22:04:00 AND Received<=2026-10-02T00:04:00) AND '
        '(From:"service.kJz@breaking87ddb171.tulsipurkhabar.com")'
    )


@pytest.mark.parametrize(
    ("category", "kind", "label"),
    [("spam", "junk", "Junk"), ("malware", "phish", "Malware"), ("notJunk", "other", "Not junk")],
)
def test_summarise_submission_categories(category, kind, label):
    summary = service.summarise_submission(_submission(category=category))
    assert (summary["kind"], summary["report_label"]) == (kind, label)


def _fake_graph(monkeypatch, responses, audit_rows=None):
    calls: list[str] = []
    exo_calls: list[tuple[str, dict]] = []

    async def fake_exo_token(company_id):
        return "exo-token", "tenant-id"

    async def fake_exo(token, tenant, cmdlet, params=None):
        exo_calls.append((cmdlet, params or {}))
        if isinstance(audit_rows, Exception):
            raise audit_rows
        return {"value": list(audit_rows or [])}

    monkeypatch.setattr(m365_service, "_acquire_exo_access_token", fake_exo_token)
    monkeypatch.setattr(m365_service, "_exo_invoke_command", fake_exo)
    monkeypatch.setattr(service, "_exo_calls", exo_calls, raising=False)

    async def fake_token(company_id, *, force_client_credentials=False, force_refresh=False):
        return "token"

    async def fake_get(token, url):
        calls.append(url)
        for prefix, response in responses:
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(url)

    monkeypatch.setattr(m365_service, "acquire_access_token", fake_token)
    monkeypatch.setattr(m365_service, "_graph_get", fake_get)
    return calls


def _recent(value: str) -> str:
    """Shift a fixture timestamp to yesterday so the lookback filter keeps it."""
    return (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT") + value


@pytest.mark.anyio
async def test_load_reported_emails_uses_submissions_when_alerts_are_empty(monkeypatch):
    """Plan 1 tenants: alerts_v2 has nothing, submissions hold the user reports."""
    _fake_graph(monkeypatch, [
        (service._ALERTS_URL, {"value": []}),
        (service._SUBMISSIONS_URL, {"value": [
            _submission(createdDateTime=_recent("23:05:00Z")),
            _submission(id="admin", source="administrator", createdDateTime=_recent("23:05:00Z")),
        ]}),
    ])
    items, warnings = await service.load_reported_emails(4, days=7)
    assert [item["id"] for item in items] == ["submission:49c5ef5b-1f65-444a-e6b9-08d772ea2059"]
    assert warnings == []


@pytest.mark.anyio
async def test_load_reported_emails_prefers_alert_over_matching_submission(monkeypatch):
    alert = _alert(createdDateTime=_recent("23:05:10Z"))
    alert["evidence"][1]["internetMessageId"] = "<ABC@mail.example>"
    _fake_graph(monkeypatch, [
        (service._ALERTS_URL, {"value": [alert]}),
        (service._SUBMISSIONS_URL, {"value": [_submission(createdDateTime=_recent("23:06:00Z"))]}),
    ])
    items, _ = await service.load_reported_emails(4, days=7)
    assert [item["source"] for item in items] == ["alert"]


@pytest.mark.anyio
async def test_load_reported_emails_reports_a_failed_source_as_warning(monkeypatch):
    _fake_graph(monkeypatch, [
        (service._ALERTS_URL, {"value": []}),
        (service._SUBMISSIONS_URL, m365_service.M365Error("Microsoft Graph request failed (403)", http_status=403)),
    ])
    items, warnings = await service.load_reported_emails(4)
    assert items == []
    assert len(warnings) == 1 and "ThreatSubmission.Read.All" in warnings[0]
    assert warnings[0].startswith("Threat submissions:")


@pytest.mark.anyio
async def test_load_reported_emails_raises_when_every_source_fails(monkeypatch):
    error = m365_service.M365Error("Microsoft Graph request failed (500)", http_status=500)
    _fake_graph(
        monkeypatch, [(service._ALERTS_URL, error), (service._SUBMISSIONS_URL, error)],
        audit_rows=m365_service.M365Error("Exchange Online failed (500)", http_status=500),
    )
    with pytest.raises(service.ReportedEmailError):
        await service.load_reported_emails(4)


@pytest.mark.anyio
async def test_list_reported_submissions_retries_without_rejected_filter(monkeypatch):
    calls = _fake_graph(monkeypatch, [
        (service._SUBMISSIONS_URL + "?", m365_service.M365Error("bad filter", http_status=400)),
        (service._SUBMISSIONS_URL, {"value": [
            _submission(createdDateTime=_recent("23:05:00Z")),
            _submission(id="old", createdDateTime="2020-01-01T00:00:00Z"),
        ]}),
    ])
    items = await service.list_reported_submissions(4, days=7)
    assert [item["id"] for item in items] == ["submission:49c5ef5b-1f65-444a-e6b9-08d772ea2059"]
    assert calls[-1] == service._SUBMISSIONS_URL


@pytest.mark.anyio
async def test_get_reported_alert_loads_submission_by_prefixed_id(monkeypatch):
    calls = _fake_graph(monkeypatch, [(service._SUBMISSIONS_URL + "/", _submission())])
    summary = await service.get_reported_alert(4, "submission:49c5ef5b-1f65-444a-e6b9-08d772ea2059")
    assert summary["source"] == "submission"
    assert calls == [service._SUBMISSIONS_URL + "/49c5ef5b-1f65-444a-e6b9-08d772ea2059"]


def test_reported_emails_template_shows_source_warnings():
    stub_base = "{% block header_actions %}{% endblock %}{% block styles %}{% endblock %}{% block content %}{% endblock %}"
    env = Environment(
        loader=ChoiceLoader([DictLoader({"base.html": stub_base}), FileSystemLoader(TEMPLATES_DIR)]),
        autoescape=select_autoescape(("html",)),
    )
    row = service.summarise_submission(_submission())
    row["search"] = None
    body = env.get_template("m365/reported_emails.html").render(
        alerts=[row], load_error=None, load_warnings=["Submissions unavailable"], kind="all", days=30,
    )
    assert "Submissions unavailable" in body
    assert 'action="/m365/reported-emails/submission%3A49c5ef5b-1f65-444a-e6b9-08d772ea2059/search"' in body
    assert ">Submissions</a>" in body


def _audit_record(**overrides):
    data = {
        "CreationTime": "2026-10-01T23:05:10",
        "Id": "4278982f-c627-443e-63f7-08df201071be",
        "Operation": "UserSubmission",
        "RecordType": 29,
        "UserId": "Scarlett@example.com.au",
        "MessageDate": "2026-10-01T23:04:00",
        "P1Sender": "service.kJz@breaking87ddb171.tulsipurkhabar.com",
        "P2Sender": '"-Storage Service Notice-" <email.DZ3lH@breaking87ddb171.tulsipurkhabar.com>',
        "Recipients": ["Scarlett@example.com.au"],
        "Subject": "Storage full",
        "SubmissionContent": [
            {"Name": "SubmissionType", "Value": "Phish"},
            {"Name": "SubmissionSource", "Value": "Microsoft"},
            {"Name": "OriginalVerdict", "Value": "NotSpam"},
        ],
        "RescanResult": {"RescanVerdict": "Phish"},
    }
    data.update(overrides)
    return {"CreationDate": data["CreationTime"], "UserIds": data["UserId"], "AuditData": json.dumps(data)}


def test_summarise_audit_record_reads_user_submission():
    summary = service.summarise_audit_record(_audit_record())
    assert summary["source"] == "audit"
    assert summary["id"] == "audit:4278982f-c627-443e-63f7-08df201071be:" + str(
        int(datetime(2026, 10, 1, 23, 5, 10, tzinfo=timezone.utc).timestamp())
    )
    assert summary["kind"] == "phish" and summary["report_label"] == "Phish"
    assert summary["p1_sender"] == "service.kJz@breaking87ddb171.tulsipurkhabar.com"
    assert summary["p2_sender"] == "email.DZ3lH@breaking87ddb171.tulsipurkhabar.com"
    assert summary["p2_sender_name"] == "-Storage Service Notice-"
    assert summary["recipient"] == "Scarlett@example.com.au"
    assert summary["window_start"] == datetime(2026, 10, 1, 22, 4)
    assert summary["window_end"] == datetime(2026, 10, 2, 0, 4)


@pytest.mark.parametrize(
    ("value", "kind", "label"),
    [("Junk", "junk", "Junk"), ("NotJunk", "other", "Not junk"), ("Malware", "phish", "Malware")],
)
def test_summarise_audit_record_category_ignores_microsoft_verdicts(value, kind, label):
    record = _audit_record(SubmissionContent=[
        {"Name": "OriginalVerdict", "Value": "Phish"},
        {"Name": "SubmissionType", "Value": value},
    ])
    summary = service.summarise_audit_record(record)
    assert (summary["kind"], summary["report_label"]) == (kind, label)


@pytest.mark.anyio
async def test_load_reported_emails_uses_audit_log_on_business_standard(monkeypatch):
    """No Defender for Office 365: Graph sources fail or are empty, audit has the report."""
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT")
    _fake_graph(
        monkeypatch,
        [
            (service._ALERTS_URL, {"value": []}),
            (service._SUBMISSIONS_URL, m365_service.M365Error("Microsoft Graph request failed (403)", http_status=403)),
        ],
        audit_rows=[
            _audit_record(CreationTime=yesterday + "23:05:10", MessageDate=yesterday + "23:04:00"),
            _audit_record(CreationTime=yesterday + "23:05:10", MessageDate=yesterday + "23:04:00"),
        ],
    )
    items, warnings = await service.load_reported_emails(4, days=7)
    assert [item["source"] for item in items] == ["audit"]
    assert len(warnings) == 1
    cmdlet, params = service._exo_calls[0]
    assert cmdlet == "Search-UnifiedAuditLog"
    assert params["Operations"] == ["UserSubmission"]
    assert re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4} \d{2}:\d{2}:\d{2}", params["StartDate"])


@pytest.mark.anyio
async def test_load_reported_emails_drops_audit_record_matching_submission(monkeypatch):
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%dT")
    _fake_graph(
        monkeypatch,
        [
            (service._ALERTS_URL, {"value": []}),
            (service._SUBMISSIONS_URL, {"value": [_submission(
                createdDateTime=yesterday + "23:05:00Z", receivedDateTime=yesterday + "23:04:00Z",
            )]}),
        ],
        audit_rows=[_audit_record(CreationTime=yesterday + "23:05:10", MessageDate=yesterday + "23:04:00")],
    )
    items, warnings = await service.load_reported_emails(4, days=7)
    assert [item["source"] for item in items] == ["submission"]
    assert warnings == []


@pytest.mark.anyio
async def test_get_reported_alert_finds_audit_record_near_its_creation_time(monkeypatch):
    record = _audit_record()
    summary_id = service.summarise_audit_record(record)["id"]
    _fake_graph(monkeypatch, [], audit_rows=[_audit_record(Id="other"), record])
    summary = await service.get_reported_alert(4, summary_id)
    assert summary["id"] == summary_id
    _, params = service._exo_calls[0]
    assert params["StartDate"] == "10/1/2026 22:55:10"
    assert params["EndDate"] == "10/1/2026 23:15:10"


@pytest.mark.anyio
async def test_get_reported_alert_rejects_malformed_audit_reference(monkeypatch):
    _fake_graph(monkeypatch, [], audit_rows=[])
    with pytest.raises(LookupError):
        await service.get_reported_alert(4, "audit:no-timestamp")


def test_reported_emails_template_collapses_warnings_when_rows_exist():
    stub_base = "{% block header_actions %}{% endblock %}{% block styles %}{% endblock %}{% block content %}{% endblock %}"
    env = Environment(
        loader=ChoiceLoader([DictLoader({"base.html": stub_base}), FileSystemLoader(TEMPLATES_DIR)]),
        autoescape=select_autoescape(("html",)),
    )
    row = service.summarise_audit_record(_audit_record())
    row["search"] = None
    body = env.get_template("m365/reported_emails.html").render(
        alerts=[row], load_error=None, load_warnings=["Defender alerts: x", "Threat submissions: y"],
        kind="all", days=30,
    )
    assert "2 report sources unavailable for this tenant" in body
    assert 'class="alert alert--warning"' not in body
    assert "Reported by Scarlett@example.com.au" in body


@pytest.mark.anyio
async def test_search_audit_retries_by_record_type_after_invalid_operation(monkeypatch):
    """Exchange answered the Operations filter with 400 Invalid Operation."""
    calls: list[dict] = []

    async def fake_exo_token(company_id):
        return "exo-token", "tenant-id"

    async def fake_exo(token, tenant, cmdlet, params=None):
        calls.append(params)
        if "Operations" in params:
            raise m365_service.M365Error(
                "Exchange Online Search-UnifiedAuditLog failed (400): Invalid Operation", http_status=400,
            )
        other = _audit_record(Operation="AdminSubmission", Id="other")
        other["Operations"] = "AdminSubmission"
        return {"value": [_audit_record(), other]}

    monkeypatch.setattr(m365_service, "_acquire_exo_access_token", fake_exo_token)
    monkeypatch.setattr(m365_service, "_exo_invoke_command", fake_exo)
    rows = await service._search_audit(4, datetime(2026, 10, 1, 22), datetime(2026, 10, 2, 0))
    assert [service._audit_data(row)["Id"] for row in rows] == ["4278982f-c627-443e-63f7-08df201071be"]
    assert calls[1]["RecordType"] == "MailSubmission" and "Operations" not in calls[1]
    assert calls[1]["StartDate"] == "10/1/2026 22:00:00"


@pytest.mark.anyio
async def test_search_audit_reports_error_when_both_filters_fail(monkeypatch):
    async def fake_exo_token(company_id):
        return "exo-token", "tenant-id"

    async def fake_exo(token, tenant, cmdlet, params=None):
        raise m365_service.M365Error("Exchange Online Search-UnifiedAuditLog failed (400): Invalid Operation", http_status=400)

    monkeypatch.setattr(m365_service, "_acquire_exo_access_token", fake_exo_token)
    monkeypatch.setattr(m365_service, "_exo_invoke_command", fake_exo)
    with pytest.raises(service.ReportedEmailError, match="Invalid Operation"):
        await service._search_audit(4, datetime(2026, 10, 1), datetime(2026, 10, 2))


def _render_reported(**context):
    stub_base = "{% block header_actions %}{% endblock %}{% block styles %}{% endblock %}{% block content %}{% endblock %}"
    env = Environment(
        loader=ChoiceLoader([DictLoader({"base.html": stub_base}), FileSystemLoader(TEMPLATES_DIR)]),
        autoescape=select_autoescape(("html",)),
    )
    values = {"load_error": None, "kind": "all", "days": 30, "csrf_token": "tok"}
    values.update(context)
    return env.get_template("m365/reported_emails.html").render(**values)


def test_reported_emails_template_offers_ignore_and_restore():
    active = service.summarise_alert(_alert())
    active.update(search=None, ignored=False)
    ignored = service.summarise_alert(_alert(id="junk", title="Email reported by user as junk"))
    ignored.update(search=None, ignored=True)
    body = _render_reported(alerts=[active, ignored], ignored="show", ignored_count=1)
    assert 'action="/m365/reported-emails/da638123456789_-123456/ignore"' in body
    assert 'action="/m365/reported-emails/junk/unignore"' in body
    assert ">Ignore</button>" in body and ">Restore</button>" in body
    assert 'name="ignored" value="show"' in body
    assert '<option value="show" selected>' in body


def test_reported_emails_template_notes_hidden_ignored_reports():
    body = _render_reported(alerts=[], ignored="hide", ignored_count=2)
    assert "2 ignored reports hidden" in body
    assert "ignored=show" in body
    assert "Every report in this period is ignored." in body


def test_reported_filters_default_to_hiding_ignored_reports():
    from app.features.m365_admin import routes

    assert routes._reported_filters({}) == ("all", service.DEFAULT_LOOKBACK_DAYS, "hide")
    assert routes._reported_filters({"kind": "JUNK", "days": "7", "ignored": "only"}) == ("junk", 7, "only")
    assert routes._reported_filters({"kind": "x", "days": "abc", "ignored": "x"})[::2] == ("all", "hide")
    assert routes._reported_emails_url("phish", 14, "show") == "/m365/reported-emails?kind=phish&days=14&ignored=show"


class _FakeForm(dict):
    pass


class _FakeRequest:
    def __init__(self, form):
        self._form = _FakeForm(form)
        self.state = type("State", (), {"active_company_id": 7})()

    async def form(self):
        return self._form


@pytest.mark.anyio
@pytest.mark.parametrize("ignore", [True, False])
async def test_ignore_routes_store_audit_and_return_to_filters(monkeypatch, ignore):
    from app.features.m365_admin import routes

    calls = []

    async def fake_context(request):
        return {"id": 3}, 7, None

    async def fake_ignore(company_id, alert_id, user_id):
        calls.append(("ignore", company_id, alert_id, user_id))
        return True

    async def fake_unignore(company_id, alert_id):
        calls.append(("unignore", company_id, alert_id))

    async def fake_record(**kwargs):
        calls.append(("audit", kwargs["action"], kwargs["after"]))

    monkeypatch.setattr(routes, "_context", fake_context)
    monkeypatch.setattr(routes.ignores_repo, "ignore", fake_ignore)
    monkeypatch.setattr(routes.ignores_repo, "unignore", fake_unignore)
    monkeypatch.setattr(routes.audit_service, "record", fake_record)

    handler = routes.ignore_reported_email if ignore else routes.unignore_reported_email
    response = await handler("submission:abc", _FakeRequest({"kind": "junk", "days": "7", "ignored": "show"}))

    assert response.headers["location"] == "/m365/reported-emails?kind=junk&days=7&ignored=show"
    if ignore:
        assert calls[0] == ("ignore", 7, "submission:abc", 3)
        assert calls[1] == ("audit", "m365.reported_email.ignore", {"alert_id": "submission:abc", "ignored": True})
    else:
        assert calls[0] == ("unignore", 7, "submission:abc")
        assert calls[1][1] == "m365.reported_email.unignore"


@pytest.mark.anyio
async def test_ignore_route_rejects_malformed_alert_id(monkeypatch):
    from app.features.m365_admin import routes

    async def fake_context(request):
        return {"id": 3}, 7, None

    async def fail(*args, **kwargs):
        raise AssertionError("must not store an invalid alert id")

    monkeypatch.setattr(routes, "_context", fake_context)
    monkeypatch.setattr(routes.ignores_repo, "ignore", fail)

    response = await routes.ignore_reported_email("../bad id", _FakeRequest({}))
    assert response.headers["location"] == "/m365/reported-emails?kind=all&days=30&ignored=hide".replace(
        "days=30", f"days={service.DEFAULT_LOOKBACK_DAYS}"
    )
