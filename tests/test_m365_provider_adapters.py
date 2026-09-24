import asyncio

import pytest

from app.services import m365_providers as providers


def test_provider_contracts_keep_teams_commands_out_of_exchange():
    teams = providers.PROVIDERS["teams"]
    exchange = providers.PROVIDERS["exchange"]
    expected = {
        "Get-CsTeamsMeetingPolicy",
        "Get-CsTenantFederationConfiguration",
        "Get-CsTeamsClientConfiguration",
    }
    assert expected <= teams.commands
    assert expected.isdisjoint(exchange.commands)
    assert teams.audience == (
        "https://graph.microsoft.com/.default",
        "https://api.interfaces.records.teams.microsoft.com/.default",
    )
    assert "Organization.Read.All" in teams.rbac


def test_company_rollout_is_deny_by_default_and_supports_rollback(monkeypatch):
    monkeypatch.delenv("M365_TEAMS_PROVIDER_COMPANY_IDS", raising=False)
    assert providers.provider_enabled(42, "teams") is False
    monkeypatch.setenv("M365_TEAMS_PROVIDER_COMPANY_IDS", "7,42")
    assert providers.provider_enabled(42, "teams") is True
    assert providers.provider_enabled(8, "teams") is False
    monkeypatch.setenv("M365_TEAMS_PROVIDER_COMPANY_IDS", "")
    assert providers.provider_enabled(42, "teams") is False


def test_teams_rejects_unsupported_commands_before_starting_process():
    with pytest.raises(providers.ProviderCommandError) as exc:
        asyncio.run(providers.invoke_teams_command(
            tenant_id="tenant", graph_token="secret-a", teams_token="secret-b",
            command="Set-CsTeamsMeetingPolicy", parameters={},
        ))
    assert exc.value.kind == "unsupported"
    assert "secret" not in str(exc.value)
