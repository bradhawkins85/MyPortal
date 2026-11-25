from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.rmm_patching import FEATURE_RELEASES, script_content


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
BUILTIN = Path(__file__).resolve().parents[1] / "app" / "services" / "rmm_builtin"
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Requires PowerShell; Windows Update is mocked")

UPGRADES = "3689bdc8-b205-4af4-8d4a-a63924c5e9d5"
SECURITY = "0fa1201d-4330-4fa8-8ae9-b877473b6441"
UPDATES = "cd5ffd1e-e932-4e3a-bf74-18bf0b1bbd83"
UPGRADE_24 = {"title": "Funktionsupdate für Windows 11, Version 24H2", "category": UPGRADES}
UPGRADE_25 = {"title": "Windows 11, version 25H2 x64 2026-10B", "category": UPGRADES}
UPGRADE_26 = {"title": "Windows 11, version 26H2", "category": UPGRADES, "optional": True}
ENABLEMENT_26 = {"title": "Feature Update via Enablement Package", "kb": "5121794", "category": UPDATES}
BASELINE_26 = {"title": "2026-09 Preview Update", "kb": "5124010", "category": UPDATES, "optional": True}
LATER_CU = {"title": "2026-11 Security Update (KB5132061) (26100.9974)", "kb": "5132061", "category": SECURITY}
UNRELATED_CU = {"title": "2026-11 Cumulative Update for Windows 11 Version 26H2", "kb": "5132062", "category": SECURITY}


FAKE_UPDATES = r'''
function Get-TestOffers {
    $id = 0
    foreach ($item in ($env:TEST_OFFERS | ConvertFrom-Json)) {
        $id++
        $update = [pscustomobject]@{
            Title = $item.title
            Identity = [pscustomobject]@{ UpdateID = ('00000000-0000-0000-0000-{0:d12}' -f $id); RevisionNumber = 1 }
            Categories = @([pscustomobject]@{ CategoryID = $item.category })
            KBArticleIDs = @($item.kb | Where-Object { $_ })
            IsHidden = [bool]$item.hidden
            IsBeta = $false
            DeploymentAction = $(if ($item.optional) { 4 } else { 1 })
            LastDeploymentChangeTime = [datetime]::UtcNow.AddDays(-[int]$item.days_old)
            EulaAccepted = $false
            InstallationBehavior = [pscustomobject]@{ CanRequestUserInput = [bool]$item.interactive }
        }
        $update | Add-Member ScriptMethod AcceptEula { $this.EulaAccepted = $true }
        $update
    }
}
'''


def _run(tmp_path, source, **env):
    harness = tmp_path / "harness.ps1"
    harness.write_text(source, encoding="utf-8")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        env={**os.environ, **env}, capture_output=True, text=True, timeout=30,
    )


def _feature(target="26H2"):
    return {"target": target, "releases": FEATURE_RELEASES}


