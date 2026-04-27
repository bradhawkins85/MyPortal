"""Tests for the Backup History admin page and webhook endpoint."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import app.main as main_module
import pytest
from fastapi.testclient import TestClient

from app.core.database import db
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return None

    async def fake_list_companies_for_user(user_id):
        return []

    async def fake_get_user_company(user_id, company_id):
        return {}

    async def fake_list_modules():
        return []

    monkeypatch.setattr(db, "connect", _noop)
    monkeypatch.setattr(db, "disconnect", _noop)
    monkeypatch.setattr(db, "run_migrations", _noop)
    monkeypatch.setattr(scheduler_service, "start", _noop)
    monkeypatch.setattr(scheduler_service, "stop", _noop)
    monkeypatch.setattr(
        main_module.change_log_service, "sync_change_log_sources", _noop
    )
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", _noop)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", _noop)
    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(
        main_module.user_company_repo,
        "list_companies_for_user",
        fake_list_companies_for_user,
    )
    monkeypatch.setattr(
        main_module.user_company_repo, "get_user_company", fake_get_user_company
    )
    monkeypatch.setattr(main_module.modules_service, "list_modules", fake_list_modules)


@pytest.fixture
def super_admin(monkeypatch):
    async def fake_require_super_admin_page(request):
        return (
            {"id": 1, "email": "admin@example.com", "is_super_admin": True},
            None,
        )

    monkeypatch.setattr(
        main_module, "_require_super_admin_page", fake_require_super_admin_page
    )


def _sample_job(**overrides):
    job = {
        "id": 1,
        "company_id": 7,
        "name": "Nightly Veeam",
        "description": "Nightly backup of file server",
        "token": "TOKEN123",
        "is_active": True,
        "created_by": 1,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    job.update(overrides)
    return job


def test_admin_backup_jobs_page_renders(super_admin, monkeypatch):
    job = _sample_job()
    annotated = {
        **job,
        "latest_event": {
            "backup_job_id": 1,
            "event_date": date(2026, 4, 27),
            "status": "pass",
            "status_message": "OK",
            "reported_at": datetime(2026, 4, 27, 1, 2, 3, tzinfo=timezone.utc),
        },
        "latest_status": "pass",
        "today_event": {
            "backup_job_id": 1,
            "event_date": date(2026, 4, 27),
            "status": "pass",
            "status_message": "OK",
            "reported_at": datetime(2026, 4, 27, 1, 2, 3, tzinfo=timezone.utc),
        },
        "today_status": "pass",
    }

    async def fake_list_jobs_with_latest(*, company_id=None, include_inactive=True):
        return [annotated]

    async def fake_list_companies():
        return [{"id": 7, "name": "Acme Pty Ltd"}]

    async def fake_get_job(job_id):
        return None

    async def fake_build_history_grid(*, company_id=None, days=14, include_inactive=True):
        return {
            "dates": [date(2026, 4, 27)],
            "rows": [
                {
                    "job": job,
                    "events": [
                        {
                            "date": date(2026, 4, 27),
                            "status": "pass",
                            "label": "Pass",
                            "variant": "success",
                            "pdf_color": "#10b981",
                            "message": "OK",
                            "reported_at": None,
                        }
                    ],
                }
            ],
            "start_date": date(2026, 4, 27),
            "end_date": date(2026, 4, 27),
        }

    monkeypatch.setattr(
        main_module.backup_jobs_service,
        "list_jobs_with_latest",
        fake_list_jobs_with_latest,
    )
    monkeypatch.setattr(main_module.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(main_module.backup_jobs_service, "get_job", fake_get_job)
    monkeypatch.setattr(
        main_module.backup_jobs_service,
        "build_history_grid",
        fake_build_history_grid,
    )

    with TestClient(app) as client:
        response = client.get("/admin/backup-jobs")

    assert response.status_code == 200
    html = response.text
    assert "Backup history" in html
    assert "Nightly Veeam" in html
    assert "Acme Pty Ltd" in html
    # webhook URL is shown after picking a job to edit; verify the page also
    # renders the stat strip with today's pass count.
    assert "Today" in html


def test_backup_status_webhook_records_status(monkeypatch):
    job = _sample_job(token="TOKEN123")

    async def fake_get_job_by_token(token):
        assert token == "TOKEN123"
        return job

    captured: dict = {}

    async def fake_upsert_event(job_id, event_date, *, status, status_message, reported_at, source):
        captured["args"] = {
            "job_id": job_id,
            "event_date": event_date,
            "status": status,
            "status_message": status_message,
            "source": source,
        }
        return {
            "id": 99,
            "backup_job_id": job_id,
            "event_date": event_date,
            "status": status,
            "status_message": status_message,
            "reported_at": reported_at,
            "source": source,
        }

    monkeypatch.setattr(
        main_module.backup_jobs_service.backup_jobs_repo,
        "get_job_by_token",
        fake_get_job_by_token,
    )
    monkeypatch.setattr(
        main_module.backup_jobs_service.backup_jobs_repo,
        "upsert_event",
        fake_upsert_event,
    )

    # The repository's get_event is called inside upsert in production; the
    # stub above already returns the row so override get_event to a no-op.
    async def fake_get_event(job_id, event_date):
        return captured.get("event")

    monkeypatch.setattr(
        main_module.backup_jobs_service.backup_jobs_repo,
        "get_event",
        fake_get_event,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/backup-status",
            json={"job_id": "TOKEN123", "status": "ok", "message": "Backup OK"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pass"  # "ok" is normalised to "pass"
    assert body["company_id"] == 7
    assert body["status_message"] == "Backup OK"
    assert captured["args"]["status"] == "pass"
    assert captured["args"]["status_message"] == "Backup OK"


def test_backup_status_webhook_unknown_token_returns_404(monkeypatch):
    async def fake_get_job_by_token(token):
        return None

    monkeypatch.setattr(
        main_module.backup_jobs_service.backup_jobs_repo,
        "get_job_by_token",
        fake_get_job_by_token,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/backup-status",
            json={"job_id": "missing", "status": "pass"},
        )

    assert response.status_code == 404


def test_backup_status_webhook_rejects_invalid_status(monkeypatch):
    job = _sample_job()

    async def fake_get_job_by_token(token):
        return job

    monkeypatch.setattr(
        main_module.backup_jobs_service.backup_jobs_repo,
        "get_job_by_token",
        fake_get_job_by_token,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/backup-status",
            json={"job_id": "TOKEN123", "status": "exploded"},
        )

    assert response.status_code == 400
    assert "Unsupported status" in response.text


# ---------------------------------------------------------------------------
# Alert check tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_backup_alerts_no_jobs_no_tickets(monkeypatch):
    """When no jobs have alert thresholds configured, no tickets are created."""
    import app.services.backup_jobs as backup_jobs_service
    from app.repositories import backup_jobs as backup_jobs_repo

    async def fake_list_jobs(include_inactive=True):
        return [
            _sample_job(alert_no_success_days=None, alert_fail_days=None, alert_unknown_days=None)
        ]

    monkeypatch.setattr(backup_jobs_repo, "list_jobs", fake_list_jobs)

    result = await backup_jobs_service.check_backup_alerts()
    assert result["checked"] == 0
    assert result["tickets_created"] == 0


@pytest.mark.asyncio
async def test_check_backup_alerts_creates_ticket_for_no_success(monkeypatch):
    """When no successful backup in threshold days, a ticket is created."""
    import app.services.backup_jobs as backup_jobs_service
    from app.repositories import backup_jobs as backup_jobs_repo
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    today = date(2026, 4, 27)
    job = _sample_job(alert_no_success_days=3, alert_fail_days=None, alert_unknown_days=None)

    async def fake_list_jobs(include_inactive=True):
        return [job]

    # Return only fail/unknown events in the window – no 'pass'
    async def fake_list_events_in_range(*, job_ids, start_date, end_date):
        return [
            {"backup_job_id": 1, "event_date": today, "status": "fail"},
            {"backup_job_id": 1, "event_date": today - timedelta(days=1), "status": "fail"},
            {"backup_job_id": 1, "event_date": today - timedelta(days=2), "status": "unknown"},
        ]

    async def fake_find_open_ticket(external_reference):
        return None  # No existing open ticket

    tickets_created = []

    async def fake_create_ticket(*, subject, description, requester_id, company_id,
                                  assigned_user_id, priority, status, category,
                                  module_slug, external_reference, trigger_automations=True,
                                  **kwargs):
        tickets_created.append({"subject": subject, "external_reference": external_reference})
        return {"id": 99, "subject": subject}

    monkeypatch.setattr(backup_jobs_repo, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(backup_jobs_repo, "list_events_in_range", fake_list_events_in_range)
    monkeypatch.setattr(tickets_repo, "find_open_ticket_by_external_reference", fake_find_open_ticket)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)

    result = await backup_jobs_service.check_backup_alerts()
    assert result["checked"] == 1
    assert result["tickets_created"] == 1
    assert len(tickets_created) == 1
    assert "No Successful Backups" in tickets_created[0]["subject"]
    assert tickets_created[0]["external_reference"] == "backup_alert:no_success:1"


@pytest.mark.asyncio
async def test_check_backup_alerts_skips_when_open_ticket_exists(monkeypatch):
    """No duplicate ticket is created when an open alert ticket already exists."""
    import app.services.backup_jobs as backup_jobs_service
    from app.repositories import backup_jobs as backup_jobs_repo
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    today = date(2026, 4, 27)
    job = _sample_job(alert_no_success_days=2, alert_fail_days=None, alert_unknown_days=None)

    async def fake_list_jobs(include_inactive=True):
        return [job]

    async def fake_list_events_in_range(*, job_ids, start_date, end_date):
        return [
            {"backup_job_id": 1, "event_date": today, "status": "fail"},
            {"backup_job_id": 1, "event_date": today - timedelta(days=1), "status": "fail"},
        ]

    # An open ticket already exists for this alert
    async def fake_find_open_ticket(external_reference):
        return {"id": 50, "subject": "Existing open alert ticket", "closed_at": None}

    tickets_created = []

    async def fake_create_ticket(**kwargs):
        tickets_created.append(kwargs)
        return {"id": 99}

    monkeypatch.setattr(backup_jobs_repo, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(backup_jobs_repo, "list_events_in_range", fake_list_events_in_range)
    monkeypatch.setattr(tickets_repo, "find_open_ticket_by_external_reference", fake_find_open_ticket)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)

    result = await backup_jobs_service.check_backup_alerts()
    assert result["checked"] == 1
    assert result["tickets_created"] == 0
    assert len(tickets_created) == 0


@pytest.mark.asyncio
async def test_check_backup_alerts_no_alert_when_recent_success(monkeypatch):
    """No alert is raised when a successful backup exists within the threshold window."""
    import app.services.backup_jobs as backup_jobs_service
    from app.repositories import backup_jobs as backup_jobs_repo
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    today = date(2026, 4, 27)
    job = _sample_job(alert_no_success_days=3, alert_fail_days=None, alert_unknown_days=None)

    async def fake_list_jobs(include_inactive=True):
        return [job]

    # Yesterday was a pass – within the 3-day window
    async def fake_list_events_in_range(*, job_ids, start_date, end_date):
        return [
            {"backup_job_id": 1, "event_date": today, "status": "fail"},
            {"backup_job_id": 1, "event_date": today - timedelta(days=1), "status": "pass"},
        ]

    tickets_created = []

    async def fake_create_ticket(**kwargs):
        tickets_created.append(kwargs)
        return {"id": 99}

    monkeypatch.setattr(backup_jobs_repo, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(backup_jobs_repo, "list_events_in_range", fake_list_events_in_range)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)

    result = await backup_jobs_service.check_backup_alerts()
    assert result["checked"] == 1
    assert result["tickets_created"] == 0
    assert len(tickets_created) == 0


@pytest.mark.asyncio
async def test_check_backup_alerts_unknown_status(monkeypatch):
    """Ticket is created when unknown status threshold is exceeded."""
    import app.services.backup_jobs as backup_jobs_service
    from app.repositories import backup_jobs as backup_jobs_repo
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    today = date(2026, 4, 27)
    job = _sample_job(alert_no_success_days=None, alert_fail_days=None, alert_unknown_days=2)

    async def fake_list_jobs(include_inactive=True):
        return [job]

    async def fake_list_events_in_range(*, job_ids, start_date, end_date):
        return [
            {"backup_job_id": 1, "event_date": today, "status": "unknown"},
            {"backup_job_id": 1, "event_date": today - timedelta(days=1), "status": "unknown"},
        ]

    async def fake_find_open_ticket(external_reference):
        return None

    tickets_created = []

    async def fake_create_ticket(*, subject, description, requester_id, company_id,
                                  assigned_user_id, priority, status, category,
                                  module_slug, external_reference, trigger_automations=True,
                                  **kwargs):
        tickets_created.append({"subject": subject, "external_reference": external_reference})
        return {"id": 99, "subject": subject}

    monkeypatch.setattr(backup_jobs_repo, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(backup_jobs_repo, "list_events_in_range", fake_list_events_in_range)
    monkeypatch.setattr(tickets_repo, "find_open_ticket_by_external_reference", fake_find_open_ticket)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)

    result = await backup_jobs_service.check_backup_alerts()
    assert result["checked"] == 1
    assert result["tickets_created"] == 1
    assert "Unknown Backup Job Status" in tickets_created[0]["subject"]
    assert tickets_created[0]["external_reference"] == "backup_alert:unknown:1"
