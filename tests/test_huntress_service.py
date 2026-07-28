"""Tests for the Huntress API client / refresh service."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _set_credentials(monkeypatch):
    """Force ``_get_credentials`` to return a fixed test set."""
    from app.services import huntress as huntress_service

    monkeypatch.setattr(
        huntress_service,
        "_get_credentials",
        lambda: {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://api.huntress.io/v1",
        },
    )
    monkeypatch.setattr(
        huntress_service,
        "_get_curricula_credentials",
        lambda: {
            "api_key": "test-key",
            "api_secret": "test-secret",
            "base_url": "https://mycurricula.com/api/v1",
        },
    )
    # Skip the per-call sleep so tests run instantly.
    monkeypatch.setattr(huntress_service, "_REQUEST_INTERVAL_SECONDS", 0)
    # pytest runs async tests on separate loops; avoid retaining a lock bound
    # by a concurrent request in an earlier test.
    monkeypatch.setattr(huntress_service, "_request_lock", asyncio.Lock())


def _patch_client(transport):
    from app.services import huntress as huntress_service

    def builder(credentials):
        return httpx.AsyncClient(
            base_url=credentials["base_url"],
            auth=(credentials["api_key"], credentials["api_secret"]),
            transport=transport,
        )

    return patch.object(huntress_service, "_client", builder)


@pytest.mark.asyncio
async def test_credentials_status_reflects_environment(monkeypatch):
    from app.core import config as config_module
    from app.services import huntress as huntress_service

    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "huntress_api_key": "abc",
                "huntress_api_secret": "",
                "huntress_base_url": "https://api.huntress.io/v1",
                "curricula_api_key": "curricula-key",
                "curricula_api_secret": "",
                "curricula_base_url": "https://mycurricula.com/api/v1",
            },
        )(),
    )
    # huntress imports get_settings via its module namespace
    monkeypatch.setattr(huntress_service, "get_settings", config_module.get_settings)
    status = huntress_service.credentials_status()
    assert status == {
        "api_key_present": True,
        "api_secret_present": False,
        "base_url_present": True,
        "curricula_api_key_present": True,
        "curricula_api_secret_present": False,
        "curricula_base_url_present": True,
    }


@pytest.mark.asyncio
async def test_missing_curricula_credentials_do_not_abort_edr_sync(monkeypatch):
    """SAT configuration is optional and independent from Huntress EDR."""
    from app.services import huntress as huntress_service

    monkeypatch.setattr(
        huntress_service,
        "get_edr_summary",
        AsyncMock(
            return_value={
                "active_incidents": 1,
                "resolved_incidents": 2,
                "signals_investigated": 3,
            }
        ),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_itdr_summary",
        AsyncMock(return_value={"signals_investigated": 4}),
    )
    missing_sat = AsyncMock(
        side_effect=huntress_service.HuntressConfigurationError(
            "Curricula credentials are not configured"
        )
    )
    monkeypatch.setattr(huntress_service, "get_sat_summary", missing_sat)
    monkeypatch.setattr(huntress_service, "get_sat_learner_breakdown", missing_sat)
    monkeypatch.setattr(
        huntress_service, "get_siem_data_volume", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        huntress_service, "get_soc_event_count", AsyncMock(return_value=None)
    )

    repo = huntress_service.huntress_repo
    monkeypatch.setattr(repo, "upsert_edr_stats", AsyncMock())
    monkeypatch.setattr(repo, "upsert_itdr_stats", AsyncMock())

    result = await huntress_service.refresh_company(
        {
            "id": 42,
            "huntress_organization_id": "org-1",
            "huntress_sat_account_id": "sat-1",
        }
    )

    assert result["status"] == "partial"
    assert set(result["errors"]) == {"sat", "sat_learners"}
    repo.upsert_edr_stats.assert_awaited_once()
    repo.upsert_itdr_stats.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_edr_summary_uses_basic_auth_and_parses_totals(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    captured_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth.append(request.headers.get("authorization", ""))
        path = request.url.path
        if path.endswith("/incident_reports"):
            # API uses "sent" for active/open incidents, not "open"
            assert request.url.params.get("status") == "sent"
            return httpx.Response(200, json={"total": 4, "incident_reports": []})
        if path.endswith("/signals"):
            return httpx.Response(200, json={"total": 17, "signals": []})
        if path.endswith("/reports"):
            return httpx.Response(
                200,
                json={"reports": [{"incidents_resolved": 9}]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_edr_summary("org-123")

    assert result == {
        "active_incidents": 4,
        "resolved_incidents": 9,
        "signals_investigated": 17,
    }
    # All calls should carry HTTP Basic auth derived from the configured key/secret.
    assert captured_auth and all(auth.startswith("Basic ") for auth in captured_auth)


@pytest.mark.asyncio
async def test_get_edr_summary_does_not_filter_incidents_by_resolved(monkeypatch):
    """Resolved is a summary metric, not a valid incident-report status filter."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)
    incident_statuses: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/incident_reports"):
            status = request.url.params.get("status")
            incident_statuses.append(status)
            if status == "resolved":
                return httpx.Response(400, json={"error": "invalid status"})
            return httpx.Response(200, json={"total": 2, "incident_reports": []})
        if request.url.path.endswith("/signals"):
            return httpx.Response(200, json={"total": 3, "signals": []})
        if request.url.path.endswith("/reports"):
            return httpx.Response(
                200,
                json={"reports": [{"incidents_resolved": 11}]},
            )
        return httpx.Response(404)

    with _patch_client(httpx.MockTransport(handler)):
        result = await huntress_service.get_edr_summary("559776")

    assert incident_statuses == ["sent"]
    assert result == {
        "active_incidents": 2,
        "resolved_incidents": 11,
        "signals_investigated": 3,
    }


