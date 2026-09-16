"""Tests for SMTP2Go admin operations routes."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app, scheduler_service
from app.core.database import db


def _analytics_payload() -> dict:
    return {
        "totals": {"processed": 4, "delivered": 3, "open": 2, "click": 1, "bounce": 1},
        "rates": {"open_rate": 50.0, "click_rate": 25.0, "bounce_rate": 25.0},
        "trends": [{"date": "2026-09-15", "processed": 4, "delivered": 3, "open": 2, "click": 1, "bounce": 1}],
        "geo_breakdown": [{"label": "Brisbane, QLD, AU", "count": 2}],
        "device_breakdown": [{"label": "mobile", "count": 2}],
        "client_breakdown": [{"label": "Outlook", "count": 2}],
        "queue": {"summary": {"recent_total": 1, "retry_wait": 0, "queued_not_engaged_checks": 1}, "recent": []},
    }


def _module_payload() -> dict:
    return {
        "slug": "smtp2go",
        "enabled": True,
        "settings": {
            "manage_url": "/admin/modules/smtp2go",
            "rate_limit_max_retries": 3,
            "retry_backoff_seconds": 60,
            "not_engaged_delay_seconds": 86400,
            "ab_campaigns": [
                {
                    "name": "Welcome flow",
                    "winner_metric": "click_rate",
                    "minimum_sample_size": 10,
                    "variants": [
                        {"name": "A", "sent": 20, "clicked": 4},
                        {"name": "B", "sent": 20, "clicked": 6},
                    ],
                }
            ],
        },
    }


def _install_app_mocks(monkeypatch) -> None:
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    async def fake_require_super_admin_page(request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)
    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)


def test_smtp2go_admin_dashboard_renders_analytics(monkeypatch):
    _install_app_mocks(monkeypatch)
    from app.features.smtp import admin_routes

    async def fake_get_module(slug, redact=False):
        assert slug == "smtp2go"
        assert redact is False
        return _module_payload()

    async def fake_get_analytics_summary(days=30):
        assert days == 30
        return _analytics_payload()

    monkeypatch.setattr(admin_routes.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(
        admin_routes.smtp2go_service, "get_analytics_summary", fake_get_analytics_summary
    )

    with TestClient(app) as client:
        response = client.get("/admin/modules/smtp2go")

    assert response.status_code == 200
    assert "Deliverability dashboard" in response.text
    assert "SMTP2Go email opened" in response.text
    assert "Welcome flow" in response.text
    assert "Brisbane, QLD, AU" in response.text


def test_smtp2go_admin_settings_reject_invalid_campaign_json(monkeypatch):
    _install_app_mocks(monkeypatch)
    from app.features.smtp import admin_routes

    async def fake_get_module(slug, redact=False):
        return _module_payload()

    async def fake_get_analytics_summary(days=30):
        return _analytics_payload()

    monkeypatch.setattr(admin_routes.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(
        admin_routes.smtp2go_service, "get_analytics_summary", fake_get_analytics_summary
    )

    with TestClient(app) as client:
        response = client.post(
            "/admin/modules/smtp2go/settings",
            data={
                "rateLimitMaxRetries": "3",
                "retryBackoffSeconds": "60",
                "notEngagedDelaySeconds": "120",
                "abCampaignsRaw": "{bad json",
            },
        )

    assert response.status_code == 400
    assert "A/B campaign JSON is invalid." in response.text