@pytest.mark.parametrize("build,ubr,offers,chosen,status", [
    (22631, 4000, [UPGRADE_24, UPGRADE_26], UPGRADE_26, "Installing the 26H2 feature update"),
    (22631, 4000, [UPGRADE_24, ENABLEMENT_26], UPGRADE_24, "24H2 prerequisite feature update"),
    (22631, 4000, [UPGRADE_25], UPGRADE_25, "25H2 prerequisite feature update"),
    (22631, 1000, [UPGRADE_24, {"title": "2024-05 Cumulative Update", "kb": "5037853", "category": UPDATES}],
     {"title": "2024-05 Cumulative Update"}, "servicing prerequisite for 24H2"),
    (26100, 5000, [BASELINE_26], BASELINE_26, "servicing prerequisite for 26H2"),
    (26100, 5000, [BASELINE_26, LATER_CU, UNRELATED_CU], LATER_CU, "servicing prerequisite for 26H2"),
    (26100, 5000, [UNRELATED_CU], None, "reach build 26100.9550"),
    (26100, 9974, [UPGRADE_25, ENABLEMENT_26], ENABLEMENT_26, "Installing the 26H2 feature update"),
    (26200, 9974, [ENABLEMENT_26], ENABLEMENT_26, "Installing the 26H2 feature update"),
    (26300, 1000, [UPGRADE_26], None, "already at or beyond the target"),
    (28000, 1000, [UPGRADE_26], None, "separate servicing branch"),
])
def test_feature_route_uses_an_applicable_offer(tmp_path, build, ubr, offers, chosen, status):
    source = fr'''
$ErrorActionPreference = 'Stop'
. '{BUILTIN / 'windows_updates.ps1'}'
. '{BUILTIN / 'windows_feature_upgrade.ps1'}'
{FAKE_UPDATES}
$step = Get-MyPortalFeatureStep ($env:TEST_FEATURE | ConvertFrom-Json) @{{ client = $true; build = {build}; ubr = {ubr} }} `
    @(Get-TestOffers) (Get-MyPortalUpdateBlocks) ([datetime]::UtcNow)
ConvertTo-Json -InputObject @{{ message = $step.message; title = $step.update.Title }} -Compress
'''
    result = _run(tmp_path, source, TEST_FEATURE=json.dumps(_feature()), TEST_OFFERS=json.dumps(offers),
                  MYPORTAL_WU_BLOCKS="")
    assert result.returncode == 0, result.stdout + result.stderr
    decision = json.loads(result.stdout)
    assert decision["title"] == (chosen["title"] if chosen else None)
    assert status in decision["message"]


@pytest.mark.parametrize("offer,blocks,days,text", [
    (UPGRADE_26, {"update_ids": ["00000000-0000-0000-0000-000000000002"]}, 0, "blocked in MyPortal"),
    ({**UPGRADE_26, "hidden": True}, {}, 0, "hidden on this device"),
    ({**UPGRADE_26, "interactive": True}, {}, 0, "requires someone at the device"),
    (UPGRADE_26, {}, 2, "within the policy deferral period"),
])
def test_a_held_target_does_not_start_a_prerequisite_upgrade(tmp_path, offer, blocks, days, text):
    source = fr'''
$ErrorActionPreference = 'Stop'
. '{BUILTIN / 'windows_updates.ps1'}'
. '{BUILTIN / 'windows_feature_upgrade.ps1'}'
{FAKE_UPDATES}
$step = Get-MyPortalFeatureStep ($env:TEST_FEATURE | ConvertFrom-Json) @{{ client = $true; build = 22631; ubr = 4000 }} `
    @(Get-TestOffers) (Get-MyPortalUpdateBlocks) ([datetime]::UtcNow.AddDays(-{days}))
if ($step.update) {{ throw 'A held target must not install a different release.' }}
$step.message
'''
    result = _run(tmp_path, source, TEST_FEATURE=json.dumps(_feature()),
                  TEST_OFFERS=json.dumps([{**UPGRADE_24, "days_old": 100}, offer]), MYPORTAL_WU_BLOCKS=json.dumps(blocks))
    assert result.returncode == 0, result.stdout + result.stderr
    assert text in result.stdout


