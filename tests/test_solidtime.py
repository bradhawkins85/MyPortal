"""Unit tests for the Solidtime integration service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.services import solidtime
from app.services.solidtime import (
    SolidtimeAPIError,
    SolidtimeConfigurationError,
    reply_to_time_entry_payload,
    ticket_to_project_payload,
    verify_webhook_signature,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def reset_solidtime_caches(monkeypatch):
    monkeypatch.setattr(solidtime, "_MODULE_SETTINGS_CACHE", None)
    monkeypatch.setattr(solidtime, "_MODULE_SETTINGS_EXPIRY", 0.0)
    monkeypatch.setattr(solidtime, "_RATE_LIMITER_CACHE", None)
    return monkeypatch


# ---------------------------------------------------------------------------
# Conversion utilities
# ---------------------------------------------------------------------------

def test_ticket_to_project_payload_uses_number_and_subject():
    ticket = {
        "id": 7,
        "ticket_number": "T-007",
        "subject": "Email not sending",
        "description": "  please help  ",
        "status": "open",
    }
    body = ticket_to_project_payload(ticket, client_id="client-uuid")
    assert body["name"] == "#T-007 – Email not sending"
    assert body["client_id"] == "client-uuid"
    assert body["color"].startswith("#")
    assert len(body["color"]) == 7
    assert body["description"] == "please help"
    assert body["is_archived"] is False
    assert body["is_billable"] is True


def test_ticket_to_project_payload_archives_closed():
    ticket = {
        "id": 11,
        "ticket_number": None,
        "subject": "All done",
        "status": "closed",
    }
    body = ticket_to_project_payload(ticket)
    assert body["name"] == "#11 – All done"
    assert body["is_archived"] is True
    assert body["client_id"] is None


def test_ticket_to_project_payload_falls_back_when_missing():
    body = ticket_to_project_payload({"id": 3, "subject": ""})
    # Empty subject falls back to "Ticket"
    assert body["name"] == "#3 – Ticket"
    assert body["color"] == ticket_to_project_payload(
        {"id": 3, "subject": "Renamed"}
    )["color"]


def test_reply_to_time_entry_payload_computes_start_from_minutes_and_created_at():
    end_at = datetime(2026, 5, 11, 12, 30, 0, tzinfo=timezone.utc)
    reply = {
        "id": 99,
        "ticket_id": 7,
        "minutes_spent": 45,
        "is_billable": True,
        "body": "<p>Worked on the issue</p>\n<p>second line</p>",
        "created_at": end_at,
    }
    body = reply_to_time_entry_payload(
        reply, {"id": 7}, project_id="proj-uuid", member_id="user-uuid"
    )
    assert body is not None
    assert body["project_id"] == "proj-uuid"
    assert body["member_id"] == "user-uuid"
    assert body["billable"] is True
    # end == created_at; start == end - 45 min
    assert body["end"] == "2026-05-11T12:30:00Z"
    assert body["start"] == "2026-05-11T11:45:00Z"
    assert body["description"] == "Worked on the issue"


def test_reply_to_time_entry_payload_handles_naive_datetime_as_utc():
    end_at = datetime(2026, 5, 11, 9, 0, 0)  # naive
    body = reply_to_time_entry_payload(
        {"minutes_spent": 30, "is_billable": False, "body": "x", "created_at": end_at},
        {"id": 1},
        project_id="p",
    )
    assert body is not None
    assert body["start"] == "2026-05-11T08:30:00Z"
    assert body["end"] == "2026-05-11T09:00:00Z"
    assert body["billable"] is False


def test_reply_to_time_entry_payload_returns_none_for_zero_minutes():
    body = reply_to_time_entry_payload(
        {"minutes_spent": 0, "body": "ignore"},
        {"id": 1},
        project_id="p",
    )
    assert body is None


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def test_verify_webhook_signature_accepts_valid_hmac():
    secret = "shared-secret"
    body = b'{"type":"project.updated"}'
    import hmac
    import hashlib

    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, body, sig) is True
    assert verify_webhook_signature(secret, body, f"sha256={sig}") is True


def test_verify_webhook_signature_rejects_invalid_or_missing():
    secret = "shared-secret"
    body = b"payload"
    assert verify_webhook_signature(secret, body, None) is False
    assert verify_webhook_signature(secret, body, "deadbeef") is False
    # Empty secret never trusts payloads.
    assert verify_webhook_signature("", body, "anything") is False


# ---------------------------------------------------------------------------
# Configuration & request plumbing
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_effective_settings_requires_enabled_module(reset_solidtime_caches):
    async def fake_get_module(slug):
        return None

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)
    with pytest.raises(SolidtimeConfigurationError):
        await solidtime._get_effective_settings()


@pytest.mark.anyio
async def test_get_effective_settings_normalises_base_url(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io/",
                "api_token": "tok-123",
                "organization_id": "org-uuid",
                "rate_limit_per_minute": 200,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)
    settings = await solidtime._get_effective_settings()
    assert settings["base_url"] == "https://app.solidtime.io/api/v1"
    assert settings["api_token"] == "tok-123"
    assert settings["organization_id"] == "org-uuid"


@pytest.mark.anyio
async def test_get_effective_settings_rejects_invalid_url(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "ftp://nope.example",
                "api_token": "tok",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)
    with pytest.raises(SolidtimeConfigurationError):
        await solidtime._get_effective_settings()


def test_solidtime_monitor_target_url_uses_invalid_scheme_fallback():
    assert (
        solidtime._solidtime_monitor_target_url({"base_url": "ftp://bad.example"})
        == "solidtime://invalid-base-url"
    )


@pytest.mark.parametrize(
    ("settings", "sync_result", "expected"),
    [
        (None, None, "Solidtime module is not configured"),
        ({"enabled": False}, None, "Solidtime module is disabled"),
        ({"enabled": True, "base_url": "", "api_token": "tok"}, None, "Solidtime base URL is not configured"),
        ({"enabled": True, "base_url": "https://app.solidtime.io", "api_token": ""}, None, "Solidtime API token is not configured"),
        (
            {"enabled": True, "base_url": "https://app.solidtime.io", "api_token": "tok", "sync_tickets_to_projects": False},
            None,
            "sync_tickets_to_projects is disabled",
        ),
        (
            {"enabled": True, "base_url": "https://app.solidtime.io", "api_token": "tok"},
            None,
            "sync returned no action",
        ),
        (
            {"enabled": True, "base_url": "https://app.solidtime.io", "api_token": "tok"},
            {"ticket_id": 1},
            "ticket synced",
        ),
    ],
)
def test_ticket_sync_outcome_reason_variants(settings, sync_result, expected):
    assert (
        solidtime._ticket_sync_outcome_reason(settings=settings, sync_result=sync_result)
        == expected
    )


@pytest.mark.anyio
async def test_record_ticket_sync_outcome_records_success(reset_solidtime_caches):
    created: list[dict[str, object]] = []
    successes: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    async def fake_create_manual_event(**kwargs):
        created.append(kwargs)
        return {"id": 321}

    async def fake_record_manual_success(event_id, **kwargs):
        successes.append({"event_id": event_id, **kwargs})
        return {"id": event_id}

    async def fake_record_manual_failure(event_id, **kwargs):
        failures.append({"event_id": event_id, **kwargs})
        return {"id": event_id}

    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "create_manual_event", fake_create_manual_event
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_success", fake_record_manual_success
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_failure", fake_record_manual_failure
    )

    await solidtime._record_ticket_sync_outcome(
        ticket_id=9,
        settings={"base_url": "https://app.solidtime.io"},
        status="succeeded",
        reason="ticket synced",
        sync_result={"solidtime_project_id": "proj-9"},
    )

    assert created and created[0]["name"] == "solidtime.ticket.sync"
    assert successes and successes[0]["event_id"] == 321
    assert not failures


@pytest.mark.anyio
async def test_request_records_webhook_and_unwraps_data(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://example.solidtime",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "rate_limit_per_minute": 200,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    recorded: dict[str, object] = {}

    async def fake_manual_event(**kwargs):
        recorded["enqueue"] = kwargs
        return {"id": 7}

    async def fake_record_success(event_id, **kwargs):
        recorded["success"] = {"event_id": event_id, **kwargs}
        return {"id": event_id, "status": "succeeded"}

    async def fake_record_failure(event_id, **kwargs):
        recorded["failure"] = {"event_id": event_id, **kwargs}
        return {"id": event_id, "status": "failed"}

    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "create_manual_event", fake_manual_event
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_success", fake_record_success
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_failure", fake_record_failure
    )

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "p1"}, {"id": "p2"}]})

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            self._client = original_async_client(transport=transport)

        async def __aenter__(self):
            return self._client

        async def __aexit__(self, *exc):
            await self._client.aclose()

    reset_solidtime_caches.setattr(solidtime.httpx, "AsyncClient", _DummyAsyncClient)

    payload = await solidtime._request("GET", "/organizations/org-uuid/projects")
    data = solidtime._extract_data(payload)
    assert data == [{"id": "p1"}, {"id": "p2"}]
    assert captured["auth"] == "Bearer tok"
    assert captured["url"].endswith(
        "/api/v1/organizations/org-uuid/projects"
    )
    assert recorded.get("success", {}).get("event_id") == 7


@pytest.mark.anyio
async def test_request_records_failure_when_api_returns_error(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://example.solidtime",
                "api_token": "tok",
                "organization_id": "org-uuid",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    failures: list[dict[str, object]] = []

    async def fake_manual_event(**kwargs):
        return {"id": 11}

    async def fake_record_success(event_id, **kwargs):
        return {"id": event_id, "status": "succeeded"}

    async def fake_record_failure(event_id, **kwargs):
        failures.append({"event_id": event_id, **kwargs})
        return {"id": event_id, "status": "failed"}

    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "create_manual_event", fake_manual_event
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_success", fake_record_success
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_failure", fake_record_failure
    )

    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text="server boom")
    )
    original_async_client = httpx.AsyncClient

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            self._client = original_async_client(transport=transport)

        async def __aenter__(self):
            return self._client

        async def __aexit__(self, *exc):
            await self._client.aclose()

    reset_solidtime_caches.setattr(solidtime.httpx, "AsyncClient", _DummyAsyncClient)

    with pytest.raises(SolidtimeAPIError) as exc_info:
        await solidtime._request("GET", "/anything")

    assert "server boom" in str(exc_info.value)
    assert failures, "failure should be recorded against the webhook monitor"
    assert failures[0]["response_status"] == 500


# ---------------------------------------------------------------------------
# Outbound sync
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sync_ticket_to_project_creates_link(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": True,
                "rate_limit_per_minute": 100,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "ticket_number": "T-100",
            "subject": "Reset password",
            "company_id": 5,
            "status": "open",
        }

    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "get_ticket", fake_get_ticket
    )

    async def fake_get_company_by_id(company_id):
        return {"id": company_id, "name": "Acme Pty Ltd"}

    reset_solidtime_caches.setattr(
        solidtime.company_repo, "get_company_by_id", fake_get_company_by_id
    )

    async def fake_get_client_link(company_id):
        return None

    upserts: list[dict[str, object]] = []

    async def fake_upsert_client_link(**kwargs):
        upserts.append({"kind": "client", **kwargs})
        return {"company_id": kwargs["company_id"], **kwargs}

    async def fake_get_project_link(ticket_id):
        return None

    async def fake_upsert_project_link(**kwargs):
        upserts.append({"kind": "project", **kwargs})
        return {"ticket_id": kwargs["ticket_id"], **kwargs}

    async def fake_mark_client_error(*args, **kwargs):
        upserts.append({"kind": "client_error", "args": args, "kwargs": kwargs})

    async def fake_mark_project_error(*args, **kwargs):
        upserts.append({"kind": "project_error", "args": args, "kwargs": kwargs})

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "get_client_link", fake_get_client_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "upsert_client_link", fake_upsert_client_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "mark_client_link_error", fake_mark_client_error
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "get_project_link", fake_get_project_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "upsert_project_link", fake_upsert_project_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "mark_project_link_error", fake_mark_project_error
    )

    api_calls: list[tuple[str, object]] = []

    async def fake_create_client(org_id, *, name):
        api_calls.append(("create_client", name))
        return {"id": "client-uuid"}

    async def fake_create_project(org_id, *, body):
        api_calls.append(("create_project", dict(body)))
        return {"id": "project-uuid", "name": body["name"]}

    async def fake_update_project(*args, **kwargs):
        api_calls.append(("update_project", kwargs.get("body", {}).get("name")))
        return {"id": "project-uuid"}

    reset_solidtime_caches.setattr(solidtime, "create_client", fake_create_client)
    reset_solidtime_caches.setattr(solidtime, "create_project", fake_create_project)
    reset_solidtime_caches.setattr(solidtime, "update_project", fake_update_project)

    link = await solidtime.sync_ticket_to_project(42)
    assert link is not None
    assert link["solidtime_project_id"] == "project-uuid"
    # Client should be created first, then project.
    kinds = [call[0] for call in api_calls]
    assert kinds == ["create_client", "create_project"]
    project_payload = api_calls[1][1]
    assert isinstance(project_payload, dict)
    assert project_payload["color"].startswith("#")
    assert project_payload["client_id"] == "client-uuid"
    assert any(u.get("kind") == "client" for u in upserts)
    assert any(
        u.get("kind") == "project" and u.get("sync_status") == "synced"
        for u in upserts
    )


@pytest.mark.anyio
async def test_sync_ticket_to_project_records_error_on_api_failure(
    reset_solidtime_caches,
):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": True,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "subject": "X", "company_id": None, "status": "open"}

    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "get_ticket", fake_get_ticket
    )

    async def fake_get_project_link(ticket_id):
        return None

    captured_errors: list[tuple[int, str]] = []

    async def fake_mark_project_error(ticket_id, error):
        captured_errors.append((ticket_id, error))

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "get_project_link", fake_get_project_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "mark_project_link_error", fake_mark_project_error
    )

    async def fake_create_project(org_id, *, body):
        raise SolidtimeAPIError("upstream 502")

    reset_solidtime_caches.setattr(solidtime, "create_project", fake_create_project)

    with pytest.raises(SolidtimeAPIError):
        await solidtime.sync_ticket_to_project(99)
    assert captured_errors and captured_errors[0][1] == "upstream 502"


@pytest.mark.anyio
async def test_sync_ticket_to_project_links_existing_project_on_duplicate_create(
    reset_solidtime_caches,
):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": True,
                "default_client_id": "client-uuid",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_get_ticket(ticket_id):
        return {
            "id": ticket_id,
            "ticket_number": "T-422",
            "subject": "Existing upstream project",
            "company_id": None,
            "status": "open",
        }

    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "get_ticket", fake_get_ticket
    )

    async def fake_get_project_link(ticket_id):
        return None

    upserts: list[dict[str, object]] = []
    captured_errors: list[tuple[int, str]] = []

    async def fake_upsert_project_link(**kwargs):
        upserts.append(kwargs)
        return {"ticket_id": kwargs["ticket_id"], **kwargs}

    async def fake_mark_project_error(ticket_id, error):
        captured_errors.append((ticket_id, error))

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "get_project_link", fake_get_project_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "upsert_project_link", fake_upsert_project_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "mark_project_link_error", fake_mark_project_error
    )

    async def fake_create_project(org_id, *, body):
        raise SolidtimeAPIError(
            '{"message":"A project with the same name and client already exists '
            'in the organization."}'
        )

    async def fake_list_projects(org_id):
        return [
            {
                "id": "existing-project-uuid",
                "name": "#T-422 – Existing upstream project",
                "client_id": "client-uuid",
            }
        ]

    reset_solidtime_caches.setattr(solidtime, "create_project", fake_create_project)
    reset_solidtime_caches.setattr(solidtime, "list_projects", fake_list_projects)

    link = await solidtime.sync_ticket_to_project(422)

    assert link is not None
    assert link["solidtime_project_id"] == "existing-project-uuid"
    assert upserts and upserts[0]["sync_status"] == "synced"
    assert captured_errors == []


@pytest.mark.anyio
async def test_sync_ticket_skips_when_module_disabled(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {"enabled": False, "settings": {}}

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)
    # Should silently no-op rather than raise.
    assert await solidtime.sync_ticket_to_project(1) is None


@pytest.mark.anyio
async def test_sync_ticket_logs_reason_when_ticket_push_toggle_off(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": False,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    log_calls: list[dict[str, object]] = []

    def fake_log_info(message: str, **kwargs):
        log_calls.append({"message": message, **kwargs})

    reset_solidtime_caches.setattr(solidtime, "log_info", fake_log_info)

    assert await solidtime.sync_ticket_to_project(22) is None
    assert any(
        call.get("message") == "Solidtime ticket sync skipped"
        and call.get("reason") == "sync_tickets_to_projects is disabled"
        and call.get("ticket_id") == 22
        for call in log_calls
    )


@pytest.mark.anyio
async def test_schedule_ticket_sync_records_skipped_outcome_when_module_disabled(
    reset_solidtime_caches,
):
    async def fake_get_module(slug):
        return {
            "enabled": False,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    created_events: list[dict[str, object]] = []
    failure_records: list[dict[str, object]] = []
    completion_event = asyncio.Event()

    async def fake_create_manual_event(**kwargs):
        created_events.append(kwargs)
        return {"id": 901}

    async def fake_record_manual_success(event_id, **kwargs):
        return {"id": event_id, **kwargs}

    async def fake_record_manual_failure(event_id, **kwargs):
        failure_records.append({"event_id": event_id, **kwargs})
        completion_event.set()
        return {"id": event_id, **kwargs}

    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "create_manual_event", fake_create_manual_event
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_success", fake_record_manual_success
    )
    reset_solidtime_caches.setattr(
        solidtime.webhook_monitor, "record_manual_failure", fake_record_manual_failure
    )

    solidtime.schedule_ticket_sync(77)
    await asyncio.wait_for(completion_event.wait(), timeout=1.0)

    assert created_events
    assert created_events[0]["name"] == "solidtime.ticket.sync"
    assert failure_records
    assert failure_records[0]["event_id"] == 901
    assert failure_records[0]["status"] == "skipped"
    assert "disabled" in str(failure_records[0]["error_message"]).lower()


@pytest.mark.anyio
async def test_sync_reply_to_time_entry_deletes_non_billable_remote_entry(
    reset_solidtime_caches,
):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_time_entries_to_solidtime": True,
                "only_billable_to_solidtime": True,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_get_reply_by_id(reply_id):
        return {
            "id": reply_id,
            "ticket_id": 10,
            "minutes_spent": 25,
            "is_billable": False,
        }

    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "get_reply_by_id", fake_get_reply_by_id
    )

    async def fake_get_time_entry_link(reply_id):
        return {
            "ticket_reply_id": reply_id,
            "solidtime_org_id": "org-uuid",
            "solidtime_time_entry_id": "time-uuid",
        }

    deletions: list[tuple[str, str]] = []
    deleted_links: list[int] = []

    async def fake_delete_time_entry(org_id, time_entry_id):
        deletions.append((org_id, time_entry_id))

    async def fake_delete_time_entry_link(reply_id):
        deleted_links.append(reply_id)

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "get_time_entry_link", fake_get_time_entry_link
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "delete_time_entry_link", fake_delete_time_entry_link
    )
    reset_solidtime_caches.setattr(
        solidtime, "delete_time_entry", fake_delete_time_entry
    )

    assert await solidtime.sync_reply_to_time_entry(55) is None
    assert deletions == [("org-uuid", "time-uuid")]
    assert deleted_links == [55]


@pytest.mark.anyio
async def test_reconcile_once_updates_linked_reply_billable_status(
    reset_solidtime_caches,
):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": False,
                "sync_time_entries_from_solidtime": True,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_list_projects(org_id):
        return []

    async def fake_list_time_entries(org_id):
        return [
            {
                "id": "time-1",
                "project_id": "project-1",
                "start": "2026-05-11T10:00:00Z",
                "end": "2026-05-11T10:30:00Z",
                "billable": False,
                "description": "Remote fix",
            }
        ]

    reset_solidtime_caches.setattr(solidtime, "list_projects", fake_list_projects)
    reset_solidtime_caches.setattr(
        solidtime, "list_time_entries", fake_list_time_entries
    )

    async def fake_get_time_entry_link_by_remote(org_id, time_entry_id):
        return {
            "ticket_reply_id": 77,
            "solidtime_org_id": org_id,
            "solidtime_time_entry_id": time_entry_id,
            "direction": "out",
            "last_payload_hash": "stale-hash",
        }

    async def fake_get_reply_by_id(reply_id):
        return {"id": reply_id, "minutes_spent": 30, "is_billable": True}

    updated: dict[str, object] = {}
    upserts: list[dict[str, object]] = []

    async def fake_update_reply(reply_id, **kwargs):
        updated["reply_id"] = reply_id
        updated["kwargs"] = kwargs
        return {"id": reply_id, **kwargs}

    async def fake_upsert_time_entry_link(**kwargs):
        upserts.append(kwargs)
        return kwargs

    reset_solidtime_caches.setattr(
        solidtime.links_repo,
        "get_time_entry_link_by_remote",
        fake_get_time_entry_link_by_remote,
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "upsert_time_entry_link", fake_upsert_time_entry_link
    )
    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "get_reply_by_id", fake_get_reply_by_id
    )
    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "update_reply", fake_update_reply
    )

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "ok"
    assert summary["time_entries_pulled"] == 1
    assert updated == {"reply_id": 77, "kwargs": {"is_billable": False}}
    assert upserts and upserts[0]["direction"] == "out"


@pytest.mark.anyio
async def test_reconcile_once_imports_new_time_entry_with_billable_status(
    reset_solidtime_caches,
):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": False,
                "sync_time_entries_from_solidtime": True,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_list_projects(org_id):
        return []

    async def fake_list_time_entries(org_id):
        return [
            {
                "id": "time-2",
                "project_id": "project-2",
                "start": "2026-05-11T11:00:00Z",
                "end": "2026-05-11T11:45:00Z",
                "billable": True,
                "description": "Imported work",
            }
        ]

    reset_solidtime_caches.setattr(solidtime, "list_projects", fake_list_projects)
    reset_solidtime_caches.setattr(
        solidtime, "list_time_entries", fake_list_time_entries
    )

    async def fake_get_time_entry_link_by_remote(org_id, time_entry_id):
        return None

    async def fake_get_project_link_by_remote(org_id, project_id):
        return {"ticket_id": 12, "solidtime_project_id": project_id}

    created: dict[str, object] = {}
    upserts: list[dict[str, object]] = []

    async def fake_create_reply(**kwargs):
        created.update(kwargs)
        return {"id": 88, **kwargs}

    async def fake_upsert_time_entry_link(**kwargs):
        upserts.append(kwargs)
        return kwargs

    reset_solidtime_caches.setattr(
        solidtime.links_repo,
        "get_time_entry_link_by_remote",
        fake_get_time_entry_link_by_remote,
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo,
        "get_project_link_by_remote",
        fake_get_project_link_by_remote,
    )
    reset_solidtime_caches.setattr(
        solidtime.links_repo, "upsert_time_entry_link", fake_upsert_time_entry_link
    )
    reset_solidtime_caches.setattr(
        solidtime.tickets_repo, "create_reply", fake_create_reply
    )

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "ok"
    assert created["ticket_id"] == 12
    assert created["minutes_spent"] == 45
    assert created["is_billable"] is True
    assert upserts and upserts[0]["direction"] == "in"


# ---------------------------------------------------------------------------
# UI helper
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_ticket_links_returns_disabled_snapshot(reset_solidtime_caches):
    async def fake_get_module(slug):
        return None

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)
    result = await solidtime.get_ticket_links(1)
    assert result["enabled"] is False
    assert result["timer_url"] == ""


@pytest.mark.anyio
async def test_get_ticket_links_builds_timer_url_when_linked(reset_solidtime_caches):
    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_get_project_link(ticket_id):
        return {
            "solidtime_org_id": "org-uuid",
            "solidtime_project_id": "p-1",
            "last_synced_at": None,
            "sync_status": "synced",
        }

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "get_project_link", fake_get_project_link
    )
    result = await solidtime.get_ticket_links(7)
    assert result["enabled"] is True
    assert result["project_id"] == "p-1"
    assert result["project_url"].endswith("/projects/p-1")
    assert result["timer_url"].endswith("/time?project=p-1")


# ---------------------------------------------------------------------------
# Migration idempotency (smoke)
# ---------------------------------------------------------------------------

def test_migration_uses_create_table_if_not_exists():
    from pathlib import Path

    sql = Path("migrations/245_solidtime_integration.sql").read_text()
    # Every CREATE TABLE statement must use IF NOT EXISTS so the migration is
    # safe to re-run when validating idempotency.
    create_count = sql.upper().count("CREATE TABLE")
    if_not_exists_count = sql.upper().count("CREATE TABLE IF NOT EXISTS")
    assert create_count == if_not_exists_count == 4


# ---------------------------------------------------------------------------
# trigger_module integration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_trigger_module_solidtime_calls_reconcile_once(reset_solidtime_caches, monkeypatch):
    """trigger_module('solidtime', ...) must not raise 'No handler registered'."""
    from app.services import modules
    from app.repositories import integration_modules as module_repo

    async def fake_get_module(slug):
        if slug == "solidtime":
            return {
                "slug": "solidtime",
                "enabled": True,
                "settings": {
                    "base_url": "https://app.solidtime.io",
                    "api_token": "tok",
                    "organization_id": "org-uuid",
                },
            }
        return None

    monkeypatch.setattr(module_repo, "get_module", fake_get_module)

    reconciled: list[bool] = []

    async def fake_reconcile_once():
        reconciled.append(True)
        return {"status": "ok", "time_entries_pulled": 0, "time_entries_pushed": 0}

    monkeypatch.setattr(solidtime, "reconcile_once", fake_reconcile_once)

    result = await modules.trigger_module("solidtime", {}, background=False)

    assert result is not None
    assert result.get("status") == "ok"
    assert reconciled, "reconcile_once should have been called"


@pytest.mark.anyio
async def test_trigger_module_solidtime_not_in_trigger_actions(monkeypatch):
    """solidtime must be excluded from automation trigger-action module list."""
    from app.services import modules
    from app.repositories import integration_modules as module_repo

    async def fake_list_modules():
        return [
            {"slug": "solidtime", "name": "Solidtime", "enabled": True, "settings": {}},
            {"slug": "smtp", "name": "Send Email", "enabled": True, "settings": {}},
        ]

    monkeypatch.setattr(module_repo, "list_modules", fake_list_modules)

    result = await modules.list_trigger_action_modules()
    result_slugs = {m["slug"] for m in result}
    assert "solidtime" not in result_slugs
    assert "smtp" in result_slugs


# ---------------------------------------------------------------------------
# Outbound ticket push in reconcile_once
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_reconcile_once_pushes_unsynced_open_tickets(reset_solidtime_caches):
    """reconcile_once should push open tickets that have no Solidtime project link."""

    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": True,
                "sync_time_entries_from_solidtime": False,
                "sync_projects_to_tickets": False,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_list_unsynced_ticket_ids(limit=50):
        return [101, 102]

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "list_unsynced_ticket_ids", fake_list_unsynced_ticket_ids
    )

    synced_ids: list[int] = []

    async def fake_sync_ticket_to_project(ticket_id):
        synced_ids.append(ticket_id)
        return {"ticket_id": ticket_id, "solidtime_project_id": f"p-{ticket_id}"}

    reset_solidtime_caches.setattr(
        solidtime, "sync_ticket_to_project", fake_sync_ticket_to_project
    )

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "ok"
    assert summary["tickets_pushed"] == 2
    assert synced_ids == [101, 102]


@pytest.mark.anyio
async def test_reconcile_once_records_error_on_ticket_push_failure(reset_solidtime_caches):
    """reconcile_once should record errors when ticket push fails."""

    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": True,
                "sync_time_entries_from_solidtime": False,
                "sync_projects_to_tickets": False,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    async def fake_list_unsynced_ticket_ids(limit=50):
        return [55]

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "list_unsynced_ticket_ids", fake_list_unsynced_ticket_ids
    )

    async def fake_sync_ticket_to_project(ticket_id):
        raise solidtime.SolidtimeAPIError("API down")

    reset_solidtime_caches.setattr(
        solidtime, "sync_ticket_to_project", fake_sync_ticket_to_project
    )

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "error"
    assert summary["errors"] == 1
    assert summary["tickets_pushed"] == 0


@pytest.mark.anyio
async def test_reconcile_once_skips_ticket_push_when_disabled(reset_solidtime_caches):
    """reconcile_once should not push tickets when sync_tickets_to_projects is False."""

    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
                "sync_tickets_to_projects": False,
                "sync_time_entries_from_solidtime": False,
                "sync_projects_to_tickets": False,
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    called: list[bool] = []

    async def fake_list_unsynced_ticket_ids(limit=50):
        called.append(True)
        return [99]

    reset_solidtime_caches.setattr(
        solidtime.links_repo, "list_unsynced_ticket_ids", fake_list_unsynced_ticket_ids
    )

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "ok"
    assert summary["tickets_pushed"] == 0
    assert not called, "list_unsynced_ticket_ids should not be called when sync_tickets_to_projects is False"


@pytest.mark.anyio
async def test_reconcile_once_returns_reason_when_module_disabled(reset_solidtime_caches):
    """reconcile_once should expose why it skipped so admins can act on it."""

    async def fake_get_module(slug):
        return {
            "enabled": False,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "org-uuid",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "skipped"
    assert "disabled" in str(summary.get("reason", "")).lower()


@pytest.mark.anyio
async def test_reconcile_once_returns_reason_when_organization_id_missing(
    reset_solidtime_caches,
):
    """reconcile_once should report when the organisation id is unset."""

    async def fake_get_module(slug):
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://app.solidtime.io",
                "api_token": "tok",
                "organization_id": "",
            },
        }

    reset_solidtime_caches.setattr(solidtime.module_repo, "get_module", fake_get_module)

    summary = await solidtime.reconcile_once()

    assert summary["status"] == "skipped"
    assert "organization_id" in str(summary.get("reason", ""))
