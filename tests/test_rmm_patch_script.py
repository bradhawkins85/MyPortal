"""Exercise app selection/commands on Windows without installing packages or rebooting."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='Requires PowerShell; Windows calls are mocked')


def _patch_script_text() -> str:
    """Load the patch runner and its feature helpers with an isolated mutex.

    The test harness supplies Chocolatey stubs. An unnamed mutex avoids sharing
    the production Windows mutex across tests and also works with Linux PowerShell.
    """
    builtin = ROOT / 'app/services/rmm_builtin'
    content = (builtin / 'patch.ps1').read_text(encoding='utf-8')
    mutex = r"New-Object Threading.Mutex($false, 'Global\MyPortalPatchManagement')"
    assert mutex in content
    return '\n'.join([
        (builtin / 'windows_feature_upgrade.ps1').read_text(encoding='utf-8'),
        content.replace(mutex, '[Threading.Mutex]::new($false)', 1),
    ])


def _patch_script_copy(tmp_path: Path) -> Path:
    script = tmp_path / 'patch.ps1'
    script.write_text(_patch_script_text(), encoding='utf-8')
    return script


@pytest.mark.parametrize('managed,installed,missing,expected', [
    ('1.0', '1.0', False, 'upgrade'), (None, '1.0', False, 'install'), (None, '2.0', False, 'install'),
    ('3.0', '3.0', False, None), ('1.0', '3.0', False, None), (None, '3.0', False, 'register'),
    (None, None, False, None), (None, None, True, 'install'),
])
@pytest.mark.parametrize('available_source', ['custom1', 'custom2', 'custom10', 'default'])
def test_chocolatey_command_selection(tmp_path, managed, installed, missing, expected, available_source):
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
switch ($args[0]) {
    '--version' { '2.3.0' }
    'list' { if ($env:TEST_MANAGED) { "testapp|$env:TEST_MANAGED" } }
    'search' {
        ($args -join ' ') | Add-Content $env:TEST_SEARCH_LOG
        if ($args -contains "--source=$env:TEST_AVAILABLE_SOURCE") { 'testapp|2.0' }
        elseif ($args -contains '--source=default') { 'testapp|9.0' }
    }
    default { ($args -join ' ') | Add-Content $env:TEST_COMMAND_LOG }
}
''')
    harness = tmp_path / 'harness.ps1'
    harness.write_text('''
function Sync-ChocolateySources { param($sources); $script:choco = $env:TEST_CHOCO }
function Get-Command { [pscustomobject]@{ Source = $env:TEST_CHOCO } }
function Test-Path { $false }
function Get-ItemProperty {
    param($Path, $Name, $ErrorAction)
    if (-not $Name -and $env:TEST_INSTALLED) {
        [pscustomobject]@{ DisplayName = 'Test Application'; DisplayVersion = $env:TEST_INSTALLED }
    }
}
. $env:TEST_PATCH_SCRIPT
''')
    log = tmp_path / 'commands.txt'
    plan = {'sources': [{'name': name, 'enabled': enabled, 'priority': priority} for name, enabled, priority in
                        [('default', True, 5), ('custom10', True, 3), ('custom2', True, 2),
                         ('custom1', True, 1), ('disabled', False, 4)]], 'windows': None, 'reboot': {'reboot_mode': 'never'},
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': missing}]}
    env = {**os.environ, 'TEST_MANAGED': managed or '', 'TEST_INSTALLED': installed or '',
           'TEST_CHOCO': str(fake), 'TEST_COMMAND_LOG': str(log),
           'TEST_SEARCH_LOG': str(tmp_path / 'searches.txt'), 'TEST_AVAILABLE_SOURCE': available_source,
           'TEST_PATCH_SCRIPT': str(_patch_script_copy(tmp_path)), 'MYPORTAL_PATCH_PLAN': json.dumps(plan)}
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(harness)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    searches = (tmp_path / 'searches.txt').read_text().splitlines()
    order = ['custom1', 'custom2', 'custom10', 'default']
    assert len(searches) == order.index(available_source) + 1
    assert all(f'--source={name}' in search for name, search in zip(order, searches))
    commands = log.read_text() if log.exists() else ''
    if expected:
        # Unmanaged apps are brought under Chocolatey; a newer one is only registered, never downgraded.
        assert commands.startswith(('install' if expected == 'register' else expected) + ' testapp ')
        assert '--version=2.0' in commands and '--source=' not in commands
        assert ('--force' in commands) == (expected in {'install', 'register'})
        assert ('--skip-automation-scripts' in commands) == (expected == 'register')
    else:
        assert commands == ''


@pytest.mark.parametrize('mode', ['install', 'upgrade', 'uninstall'])
@pytest.mark.parametrize('force', [False, True])
@pytest.mark.parametrize('ignore_reboots', [None, False, True])
def test_manual_package_options_and_pending_reboot(tmp_path, mode, force, ignore_reboots):
    """A pending reboot is ignored by default; unticking the option defers work."""
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
($args -join ' ') | Add-Content $env:TEST_COMMAND_LOG
switch ($args[0]) {
    '--version' { '2.3.0' }
    'list' { 'testapp|2.0' }
    'search' { 'testapp|2.0' }
    default { $global:LASTEXITCODE = [int]$env:TEST_EXIT_CODE }
}
''', encoding='utf-8')
    harness = tmp_path / 'harness.ps1'
    harness.write_text(r'''
function Sync-ChocolateySources { param($sources); $script:choco = $env:TEST_CHOCO }
function Test-Path { param($Path); $Path -like '*RebootPending' }
function Get-ItemProperty { param($Path, $Name, $ErrorAction) }
''' + _patch_script_text(), encoding='utf-8')
    # Ignore checksums follows force here to keep the matrix small.
    options = {'mode': mode, 'package_ids': ['testapp'], 'force': force, 'ignore_checksums': force}
    if ignore_reboots is not None:
        options['ignore_pending_reboots'] = ignore_reboots
    plan = {'manual_apps': options,
            'sources': [{'name': 'company', 'enabled': True, 'priority': 1}],
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': mode == 'install'}],
            'reboot': {'reboot_mode': 'never'}}
    log = tmp_path / 'commands.txt'
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
        env={**os.environ, 'TEST_CHOCO': str(fake), 'TEST_COMMAND_LOG': str(log), 'TEST_EXIT_CODE': '3010',
             'TEST_PATCH_SCRIPT': str(_patch_script_copy(tmp_path)), 'MYPORTAL_PATCH_PLAN': json.dumps(plan)},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Reboot pending: True' in result.stdout
    if ignore_reboots is False:
        assert not log.exists()
        assert 'Installation deferred' in result.stdout
    else:
        commands = log.read_text(encoding='utf-8-sig').splitlines()
        action = commands[-1].split()
        assert action[:2] == [mode, 'testapp']
        assert ('--force' in action) == force
        assert ('--ignore-checksums' in action) == (force and mode != 'uninstall')
        assert '--ignore-detected-reboot' in action
        assert ('--fail-on-not-installed' in action) == (mode == 'upgrade')
        assert ('--version=2.0' in action) == (mode != 'uninstall')
        assert any(command.startswith('search ') for command in commands) == (mode != 'uninstall')


@pytest.mark.parametrize('mode', ['install', 'upgrade', 'uninstall'])
def test_manual_package_without_reboot_does_not_ignore_when_unticked(tmp_path, mode):
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
switch ($args[0]) {
    '--version' { '2.3.0' }
    'search' { 'testapp|2.0' }
    'list' { }
    default {
        ($args -join ' ') | Set-Content $env:TEST_COMMAND_LOG
        $global:LASTEXITCODE = 1
    }
}
''', encoding='utf-8')
    harness = tmp_path / 'harness.ps1'
    harness.write_text('''
function Sync-ChocolateySources { param($sources); $script:choco = $env:TEST_CHOCO }
function Test-Path { $false }
function Get-ItemProperty { param($Path, $Name, $ErrorAction) }
''' + _patch_script_text(), encoding='utf-8')
    log = tmp_path / 'commands.txt'
    plan = {'manual_apps': {'mode': mode, 'force': False, 'ignore_pending_reboots': False},
            'sources': [{'name': 'company', 'enabled': True, 'priority': 1}],
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': True}],
            'reboot': {'reboot_mode': 'never'}}
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(harness)],
        env={**os.environ, 'TEST_CHOCO': str(fake), 'TEST_COMMAND_LOG': str(log),
             'TEST_PATCH_SCRIPT': str(_patch_script_copy(tmp_path)), 'MYPORTAL_PATCH_PLAN': json.dumps(plan)},
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    command = log.read_text(encoding='utf-8-sig').split()
    assert command[:2] == [mode, 'testapp']
    assert '--ignore-detected-reboot' not in command and '--force' not in command
    assert '--ignore-checksums' not in command
    assert 'Chocolatey returned 1' in result.stdout


@pytest.mark.parametrize('manual', [False, True])
def test_asset_upgrade_ignores_pending_reboot(tmp_path, manual):
    """The asset page's one-click upgrade runs despite a pending reboot; scheduled patching still defers."""
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
switch ($args[0]) {
    '--version' { '2.3.0' }
    'list' { 'testapp|1.0' }
    'search' { 'testapp|2.0' }
    default { ($args -join ' ') | Add-Content $env:TEST_COMMAND_LOG }
}
''', encoding='utf-8')
    harness = tmp_path / 'harness.ps1'
    harness.write_text(r'''