PATCH_MOCKS = r'''
$global:Events = @()
function New-Object {
    param([string]$TypeName, [object[]]$ArgumentList, [string]$ComObject)
    if ($TypeName -eq 'Threading.Mutex') { return [Threading.Mutex]::new($false) }
    switch ($ComObject) {
        'Microsoft.Update.Session' {
            $session = [pscustomobject]@{ ClientApplicationID = '' }
            $session | Add-Member ScriptMethod CreateUpdateSearcher {
                $searcher = [pscustomobject]@{ Online = $true }
                $searcher | Add-Member ScriptMethod Search { param($query)
                    $offers = @(Get-TestOffers)
                    if ($query -match 'RebootRequired=1') {
                        if ($this.Online -or $query -notmatch 'RevisionNumber=1') { throw "Invalid recovery query: $query" }
                        if ($env:TEST_RECOVERY_FAIL) { return [pscustomobject]@{ ResultCode = 3; Updates = @() } }
                        $offers = @($offers | Where-Object { $query -match $_.Identity.UpdateID })
                    } elseif ($env:TEST_FEATURE -and $query -notmatch "DeploymentAction='OptionalInstallation'") {
                        throw 'Feature queries must include optional offers.'
                    }
                    $collection = New-Object -ComObject Microsoft.Update.UpdateColl
                    foreach ($offer in $offers) { [void]$collection.Add($offer) }
                    if ($query -notmatch 'RebootRequired=1') {
                        return [pscustomobject]@{ ResultCode = 2; Updates = $collection.Items }
                    }
                    return [pscustomobject]@{ ResultCode = 2; Updates = $collection }
                }
                return $searcher
            }
            $session | Add-Member ScriptMethod CreateUpdateDownloader {
                $downloader = [pscustomobject]@{ Updates = $null }
                $downloader | Add-Member ScriptMethod Download { [pscustomobject]@{ ResultCode = 2 } }
                return $downloader
            }
            $session | Add-Member ScriptMethod CreateUpdateInstaller {
                $installer = [pscustomobject]@{ Updates = $null; AllowSourcePrompts = $true; Installed = $false }
                $installer | Add-Member ScriptMethod Install {
                    $this.Installed = $true
                    foreach ($item in $this.Updates.Items) { $global:Events += "installed $($item.Title)" }
                    $result = [pscustomobject]@{ ResultCode = 2; RebootRequired = $true }
                    $result | Add-Member ScriptMethod GetUpdateResult { param($i) [pscustomobject]@{ ResultCode = 2; HResult = 0 } }
                    if ($env:TEST_DEFER_AFTER_INSTALL) { [PatchSessions]::HasUser = $true }
                    return $result
                }
                return $installer
            }
            return $session
        }
        'Microsoft.Update.UpdateColl' {
            $collection = [pscustomobject]@{ Items = [Collections.ArrayList]::new() }
            $collection | Add-Member ScriptProperty Count { $this.Items.Count }
            $collection | Add-Member ScriptMethod Add { param($u) $this.Items.Add($u) }
            $collection | Add-Member ScriptMethod Item { param($i) $this.Items[$i] }
            return $collection
        }
        'Microsoft.Update.Installer' { return [pscustomobject]@{ IsBusy = $false } }
        default { return Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName -ArgumentList $ArgumentList }
    }
}
function Get-CimInstance { @{ LastBootUpTime = [datetime]$env:TEST_BOOT } }
function Get-ItemProperty {
    param($Path, $Name, $ErrorAction)
    if ($Path -like '*Windows NT*') {
        return @{ InstallationType = 'Client'; CurrentBuildNumber = $env:TEST_BUILD; UBR = [int]$env:TEST_UBR }
    }
    if ($Name -eq 'PendingFeature' -and (Microsoft.PowerShell.Management\Test-Path $env:TEST_STATE)) {
        return @{ PendingFeature = (Get-Content $env:TEST_STATE -Raw) }
    }
}
function Test-Path { param($Path) return $Path -like '*PatchManagement' }
function Set-ItemProperty { param($Path, $Name, $Value) Set-Content $env:TEST_STATE $Value }
function Remove-ItemProperty { param($Path, $Name, $ErrorAction) Remove-Item $env:TEST_STATE }
function Send-MyPortalWindowsUpdateState { }
function Invoke-MyPortalFeatureCommit($installer) {
    if ($installer.Updates.Count -ne 1) { throw 'Commit requires an installer with exactly one staged update.' }
    $global:Events += "committed $($installer.Updates.Item(0).Title), same installer: $($installer.Installed)"
}
function shutdown.exe { $global:Events += 'shutdown'; $global:LASTEXITCODE = 0 }
Add-Type 'public static class PatchSessions { public static bool HasUser; public static bool HasUsers() { return HasUser; } }'
'''


def _run_patch(tmp_path, *, target="26H2", offers, blocks=None, **env):
    policy = {"categories": ["Updates"], "deferral_days": 0}
    plan = {"windows": policy, "apps": [], "reboot": {"reboot_mode": "no_users", "reboot_delay_minutes": 5,
                                                    "immediate": True}}
    if target:
        plan["feature_upgrade"] = _feature(target)
    body = script_content().replace("# Runs as LocalSystem", PATCH_MOCKS + "\n# Runs as LocalSystem", 1)
    source = FAKE_UPDATES + "\n" + body + '\nWrite-Output ("EVENTS=" + ($global:Events -join " | "))\n'
    return _run(tmp_path, source, MYPORTAL_PATCH_PLAN=json.dumps(plan), MYPORTAL_WU_BLOCKS=json.dumps(blocks or {}),
                TEST_OFFERS=json.dumps(offers), TEST_FEATURE=target or "", TEST_STATE=str(tmp_path / "state.json"),
                **{"TEST_BOOT": "2026-11-24T00:00:00Z", "TEST_BUILD": "22631", "TEST_UBR": "4000", **env})


