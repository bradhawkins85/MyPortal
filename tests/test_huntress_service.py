"""Tests for the Huntress API client / refresh service."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
    # Skip the per-call sleep so tests run instantly.
    monkeypatch.setattr(huntress_service, "_REQUEST_INTERVAL_SECONDS", 0)


def _patch_client(transport):
    from app.services import huntress as huntress_service

    real_client = huntress_service._client

    def builder(credentials):
        client = real_client(credentials)
        client._transport = transport
        return client

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
    }


@pytest.mark.asyncio
async def test_get_edr_summary_uses_basic_auth_and_parses_totals(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    captured_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth.append(request.headers.get("authorization", ""))
        path = request.url.path
        if path.endswith("/incident_reports"):
            status_value = request.url.params.get("status")
            # API uses "sent" for active/open incidents, not "open"
            total = 4 if status_value == "sent" else 9
            return httpx.Response(200, json={"total": total, "incident_reports": []})
        if path.endswith("/signals"):
            return httpx.Response(200, json={"total": 17, "signals": []})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_edr_summary("org-123")

    assert result == {
        "active_incidents": 4,
        "resolved_incidents": 9,
        "signals_investigated": 17,
    }
    # All three calls should carry HTTP Basic auth derived from the configured key/secret.
    assert captured_auth and all(auth.startswith("Basic ") for auth in captured_auth)


@pytest.mark.asyncio
async def test_get_siem_data_volume_returns_window_and_bytes(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/siem/usage")
        return httpx.Response(200, json={"total_bytes": 5 * 1024 ** 3})

    transport = httpx.MockTransport(handler)
    with _patch_client(transport):
        result = await huntress_service.get_siem_data_volume("org-1", days=30)

    assert result["data_collected_bytes_30d"] == 5 * 1024 ** 3
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
        AsyncMock(return_value={
            "active_incidents": 1,
            "resolved_incidents": 2,
            "signals_investigated": 3,
        }),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_itdr_summary",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_sat_summary",
        AsyncMock(return_value={
            "avg_completion_rate": 80.0,
            "avg_score": 90.0,
            "phishing_clicks": 4,
            "phishing_compromises": 1,
            "phishing_reports": 7,
        }),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_sat_learner_breakdown",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        huntress_service,
        "get_siem_data_volume",
        AsyncMock(return_value={
            "data_collected_bytes_30d": 2048,
            "window_start": datetime(2026, 4, 1),
            "window_end": datetime(2026, 4, 30),
        }),
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
        {"id": 42, "huntress_organization_id": "org-1"}
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
    monkeypatch.setattr(huntress_service, "is_module_enabled", AsyncMock(return_value=False))

    result = await huntress_service.refresh_all_companies()
    assert result == {"status": "skipped", "reason": "module_disabled", "companies": []}


@pytest.mark.asyncio
async def test_refresh_all_companies_skips_companies_without_org_id(monkeypatch):
    from app.services import huntress as huntress_service

    _set_credentials(monkeypatch)
    monkeypatch.setattr(huntress_service, "is_module_enabled", AsyncMock(return_value=True))
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
