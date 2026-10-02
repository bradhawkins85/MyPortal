from __future__ import annotations

from datetime import datetime
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
