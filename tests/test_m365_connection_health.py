from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.m365_connection_health import build_connection_health


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
CREDS = {"tenant_id": "tenant", "client_id": "client", "client_secret_expires_at": NOW + timedelta(days=30)}
VERIFIED = {"mode": "managed", "verification_tenant": 1, "verification_workload": 1, "verification_renewal": 1, "verified_at": NOW}


def test_new_setup_and_stored_credentials_are_not_connected():
    assert build_connection_health(None, now=NOW)["state"] == "pending_consent"
    health = build_connection_health(CREDS, now=NOW)
    assert health["state"] == "pending_consent"
    assert health["label"] != "Connected"


def test_healthy_legacy_connection_is_exposed_for_migration_not_mislabelled():
    health = build_connection_health({**CREDS, "refresh_token": "encrypted"}, active={**VERIFIED, "mode": "legacy"}, now=NOW)
    assert health["state"] == "legacy"
    assert health["next_action_label"] == "Migrate connection"


def test_partial_consent_and_interrupted_setup_are_resumable():
    health = build_connection_health(CREDS, pending={"verification_tenant": 1, "verification_workload": 0}, now=NOW)
    assert health["state"] == "partially_ready"
    assert health["next_action_label"] == "Complete verification"
    assert health["next_action_url"] == "/m365"
    assert health["has_pending_candidate"] is True
    assert all(item["help"] for item in health["workloads"])


def test_fully_verified_candidate_still_requires_activation():
    health = build_connection_health(CREDS, pending=VERIFIED, now=NOW)

    assert health["state"] == "partially_ready"
    assert health["has_pending_candidate"] is True


def test_wrong_tenant_requires_reconnect():
    health = build_connection_health(CREDS, pending={"verification_error": "different tenant"}, now=NOW)
    assert health["state"] == "reconnect_required"


def test_managed_connection_requires_permissions_for_healthy_state():
    healthy = build_connection_health(CREDS, active=VERIFIED, permission_results=[{"all_ok": True}], now=NOW)
    degraded = build_connection_health(CREDS, active=VERIFIED, permission_results=[{"all_ok": False}], now=NOW)
    assert healthy["state"] == "healthy"
    assert degraded["state"] == "degraded"


def test_expired_connection_preserves_reconnect_action_for_rollback_path():
    health = build_connection_health({**CREDS, "client_secret_expires_at": NOW - timedelta(seconds=1)}, active=VERIFIED, now=NOW)
    assert health["state"] == "reconnect_required"
    assert health["next_action_url"] == "/m365/discover"


def test_canonical_diagnostics_has_one_permission_table_and_accessible_controls():
    template = Path("app/templates/m365/diagnostics.html").read_text()
    assert template.count('id="m365-permission-table"') == 1
    assert template.count('id="permission-filter"') == 1
    assert "Requested" in template and "Consented" in template and "Operational" in template
    assert "data-permission-filter" in template
