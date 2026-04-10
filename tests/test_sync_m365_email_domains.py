"""Tests for the sync_m365_email_domains feature."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.main as main_module
import app.services.m365 as m365_service
from app.repositories import companies as companies_repo
from app.services.scheduler import COMMANDS_BY_MODULE


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# sync_email_domains (m365 service)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sync_email_domains_adds_new_domains(monkeypatch):
    """New verified domains are merged into the company's email domain list."""
    monkeypatch.setattr(
        m365_service,
        "acquire_access_token",
        AsyncMock(return_value="fake-token"),
    )
    monkeypatch.setattr(
        m365_service,
        "_graph_get",
        AsyncMock(return_value={
            "value": [
                {"id": "contoso.com", "isVerified": True},
                {"id": "contoso.onmicrosoft.com", "isVerified": True},
                {"id": "fabrikam.com", "isVerified": True},
                {"id": "unverified.com", "isVerified": False},
            ]
        }),
    )
    monkeypatch.setattr(
        companies_repo,
        "get_email_domains_for_company",
        AsyncMock(return_value=[]),
    )
    replaced: list[tuple] = []

    async def fake_replace(company_id, domains):
        replaced.append((company_id, sorted(domains)))

    monkeypatch.setattr(companies_repo, "replace_company_email_domains", fake_replace)

    result = await m365_service.sync_email_domains(7)

    assert set(result["added"]) == {"contoso.com", "fabrikam.com"}
    assert result["existing"] == []
    assert len(replaced) == 1
    assert replaced[0][0] == 7
    assert sorted(replaced[0][1]) == ["contoso.com", "fabrikam.com"]


@pytest.mark.anyio
async def test_sync_email_domains_excludes_onmicrosoft(monkeypatch):
    """*.onmicrosoft.com domains are never added."""
    monkeypatch.setattr(
        m365_service,
        "acquire_access_token",
        AsyncMock(return_value="fake-token"),
    )
    monkeypatch.setattr(
        m365_service,
        "_graph_get",
        AsyncMock(return_value={
            "value": [
                {"id": "contoso.onmicrosoft.com", "isVerified": True},
                {"id": "tenant.onmicrosoft.com", "isVerified": True},
            ]
        }),
    )
    monkeypatch.setattr(
        companies_repo,
        "get_email_domains_for_company",
        AsyncMock(return_value=[]),
    )
    replaced: list = []

    async def fake_replace(company_id, domains):
        replaced.append(domains)

    monkeypatch.setattr(companies_repo, "replace_company_email_domains", fake_replace)

    result = await m365_service.sync_email_domains(7)

    assert result["added"] == []
    assert replaced == [], "replace should not be called when there are no new domains"


@pytest.mark.anyio
async def test_sync_email_domains_preserves_existing(monkeypatch):
    """Domains already on the company record are kept; only genuinely new ones are added."""
    monkeypatch.setattr(
        m365_service,
        "acquire_access_token",
        AsyncMock(return_value="fake-token"),
    )
    monkeypatch.setattr(
        m365_service,
        "_graph_get",
        AsyncMock(return_value={
            "value": [
                {"id": "existing.com", "isVerified": True},
                {"id": "newdomain.com", "isVerified": True},
            ]
        }),
    )
    monkeypatch.setattr(
        companies_repo,
        "get_email_domains_for_company",
        AsyncMock(return_value=["existing.com"]),
    )
    replaced: list[tuple] = []

    async def fake_replace(company_id, domains):
        replaced.append((company_id, sorted(domains)))

    monkeypatch.setattr(companies_repo, "replace_company_email_domains", fake_replace)

    result = await m365_service.sync_email_domains(3)

    assert result["added"] == ["newdomain.com"]
    assert result["existing"] == ["existing.com"]
    assert replaced[0][1] == ["existing.com", "newdomain.com"]


@pytest.mark.anyio
async def test_sync_email_domains_no_new_domains(monkeypatch):
    """When all tenant domains already exist, nothing is written to the database."""
    monkeypatch.setattr(
        m365_service,
        "acquire_access_token",
        AsyncMock(return_value="fake-token"),
    )
    monkeypatch.setattr(
        m365_service,
        "_graph_get",
        AsyncMock(return_value={
            "value": [
                {"id": "already.com", "isVerified": True},
            ]
        }),
    )
    monkeypatch.setattr(
        companies_repo,
        "get_email_domains_for_company",
        AsyncMock(return_value=["already.com"]),
    )
    replaced: list = []

    async def fake_replace(company_id, domains):
        replaced.append(domains)

    monkeypatch.setattr(companies_repo, "replace_company_email_domains", fake_replace)

    result = await m365_service.sync_email_domains(5)

    assert result["added"] == []
    assert replaced == []


# ---------------------------------------------------------------------------
# _best_effort_sync_m365_email_domains (main helper)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_best_effort_sync_does_not_raise_on_error(monkeypatch):
    """The helper swallows errors so the OAuth callback is never broken."""
    monkeypatch.setattr(
        main_module.m365_service,
        "sync_email_domains",
        AsyncMock(side_effect=RuntimeError("Graph error")),
    )

    # Should not raise
    await main_module._best_effort_sync_m365_email_domains(99)


@pytest.mark.anyio
async def test_best_effort_sync_calls_sync_email_domains(monkeypatch):
    """The helper calls m365_service.sync_email_domains with the correct company_id."""
    mock_sync = AsyncMock(return_value={"added": ["example.com"], "existing": []})
    monkeypatch.setattr(main_module.m365_service, "sync_email_domains", mock_sync)

    await main_module._best_effort_sync_m365_email_domains(42)

    mock_sync.assert_called_once_with(42)


# ---------------------------------------------------------------------------
# Scheduler dispatch for sync_m365_email_domains
# ---------------------------------------------------------------------------

def test_sync_m365_email_domains_in_commands_by_module():
    """sync_m365_email_domains is listed under the m365 module commands."""
    assert "sync_m365_email_domains" in COMMANDS_BY_MODULE.get("m365", set())
