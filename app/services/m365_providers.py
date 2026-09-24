"""Validated workload adapters for Microsoft 365 administrative commands.

The command inventories in this module are deliberately closed allow-lists.  In
particular, Teams cmdlets must never be sent to Exchange's private REST route.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ProviderContract:
    audience: tuple[str, ...]
    module: str | None
    minimum_version: str | None
    commands: frozenset[str]
    rbac: str


PROVIDERS: Mapping[str, ProviderContract] = {
    "graph": ProviderContract(("https://graph.microsoft.com/.default",), None, None,
        frozenset(), "Permissions declared for the individual Graph operation"),
    "exchange": ProviderContract(("https://outlook.office365.com/.default",),
        "ExchangeOnlineManagement", "3.7.0", frozenset({
            "Get-Mailbox", "Set-Mailbox", "Get-MailboxPermission", "Add-MailboxPermission",
            "Remove-MailboxPermission", "Get-EXOMailbox", "Get-EXOMailboxPermission",
            "Get-MailboxFolderStatistics", "Get-MailboxFolderPermission",
            "Remove-MailboxFolderPermission", "Get-CalendarProcessing", "Set-CalendarProcessing",
            "Get-OrganizationConfig", "Set-OrganizationConfig", "Get-OwaMailboxPolicy",
            "Set-OwaMailboxPolicy", "Get-RetentionPolicy", "Set-RetentionPolicy",
            "Get-RemoteDomain", "Get-DkimSigningConfig", "Get-AntiPhishPolicy",
            "Set-AntiPhishPolicy", "Get-HostedOutboundSpamFilterPolicy",
        }), "Exchange RBAC roles scoped to each cmdlet"),
    "teams": ProviderContract(("https://graph.microsoft.com/.default",
        "https://api.interfaces.records.teams.microsoft.com/.default"),
        "MicrosoftTeams", "6.5.0", frozenset({"Get-CsTeamsMeetingPolicy",
        "Get-CsTenantFederationConfiguration", "Get-CsTeamsClientConfiguration"}),
        "Teams Service Administrator (and Organization.Read.All)"),
    "purview": ProviderContract(("https://ps.compliance.protection.outlook.com/.default",),
        "ExchangeOnlineManagement", "3.7.0", frozenset({"Get-ProtectionAlert",
        "New-ProtectionAlert", "Get-ComplianceSearch", "New-ComplianceSearch",
        "Start-ComplianceSearch", "New-ComplianceSearchAction"}),
        "Compliance Administrator or the least-privileged Purview role group"),
}


class ProviderCommandError(RuntimeError):
    """A safe, classified provider failure (never contains credentials)."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def provider_enabled(company_id: int, provider: str) -> bool:
    """Per-company additive rollout gate; an empty list keeps legacy behaviour."""
    raw = os.getenv(f"M365_{provider.upper()}_PROVIDER_COMPANY_IDS", "")
    enabled = {part.strip() for part in raw.split(",") if part.strip()}
    return "*" in enabled or str(company_id) in enabled


async def invoke_teams_command(
    *, tenant_id: str, graph_token: str, teams_token: str,
    command: str, parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one allow-listed cmdlet in an isolated MicrosoftTeams session.

    Tokens and the JSON parameter document travel via the child environment,
    not command-line arguments.  The short-lived process always disconnects.
    """
    contract = PROVIDERS["teams"]
    if command not in contract.commands:
        raise ProviderCommandError(f"Unsupported Microsoft Teams command: {command}", kind="unsupported")
    pwsh = shutil.which("pwsh")
    if not pwsh:
        raise ProviderCommandError("Microsoft Teams provider is unavailable: pwsh is not installed", kind="unsupported")
    script = r'''
$ErrorActionPreference = 'Stop'
try {
  $module = Get-Module -ListAvailable MicrosoftTeams | Sort-Object Version -Descending | Select-Object -First 1
  if (-not $module -or $module.Version -lt [version]'6.5.0') { throw 'MicrosoftTeams module 6.5.0 or newer is required' }
  Import-Module MicrosoftTeams -MinimumVersion 6.5.0
  Connect-MicrosoftTeams -TenantId $env:MP_TENANT -AccessTokens @($env:MP_GRAPH_TOKEN,$env:MP_TEAMS_TOKEN) | Out-Null
  $params = ConvertFrom-Json $env:MP_PARAMETERS -AsHashtable
  $result = & $env:MP_COMMAND @params
  @{ value = @($result) } | ConvertTo-Json -Depth 20 -Compress
} catch {
  @{ error = @{ message = $_.Exception.Message; category = [string]$_.CategoryInfo.Category } } | ConvertTo-Json -Compress
  exit 7
} finally { Disconnect-MicrosoftTeams -Confirm:$false -ErrorAction SilentlyContinue | Out-Null }
'''
    env = {**os.environ, "MP_TENANT": tenant_id, "MP_GRAPH_TOKEN": graph_token,
           "MP_TEAMS_TOKEN": teams_token, "MP_COMMAND": command,
           "MP_PARAMETERS": json.dumps(dict(parameters or {}), separators=(",", ":"))}
    proc = await asyncio.create_subprocess_exec(pwsh, "-NoLogo", "-NoProfile", "-NonInteractive",
        "-Command", script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
    stdout, _stderr = await proc.communicate()
    try:
        body = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderCommandError("Microsoft Teams returned an invalid command response", kind="command_body") from exc
    if proc.returncode:
        error = body.get("error", {}) if isinstance(body, dict) else {}
        message = str(error.get("message") or "Microsoft Teams command failed")
        category = str(error.get("category") or "")
        denied = any(term in (message + category).lower() for term in ("denied", "unauthorized", "permission"))
        raise ProviderCommandError(message, kind="rbac_denied" if denied else "command_body")
    if not isinstance(body, dict) or not isinstance(body.get("value"), list):
        raise ProviderCommandError("Microsoft Teams returned an unexpected response shape", kind="command_body")
    return body