@pytest.mark.asyncio
async def test_get_siem_data_volume_returns_window_and_bytes(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/siem/usage")
        return httpx.Response(200, json={"total_bytes": 5 * 1024**3})

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_siem_data_volume("org-1", days=30)

    assert result["data_collected_bytes_30d"] == 5 * 1024**3
    assert result["window_start"] is not None and result["window_end"] is not None


@pytest.mark.asyncio
async def test_get_siem_data_volume_returns_none_on_404(monkeypatch):
    """If the Managed SIEM product is not enabled, 404 should return None silently."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_siem_data_volume("org-1", days=30)

    assert result is None


@pytest.mark.asyncio
async def test_get_sat_summary_returns_none_on_404(monkeypatch):
    """If the SAT product is not enabled, 404 should return None silently."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_sat_summary("org-1")

    assert result is None


@pytest.mark.asyncio
async def test_get_sat_learner_breakdown_returns_none_on_404(monkeypatch):
    """If the SAT product is not enabled, 404 should return None silently."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_sat_learner_breakdown("org-1")

    assert result is None


@pytest.mark.asyncio
async def test_get_sat_learner_breakdown_falls_back_to_filtered_collection(
    monkeypatch,
):
    """Current Curricula tenants expose learners through the collection URL."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/accounts/57777/learners"):
            return httpx.Response(404)
        if request.url.path.endswith("/learners"):
            assert request.url.params["filter[account_id]"] == "57777"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "learner-1",
                            "type": "learners",
                            "attributes": {
                                "email": "learner@example.com",
                                "name": "Example Learner",
                                "progress": 75,
                            },
                        }
                    ]
                },
            )
        return httpx.Response(500)

    with _patch_client(httpx.MockTransport(handler)):
        result = await huntress_service.get_sat_learner_breakdown("57777")

    assert [request.url.path for request in requests] == [
        "/api/v1/accounts/57777/learners",
        "/api/v1/learners",
    ]
    assert result == [
        {
            "learner_external_id": "learner-1",
            "learner_email": "learner@example.com",
            "learner_name": "Example Learner",
            "assignment_id": "learner-summary",
            "assignment_name": None,
            "status": None,
            "completion_percent": 75.0,
            "score": 0.0,
            "click_rate": 0.0,
            "compromise_rate": 0.0,
            "report_rate": 0.0,
        }
    ]


