from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import vault as routes
from app.repositories import credential_features


@pytest.mark.anyio
async def test_company_feature_flag_is_dark_by_default(monkeypatch):
    monkeypatch.setattr(
        credential_features.db, "fetch_one", AsyncMock(return_value=None)
    )
    assert await credential_features.is_enabled(91) is False
    sql, params = credential_features.db.fetch_one.await_args.args
    assert "company_id = %s" in sql
    assert params == (91,)


@pytest.mark.anyio
async def test_disabled_company_fails_closed_before_membership(monkeypatch):
    monkeypatch.setattr(
        routes.feature_repo, "is_enabled", AsyncMock(return_value=False)
    )
    membership = AsyncMock()
    monkeypatch.setattr(routes.user_company_repo, "get_user_company", membership)
    with pytest.raises(HTTPException) as denied:
        await routes._authorize({"id": 2, "is_super_admin": True}, 91, write=True)
    assert denied.value.status_code == 404
    membership.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_status_blocks_enablement_without_safe_prerequisites(monkeypatch):
    monkeypatch.setattr(
        routes.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 91})
    )
    monkeypatch.setattr(routes.feature_repo, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        routes.feature_repo,
        "missing_prerequisite_tables",
        AsyncMock(return_value=["credential_secret_versions"]),
    )
    monkeypatch.setattr(routes.vault_crypto, "ensure_configured", lambda: None)

    result = await routes._feature_status(91)

    assert result["state"] == "disabled"
    assert result["can_enable"] is False
    assert result["enabled"] is False
    assert result["diagnostics"] == [
        "Required vault database migrations have not completed: credential_secret_versions"
    ]
    assert "VAULT_KEYS=" not in " ".join(result["diagnostics"])


@pytest.mark.anyio
async def test_rollout_status_reports_ready_without_exposing_key_material(monkeypatch):
    monkeypatch.setattr(
        routes.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 91})
    )
    monkeypatch.setattr(routes.feature_repo, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(
        routes.feature_repo, "missing_prerequisite_tables", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(routes.vault_crypto, "ensure_configured", lambda: None)

    result = await routes._feature_status(91)

    assert result["state"] == "ready"
    assert result["can_enable"] is True
    assert result["diagnostics"] == []


def test_super_admin_rollout_ui_requires_confirmation_and_retains_records():
    text = Path("app/templates/admin/company_edit.html").read_text(encoding="utf-8")
    assert 'id="vault-confirm"' in text
    assert "I understand this is an audited access-control change" in text
    assert (
        "Encrypted records are retained" not in text
    )  # supplied by the safe status API
    repository = Path("app/repositories/credential_features.py").read_text(
        encoding="utf-8"
    )
    assert "DELETE FROM credentials" not in repository


def test_release_runbook_covers_required_leakage_and_recovery_surfaces():
    text = Path("docs/operations/credential-rollout.md").read_text(encoding="utf-8")
    required = (
        "ticket and KB",
        "notifications/email",
        "analytics payloads",
        "audit/event JSON",
        "database-only restore",
        "Two simultaneous consumes",
        "offboarding",
        "Rollback is a flag update",
    )
    assert all(term in text for term in required)


def test_roadmap_records_all_eighteen_steps_in_order():
    text = Path("docs/roadmaps/itdoc-18-step-roadmap.md").read_text(encoding="utf-8")
    positions = [text.index(f"ITDOC {step:02d}/18") for step in range(1, 19)]
    assert positions == sorted(positions)