function Sync-ChocolateySources { param($sources); $script:choco = $env:TEST_CHOCO }
function Test-Path { param($Path); $Path -like '*RebootPending' }
function Get-ItemProperty { param($Path, $Name, $ErrorAction) }
''' + _patch_script_text(), encoding='utf-8')
    plan = {'sources': [{'name': 'company', 'enabled': True, 'priority': 1}],
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': False}],
            'windows': None, 'reboot': {'reboot_mode': 'never'}}
    if manual:
        plan['manual_upgrade'] = {'name': 'Test Application', 'package_id': 'testapp'}
    log = tmp_path / 'commands.txt'
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
        env={**os.environ, 'TEST_CHOCO': str(fake), 'TEST_COMMAND_LOG': str(log), 'MYPORTAL_PATCH_PLAN': json.dumps(plan)},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    if manual:
        command = log.read_text(encoding='utf-8-sig').split()
        assert command[:2] == ['upgrade', 'testapp'] and '--ignore-detected-reboot' in command
        assert 'Proceeding despite a pending restart' in result.stdout
    else:
        assert not log.exists() and 'Installation deferred' in result.stdout


@pytest.mark.parametrize('pending', [False, True])
def test_app_policy_ignore_options_pass_the_reboot_gate_and_skip_checksums(tmp_path, pending):
    """Only app policies set to ignore pending reboots run while a restart is pending."""
    fake = tmp_path / 'choco.ps1'
    fake.write_text('''
