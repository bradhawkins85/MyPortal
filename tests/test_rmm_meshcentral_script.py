"""Run the Windows activation with mocked download, service and process APIs."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not POWERSHELL, reason='Requires PowerShell; Windows calls are mocked')
def test_meshcentral_installer_launches_without_argument_list(tmp_path):
    harness = tmp_path / 'mesh-harness.ps1'
    harness.write_text(r'''
$ErrorActionPreference = 'Stop'
$env:TEMP = $env:TEST_TEMP
$env:MESH_SITE = 'https://mesh.example.com'
$env:MESH_GROUP_ID = 'group-id'
$env:PROCESSOR_ARCHITECTURE = 'AMD64'
$script:launches = 0
function Invoke-WebRequest { param($Uri, $OutFile, [switch]$UseBasicParsing, $TimeoutSec) }
function Get-Service { param($Name, $ErrorAction); [pscustomobject]@{Status = 'Running'} }
function Start-Process {
    param($FilePath, [ValidateNotNullOrEmpty()][string[]]$ArgumentList,
          [switch]$PassThru, $WindowStyle, $RedirectStandardOutput, $RedirectStandardError)
    $script:launches++
    if ($script:launches -eq 1 -and $PSBoundParameters.ContainsKey('ArgumentList')) {
        throw 'The installer must launch without ArgumentList.'
    }
    if ($script:launches -eq 2 -and ($ArgumentList.Count -ne 1 -or $ArgumentList[0] -ne '"value with spaces"')) {
        throw 'Nonempty arguments must retain quoting.'
    }
    if (-not $PassThru -or $WindowStyle -ne 'Hidden') { throw 'Missing process options.' }
    Set-Content -LiteralPath $RedirectStandardOutput -Value 'installer output'
    Set-Content -LiteralPath $RedirectStandardError -Value ''
    $process = [pscustomobject]@{Handle = 1; ExitCode = 0}
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Timeout); return $true }
    return $process
}
. $env:TEST_MESH_SCRIPT
$output = Invoke-MeshCommand -File 'test.exe' -Arguments @('value with spaces') -Step 'test'
if ($output.Trim() -ne 'installer output') { throw 'Missing command output.' }
if ($script:launches -ne 2) { throw 'Expected installer and argument-bearing command.' }
if (Get-ChildItem -LiteralPath $env:TEMP) { throw 'Temporary files were not cleaned up.' }
''')
    temp = tmp_path / 'temp'
    temp.mkdir()
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
        env={**os.environ, 'TEST_TEMP': str(temp),
             'TEST_MESH_SCRIPT': str(ROOT / 'app/services/rmm_builtin/meshcentral_activate.ps1')},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
