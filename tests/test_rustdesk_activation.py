"""Exercise the shipped PowerShell script with mocked Windows/network APIs.

Runs on Windows PowerShell or pwsh, without installing RustDesk or touching services.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(SHELL is None, reason="PowerShell is required")
SCRIPT = Path(__file__).resolve().parents[1] / "app/services/rmm_builtin/rustdesk_activate.ps1"

MOCKS = r'''
$global:testevents = [Collections.Generic.List[string]]::new()
$global:testinstalled = $env:TEST_SCENARIO -eq 'installed'
$global:testserviceInstalled = $global:testinstalled
$env:ProgramFiles = $env:TEST_PROGRAMFILES
$env:ProgramW6432 = $env:TEST_PROGRAMFILES
${env:ProgramFiles(x86)} = ''
$global:testexe = Join-Path $env:ProgramFiles 'RustDesk/rustdesk.exe'
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) }
function Get-Command { param($Name, $ErrorAction) }
function Test-Path {
    param($LiteralPath)
    return $global:testinstalled -and $LiteralPath -eq $global:testexe
}
function Start-Sleep { param($Seconds) }
function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec)
    $global:testevents.Add('release')
    if ($Uri -ne 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest') { throw 'Wrong release endpoint' }
    $assets = @('x86_64', 'x86', 'aarch64') | ForEach-Object {
        [pscustomobject]@{ name = "rustdesk-9.8.7-$_.exe"; browser_download_url = "https://github.com/rustdesk/rustdesk/releases/download/9.8.7/rustdesk-9.8.7-$_.exe" }
    }
    if ($env:TEST_SCENARIO -eq 'missing-asset') { $assets = @() }
    return [pscustomobject]@{ tag_name = '9.8.7'; assets = $assets }
}
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec)
    $global:testevents.Add("download:$Uri")
    if ($env:TEST_SCENARIO -eq 'download-failure') { throw 'Download failed' }
    Set-Content -LiteralPath $OutFile -Value 'mock installer'
}
function Start-Process {
    param($FilePath, $ArgumentList, [switch]$PassThru, $WindowStyle, $RedirectStandardOutput, $RedirectStandardError)
    $action = $ArgumentList[0].Trim('"')
    $global:testevents.Add($action)
    $output = ''
    if ($action -eq '--silent-install') { $global:testinstalled = $true }
    if ($action -eq '--install-service') { $global:testserviceInstalled = $true }
    if ($action -eq '--get-id') { $output = '123 456 789' }
    if ($action -eq '--config' -and $ArgumentList[1] -ne '"config with spaces"') { throw 'Config argument was not preserved' }
    Set-Content -LiteralPath $RedirectStandardOutput -Value $output
    Set-Content -LiteralPath $RedirectStandardError -Value ''
    $code = if ($env:TEST_SCENARIO -eq 'config-failure' -and $action -eq '--config') { 1 } else { 0 }
    $process = [pscustomobject]@{ Id = 42; Handle = 42; ExitCode = $code; Action = $action }
    $process | Add-Member ScriptMethod WaitForExit {
        param($Milliseconds)
        if ($Milliseconds -le 0 -or $Milliseconds -gt 90000) { throw 'Invalid command timeout' }
        return -not ($env:TEST_SCENARIO -eq 'command-timeout' -and $this.Action -eq '--config')
    }
    return $process
}
function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    $global:testevents.Add('kill')
}
function Get-Service {
    param($Name, $ErrorAction)
    if (-not $global:testserviceInstalled) { return }
    $service = [pscustomobject]@{ Status = 'Running' }
    $service | Add-Member ScriptMethod Stop { $global:testevents.Add('stop') }
    $service | Add-Member ScriptMethod Start { $global:testevents.Add('start') }
    $service | Add-Member ScriptMethod WaitForStatus { param($Status, $Timeout) }
    return $service
}
function Set-Service { param($Name, $StartupType) }
try {
    & $env:TEST_SCRIPT
    $failure = $null
} catch {
    $failure = $_.Exception.Message
}
Write-Output ('RESULT:' + (ConvertTo-Json -Compress -InputObject @{ events = @($global:testevents.ToArray()); error = $failure }))
'''


def run_script(tmp_path, scenario, architecture="AMD64", override=""):
    harness = tmp_path / "harness.ps1"
    harness.write_text(MOCKS)
    env = {
        **os.environ,
        "TEST_SCRIPT": str(SCRIPT),
        "TEST_SCENARIO": scenario,
        "TEST_PROGRAMFILES": str(tmp_path / "Program Files"),
        "TEMP": str(tmp_path),
        "PROCESSOR_ARCHITECTURE": architecture,
        "PROCESSOR_ARCHITEW6432": "",
        "RUSTDESK_INSTALLER_URL": override,
        "RUSTDESK_CFG": "config with spaces",
        "MYPORTAL_RUSTDESK_PASSWORD": "secret-session-password",
    }
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        env=env, capture_output=True, text=True, timeout=30, check=True,
    )
    assert "secret-session-password" not in result.stdout + result.stderr
    report = next(line.removeprefix("RESULT:") for line in result.stdout.splitlines() if line.startswith("RESULT:"))
    return json.loads(report), result.stdout


def test_installed_client_is_configured_without_a_download(tmp_path):
    report, output = run_script(tmp_path, "installed")
    assert report["error"] is None
    assert report["events"] == ["--config", "stop", "start", "--password", "--get-id"]
    assert "##myportal[session.id]=123456789" in output


@pytest.mark.parametrize("architecture,asset", [("AMD64", "x86_64"), ("X86", "x86"), ("ARM64", "aarch64")])
def test_missing_client_is_installed_before_configuration(tmp_path, architecture, asset):
    report, output = run_script(tmp_path, "missing", architecture)
    assert report["error"] is None
    assert report["events"] == [
        "release", f"download:https://github.com/rustdesk/rustdesk/releases/download/9.8.7/rustdesk-9.8.7-{asset}.exe",
        "--silent-install", "--install-service", "--config", "stop", "start", "--password", "--get-id",
    ]
    assert "##myportal[session.id]=123456789" in output


def test_installer_override_skips_github_discovery(tmp_path):
    report, _ = run_script(tmp_path, "missing", override="https://downloads.example.com/custom.exe")
    assert report["error"] is None
    assert report["events"][0] == "download:https://downloads.example.com/custom.exe"
    assert "release" not in report["events"]


@pytest.mark.parametrize("scenario,message", [
    ("download-failure", "Download failed"),
    ("missing-asset", "no unique Windows"),
    ("config-failure", "failed during: server configuration"),
    ("command-timeout", "timed out during: server configuration"),
])
def test_failures_return_an_error_without_a_ready_session(tmp_path, scenario, message):
    report, output = run_script(tmp_path, scenario)
    assert message in report["error"]
    assert "--password" not in report["events"]
    assert "##myportal[session.id]=" not in output
    if scenario == "command-timeout":
        assert report["events"][-1] == "kill"