$global:LASTEXITCODE = 0
switch ($args[0]) {
    '--version' { '2.3.0' }
    'list' { }
    'search' { if ($args[1] -eq 'skipapp') { 'skipapp|2.0' } else { 'testapp|2.0' } }
    default { ($args -join ' ') | Add-Content $env:TEST_COMMAND_LOG }
}
''', encoding='utf-8')
    harness = tmp_path / 'harness.ps1'
    harness.write_text(r'''
function Sync-ChocolateySources { param($sources); $script:choco = $env:TEST_CHOCO }
function Test-Path { param($Path); ($env:TEST_PENDING -eq '1') -and ($Path -like '*RebootPending') }
function Get-ItemProperty { param($Path, $Name, $ErrorAction) }
''' + _patch_script_text(), encoding='utf-8')
    plan = {'sources': [{'name': 'company', 'enabled': True, 'priority': 1}], 'windows': None,
            'reboot': {'reboot_mode': 'never'},
            'apps': [{'package_id': 'testapp', 'display_name': 'Test Application', 'install_missing': True,
                      'ignore_pending_reboots': True, 'ignore_checksums': True},
                     {'package_id': 'skipapp', 'display_name': 'Skip Application', 'install_missing': True}]}
    log = tmp_path / 'commands.txt'
    result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(harness)],
        env={**os.environ, 'TEST_CHOCO': str(fake), 'TEST_COMMAND_LOG': str(log),
             'TEST_PENDING': '1' if pending else '0', 'MYPORTAL_PATCH_PLAN': json.dumps(plan)},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = log.read_text(encoding='utf-8-sig').splitlines()
    test_app = next(line.split() for line in commands if line.startswith('install testapp '))
    assert '--ignore-checksums' in test_app
    skipped = [line for line in commands if line.startswith('install skipapp ')]
    if pending:
        assert skipped == []
        assert 'skipapp deferred: a restart is already pending.' in result.stdout
    else:
        assert len(skipped) == 1 and '--ignore-checksums' not in skipped[0].split()
