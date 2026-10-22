"""Run the real app script with a fake installer/Chocolatey; never install software."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='Requires PowerShell')


@pytest.mark.parametrize('mode', ['existing', 'fresh', 'installer-error', 'not-installed', 'source-error'])
def test_bootstrap_sync_then_install(tmp_path, mode):
    events = tmp_path / 'events.txt'
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
($args -join ' ') | Add-Content $env:TEST_EVENTS
switch ($args[0]) {
    'source' {
        if ($env:TEST_MODE -eq 'source-error') { $global:LASTEXITCODE = 1; return }
        switch ($args[1]) {
            'list' {
                if (Test-Path $env:TEST_SYNCED) { 'company|https://repo.example.com|False|||1|False|False|False' }
                else { 'chocolatey|https://community.chocolatey.org/api/v2/|False|||0|False|False|False' }
            }
            'add' { New-Item $env:TEST_SYNCED -ItemType File | Out-Null }
        }
    }
    '--version' { '2.3.0' }
    'search' { 'testapp|2.0' }
}
''', encoding='utf-8')
    harness = r'''
function Get-Command {
    if ($env:TEST_MODE -eq 'existing' -or $script:installed) {
        [pscustomobject]@{ Source = $env:TEST_CHOCO }
    }
}
function powershell.exe {
    'bootstrap' | Add-Content $env:TEST_EVENTS
    if ($args -notcontains '-NonInteractive' -or $args -notcontains 'Bypass' -or
        $args[-1] -notlike '*https://community.chocolatey.org/install.ps1*' -or
        $args[-1] -notlike '*Tls12*') { throw 'Invalid bootstrap command.' }
    $global:LASTEXITCODE = 0
    if ($env:TEST_MODE -eq 'installer-error') {
        'installer diagnostic'; $global:LASTEXITCODE = 1
    } elseif ($env:TEST_MODE -ne 'not-installed') { $script:installed = $true }
}
function Get-ItemProperty { param($Path, $Name, $ErrorAction) }
function Test-Path {
    param($Path, $LiteralPath, $PathType)
    if ($Path -like 'HKLM:*') { return $false }
    if ($LiteralPath) { return $false }
    Microsoft.PowerShell.Management\Test-Path $Path
}
'''
    builtin = ROOT / 'app/services/rmm_builtin'
    script = tmp_path / 'job.ps1'
    script.write_text(harness + '\n' + (builtin / 'chocolatey_sources.ps1').read_text()
                      + '\n' + (builtin / 'patch.ps1').read_text(), encoding='utf-8')
    plan = {'sources': [{'name': 'company', 'url': 'https://repo.example.com', 'priority': 1, 'enabled': True}],
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': True}],
            'reboot': {'reboot_mode': 'never'}}
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                            env={**os.environ, 'TEST_MODE': mode, 'TEST_CHOCO': str(fake),
                                 'TEST_EVENTS': str(events), 'TEST_SYNCED': str(tmp_path / 'synced'),
                                 'MYPORTAL_PATCH_PLAN': json.dumps(plan)},
                            capture_output=True, text=True, timeout=30)
    calls = events.read_text(encoding='utf-8-sig').splitlines()
    if mode in ('existing', 'fresh'):
        assert result.returncode == 0, result.stdout + result.stderr
        assert ('bootstrap' in calls) == (mode == 'fresh')
        disable = next(i for i, call in enumerate(calls) if call.startswith('source disable --name=chocolatey'))
        add = next(i for i, call in enumerate(calls) if call.startswith('source add --name=company'))
        install = next(i for i, call in enumerate(calls) if call.startswith('install testapp '))
        assert disable < add < install
        assert calls[add + 1].startswith('source enable --name=company')
        assert calls[add + 2] == 'source list --limit-output'
        if mode == 'fresh':
            assert calls[0] == 'bootstrap'
    else:
        assert result.returncode != 0
        assert not any(call.startswith(('install ', 'search ')) for call in calls)
        if mode != 'source-error':
            assert calls == ['bootstrap']
            assert 'Chocolatey installation failed' in result.stdout + result.stderr


@pytest.mark.parametrize('location', ['default', 'custom'])
def test_existing_installation_with_stale_path_does_not_bootstrap(tmp_path, location):
    root = tmp_path / ('chocolatey' if location == 'default' else 'custom chocolatey')
    (root / 'bin').mkdir(parents=True)
    executable = root / 'bin/choco.exe'
    executable.touch()
    harness = tmp_path / 'resolve.ps1'
    harness.write_text('''
$ErrorActionPreference = 'Stop'
function Get-Command { }
function powershell.exe { throw 'Should not bootstrap an existing installation.' }
. $env:TEST_SOURCE_SCRIPT
Ensure-ChocolateyInstalled
if ($script:choco -ne $env:TEST_EXPECTED) { throw "Wrong executable: $script:choco" }
if (-not $env:Path.StartsWith((Split-Path $script:choco -Parent))) { throw 'Shim directory missing from PATH.' }
''', encoding='utf-8')
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(harness)],
                            env={**os.environ, 'ProgramData': str(tmp_path),
                                 'ChocolateyInstall': str(root) if location == 'custom' else '',
                                 'TEST_EXPECTED': str(executable),
                                 'TEST_SOURCE_SCRIPT': str(ROOT / 'app/services/rmm_builtin/chocolatey_sources.ps1')},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