def test_install_commits_on_the_same_installer_before_authorized_restart(tmp_path):
    result = _run_patch(tmp_path, offers=[UPGRADE_24, UPGRADE_26, LATER_CU])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EVENTS=installed Windows 11, version 26H2 | committed Windows 11, version 26H2, same installer: True | shutdown" in result.stdout
    assert json.loads((tmp_path / "state.json").read_text())["stage"] == "committed"
    repeated = _run_patch(tmp_path, offers=[UPGRADE_24, UPGRADE_26])
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert "EVENTS=shutdown" in repeated.stdout


def test_deferred_commit_requires_the_current_target_and_fresh_blocks(tmp_path):
    offers = [UPGRADE_24]
    staged = _run_patch(tmp_path, offers=offers, TEST_DEFER_AFTER_INSTALL="1")
    assert staged.returncode == 0, staged.stdout + staged.stderr
    assert f"EVENTS=installed {UPGRADE_24['title']}\n" in staged.stdout
    assert json.loads((tmp_path / "state.json").read_text())["stage"] == "staged"

    other_target = _run_patch(tmp_path, target="25H2", offers=offers)
    assert "policy for the staged feature target" in other_target.stdout and "EVENTS=\n" in other_target.stdout
    blocked = _run_patch(tmp_path, offers=offers, blocks={"update_ids": ["00000000-0000-0000-0000-000000000001"]})
    assert "staged feature update is blocked" in blocked.stdout and "EVENTS=\n" in blocked.stdout
    unavailable = _run_patch(tmp_path, offers=offers, TEST_RECOVERY_FAIL="1")
    assert unavailable.returncode != 0
    assert json.loads((tmp_path / "state.json").read_text())["stage"] == "staged"

    resumed = _run_patch(tmp_path, offers=offers)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert f"EVENTS=committed {UPGRADE_24['title']}, same installer: False | shutdown" in resumed.stdout


def test_quality_policy_excludes_known_feature_packages(tmp_path):
    result = _run_patch(tmp_path, target=None, offers=[ENABLEMENT_26])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EVENTS=\n" in result.stdout


def test_policy_continues_to_the_target_after_prerequisite_restarts(tmp_path):
    first = _run_patch(tmp_path, offers=[UPGRADE_24, ENABLEMENT_26])
    assert first.returncode == 0, first.stdout + first.stderr
    assert f"EVENTS=installed {UPGRADE_24['title']} | committed {UPGRADE_24['title']}" in first.stdout

    servicing = _run_patch(tmp_path, offers=[BASELINE_26], TEST_BOOT="2026-11-25T00:00:00Z",
                           TEST_BUILD="26100", TEST_UBR="5000")
    assert servicing.returncode == 0, servicing.stdout + servicing.stderr
    assert f"EVENTS=installed {BASELINE_26['title']} | shutdown" in servicing.stdout
    assert not (tmp_path / "state.json").exists()

    target = _run_patch(tmp_path, offers=[ENABLEMENT_26], TEST_BOOT="2026-11-26T00:00:00Z",
                        TEST_BUILD="26100", TEST_UBR="9550")
    assert target.returncode == 0, target.stdout + target.stderr
    assert f"EVENTS=installed {ENABLEMENT_26['title']} | committed {ENABLEMENT_26['title']}" in target.stdout

    verified = _run_patch(tmp_path, offers=[ENABLEMENT_26], TEST_BOOT="2026-11-27T00:00:00Z",
                          TEST_BUILD="26300", TEST_UBR="1000")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "already at or beyond the target" in verified.stdout and "EVENTS=\n" in verified.stdout
    assert not (tmp_path / "state.json").exists()
