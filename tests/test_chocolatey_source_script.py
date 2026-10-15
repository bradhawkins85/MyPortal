"""Exercise actual source commands with a fake Chocolatey, never real repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='Requires PowerShell')


@pytest.mark.parametrize('fail', [False, True])
def test_sources_disable_unmanaged_rotate_credentials_and_fail_closed(tmp_path, fail):
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
if ($env:TEST_FAIL -eq 'true' -and $args[1] -eq 'add') {
    Write-Output 'diagnostic with secret-value'; $global:LASTEXITCODE = 1; return
}
$state = @(Get-Content -Raw $env:TEST_STATE | ConvertFrom-Json)
$name = ''; $url = ''; $user = ''; $password = ''
foreach ($arg in $args) {
    if ($arg -like '--name=*') { $name = $arg.Substring(7) }
    if ($arg -like '--source=*') { $url = $arg.Substring(9) }
    if ($arg -like '--user=*') { $user = $arg.Substring(7) }
    if ($arg -like '--password=*') { $password = $arg.Substring(11) }
}
switch ($args[1]) {
    'list' { foreach ($s in $state) { "$($s.name)|$($s.url)|$($s.disabled.ToString().ToLower())" }; return }
    'disable' { foreach ($s in $state) { if ($s.name -eq $name) { $s.disabled = $true } } }
    'enable' { foreach ($s in $state) { if ($s.name -eq $name) { $s.disabled = $false } } }
    'remove' { $state = @($state | Where-Object { $_.name -ne $name }) }
    'add' { $state += [pscustomobject]@{ name=$name; url=$url; disabled=$false; username=$user; password=$password } }
}
ConvertTo-Json -InputObject @($state) | Set-Content $env:TEST_STATE
''', encoding='utf-8')
    state = tmp_path / 'sources.json'
    state.write_text(json.dumps([
        {'name': 'unmanaged', 'url': 'https://unmanaged.example.com', 'disabled': False},
        {'name': 'nexus', 'url': 'https://old.example.com', 'disabled': False, 'username': 'old', 'password': 'old'}]))
    harness = tmp_path / 'harness.ps1'
    harness.write_text('''
$ErrorActionPreference = 'Stop'
function Get-Command { [pscustomobject]@{ Source = $env:TEST_CHOCO } }
. $env:TEST_SOURCE_SCRIPT
try { Sync-ChocolateySources @($env:TEST_SOURCES | ConvertFrom-Json) }
catch { Write-Output $_; exit 1 }
''', encoding='utf-8')
    desired = [{'name': 'nexus', 'url': 'https://new.example.com', 'enabled': True,
                'username': 'new-user', 'password': 'secret-value'}]
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
        env={**os.environ, 'TEST_CHOCO': str(fake), 'TEST_STATE': str(state), 'TEST_SOURCES': json.dumps(desired),
             'TEST_SOURCE_SCRIPT': str(ROOT / 'app/services/rmm_builtin/chocolatey_sources.ps1'),
             'TEST_FAIL': str(fail).lower()}, capture_output=True, text=True, timeout=30)
    assert 'secret-value' not in result.stdout + result.stderr
    actual = json.loads(state.read_text(encoding='utf-8-sig'))
    assert actual[0]['name'] == 'unmanaged' and actual[0]['disabled'] is True
    if fail:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert actual[1]['url'] == 'https://new.example.com'
        assert actual[1]['username'] == 'new-user' and actual[1]['password'] == 'secret-value'
        assert actual[1]['disabled'] is False