@pytest.mark.asyncio
async def test_refresh_company_reports_inaccessible_sat_account(monkeypatch):
    """A SAT 404 must be visible in task output rather than looking successful."""
    from app.services import huntress as huntress_service

    monkeypatch.setattr(huntress_service, "get_edr_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(huntress_service, "get_itdr_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(huntress_service, "get_sat_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(
        huntress_service, "get_sat_learner_breakdown", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        huntress_service, "get_siem_data_volume", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        huntress_service, "get_soc_event_count", AsyncMock(return_value=None)
    )

    result = await huntress_service.refresh_company(
        {
            "id": 20,
            "huntress_organization_id": "559776",
            "huntress_sat_account_id": "57778",
        }
    )

    assert result["status"] == "partial"
    assert set(result["errors"]) == {"sat", "sat_learners"}
    assert "parent Curricula API client" in result["errors"]["sat"]


@pytest.mark.asyncio
async def test_get_soc_event_count_returns_none_on_404(monkeypatch):
    """If the SOC product is not enabled, 404 should return None silently."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_soc_event_count("org-1")

    assert result is None


@pytest.mark.asyncio
async def test_refresh_company_tolerates_partial_failure(monkeypatch):
    """If one product errors, the rest still write to the database."""
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    monkeypatch.setattr(
        huntress_service,
        "get_edr_summary",
        AsyncMock(
            return_value={
                "active_incidents": 1,
                "resolved_incidents": 2,
                "signals_investigated": 3,
            }
        ),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_itdr_summary",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_sat_summary",
        AsyncMock(
            return_value={
                "avg_completion_rate": 80.0,
                "avg_score": 90.0,
                "phishing_clicks": 4,
                "phishing_compromises": 1,
                "phishing_reports": 7,
            }
        ),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_sat_learner_breakdown",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_siem_data_volume",
        AsyncMock(
            return_value={
                "data_collected_bytes_30d": 2048,
                "window_start": datetime(2026, 4, 1),
                "window_end": datetime(2026, 4, 30),
            }
        ),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_soc_event_count",
        AsyncMock(return_value={"total_events_analysed": 555}),
    )

    repo = huntress_service.huntress_repo
    monkeypatch.setattr(repo, "upsert_edr_stats", AsyncMock())
    monkeypatch.setattr(repo, "upsert_itdr_stats", AsyncMock())
    monkeypatch.setattr(repo, "upsert_sat_stats", AsyncMock())
    monkeypatch.setattr(repo, "replace_sat_learner_progress", AsyncMock(return_value=0))
    monkeypatch.setattr(repo, "upsert_siem_stats", AsyncMock())
    monkeypatch.setattr(repo, "upsert_soc_stats", AsyncMock())

    result = await huntress_service.refresh_company(
        {
            "id": 42,
            "huntress_organization_id": "org-1",
            "huntress_sat_account_id": "sat-1",
        }
    )

    assert result["status"] == "partial"
    assert "itdr" in result["errors"]
    # The other products did update.
    repo.upsert_edr_stats.assert_awaited_once()
    repo.upsert_sat_stats.assert_awaited_once()
    repo.upsert_siem_stats.assert_awaited_once()
    repo.upsert_soc_stats.assert_awaited_once()
    repo.upsert_itdr_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_all_companies_skips_when_module_disabled(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)
    monkeypatch.setattr(
        huntress_service, "is_module_enabled", AsyncMock(return_value=False)
    )

    result = await huntress_service.refresh_all_companies()
    assert result == {"status": "skipped", "reason": "module_disabled", "companies": []}


@pytest.mark.asyncio
async def test_refresh_all_companies_skips_companies_without_org_id(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)
    monkeypatch.setattr(
        huntress_service, "is_module_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        huntress_service.company_repo,
        "list_companies",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "A"},  # no huntress id -> skipped
                {"id": 2, "name": "B", "huntress_organization_id": "org-2"},
            ]
        ),
    )
    refresh = AsyncMock(return_value={"status": "ok", "company_id": 2, "errors": {}})
    monkeypatch.setattr(huntress_service, "refresh_company", refresh)

    result = await huntress_service.refresh_all_companies()

    assert result["refreshed"] == 1
    assert result["skipped"] == 1
    refresh.assert_awaited_once()
