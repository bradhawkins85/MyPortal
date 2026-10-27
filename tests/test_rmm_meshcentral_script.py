"""Run the Windows activation with a delivered installer and mocked service and process APIs."""
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
$env:MESH_INSTALLER_PATH = Join-Path $env:TEMP 'mesh-installer.exe'
Set-Content -LiteralPath $env:MESH_INSTALLER_PATH -Value 'installer'
$env:MESH_GROUP_ID = 'group-id'
$env:PROCESSOR_ARCHITECTURE = 'AMD64'
$script:launches = 0
function Invoke-WebRequest { throw 'The endpoint must not download the installer.' }
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


@pytest.mark.parametrize('script_name', ['meshcentral_activate.sh', 'meshcentral_activate_macos.sh'])
@pytest.mark.parametrize('installer_exit', [0, 9])
def test_unix_activation_uses_delivered_installer_and_cleans_up(tmp_path, script_name, installer_exit):
    installer = tmp_path / 'delivered installer.sh'
    marker = tmp_path / 'ran'
    installer.write_text(f'#!/bin/bash\nprintf ran > "{marker}"\nexit {installer_exit}\n')
    commands = tmp_path / 'bin'
    commands.mkdir()
    for name in ('systemctl', 'pgrep', 'launchctl'):
        command = commands / name
        command.write_text('#!/bin/bash\nexit 0\n')
        command.chmod(0o700)
    identity = commands / 'id'
    identity.write_text('#!/bin/bash\necho 0\n')
    identity.chmod(0o700)
    # Any endpoint download is an error, even if someone accidentally restores it.
    for name in ('curl', 'wget'):
        command = commands / name
        command.write_text('#!/bin/bash\nexit 99\n')
        command.chmod(0o700)
    result = subprocess.run(
        ['bash', str(ROOT / 'app/services/rmm_builtin' / script_name)],
        env={**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH'],
             'MESH_INSTALLER_PATH': str(installer), 'MESH_GROUP_ID': 'company-group'},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == installer_exit, result.stdout + result.stderr
    assert marker.read_text() == 'ran'
    assert not installer.exists()
