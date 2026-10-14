"""Exercise app selection/commands on Windows without installing packages or rebooting."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL, reason='Requires Windows PowerShell')


@pytest.mark.parametrize('managed,installed,missing,expected', [
    ('1.0', '1.0', False, 'upgrade'), (None, '1.0', False, 'install'),
    ('3.0', '3.0', False, None), ('1.0', '3.0', False, None), (None, '3.0', False, None),
    (None, None, False, None), (None, None, True, 'install'),
])
def test_chocolatey_command_selection(tmp_path, managed, installed, missing, expected):
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
switch ($args[0]) {
    '--version' { '2.3.0' }
    'list' { if ($env:TEST_MANAGED) { "testapp|$env:TEST_MANAGED" } }
    'search' { 'testapp|2.0' }
    default { ($args -join ' ') | Add-Content $env:TEST_COMMAND_LOG }
}
''')
    harness = tmp_path / 'harness.ps1'
    harness.write_text('''
function Get-Command { [pscustomobject]@{ Source = $env:TEST_CHOCO } }
function Test-Path { $false }
function Get-ItemProperty {
    param($Path, $Name, $ErrorAction)
    if (-not $Name -and $env:TEST_INSTALLED) {
        [pscustomobject]@{ DisplayName = 'Test Application'; DisplayVersion = $env:TEST_INSTALLED }
    }
}
& $env:TEST_PATCH_SCRIPT
''')
    log = tmp_path / 'commands.txt'
    plan = {'windows': None, 'reboot': {'reboot_mode': 'never'},
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': missing}]}
    env = {**os.environ, 'TEST_MANAGED': managed or '', 'TEST_INSTALLED': installed or '',
           'TEST_CHOCO': str(fake), 'TEST_COMMAND_LOG': str(log),
           'TEST_PATCH_SCRIPT': str(ROOT / 'app/services/rmm_builtin/patch.ps1'), 'MYPORTAL_PATCH_PLAN': json.dumps(plan)}
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(harness)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = log.read_text() if log.exists() else ''
    if expected:
        assert commands.startswith(expected + ' testapp ')
        assert '--version=2.0' in commands and '--source=https://community.chocolatey.org/api/v2/' in commands
        assert ('--force' in commands) == (expected == 'install')
    else:
        assert commands == ''
