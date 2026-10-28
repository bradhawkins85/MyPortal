"""Run the Windows activation with a delivered installer and mocked service and process APIs."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not POWERSHELL, reason='Requires PowerShell; Windows calls are mocked')
@pytest.mark.parametrize('scenario,expected_error', [
    ('running', ''), ('stopped', ''), ('missing-service', 'did not finish installing'),
    ('installer-failed', 'exit code 9'), ('installer-error', 'cannot create service'),
    ('timeout', 'timed out'),
])
def test_meshcentral_installs_service_with_fullinstall(tmp_path, scenario, expected_error):
    harness = tmp_path / 'mesh-harness.ps1'
    harness.write_text(r'''
$ErrorActionPreference = 'Stop'
$env:TEMP = $env:TEST_TEMP
$env:MESH_INSTALLER_PATH = Join-Path $env:TEMP 'mesh-installer.exe'
Set-Content -LiteralPath $env:MESH_INSTALLER_PATH -Value 'installer'
$env:MESH_GROUP_ID = 'group-id'
$env:PROCESSOR_ARCHITECTURE = 'AMD64'
$env:ProgramFiles = 'C:\Program Files'
$script:launches = 0
$script:nodeIdReads = 0
# The service's own path wins over the default install location.
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    return [pscustomobject]@{PathName = '"C:\Mesh\MeshAgent.exe" --run'}
}
function Invoke-WebRequest { throw 'The endpoint must not download the installer.' }
$script:clock = [datetime]'2026-10-27T12:00:00'
function Get-Date { $script:clock = $script:clock.AddSeconds(15); return $script:clock }
function Start-Sleep { param($Seconds) }
$script:serviceStatus = if ($env:TEST_CASE -eq 'stopped') { 'Stopped' } else { 'Running' }
$script:started = $false
function Get-Service {
    param($Name, $ErrorAction)
    if ($Name -ne 'Mesh Agent') { throw 'Unexpected service name.' }
    if ($env:TEST_CASE -eq 'missing-service') { return $null }
    $service = [pscustomobject]@{Status = $script:serviceStatus}
    $service | Add-Member -MemberType ScriptMethod -Name Start -Value {
        $script:started = $true
        $script:serviceStatus = 'Running'
    }
    $service | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value {
        param($Expected, $Timeout)
        if ($Expected -ne 'Running' -or $script:serviceStatus -ne 'Running') { throw 'Service failed to start.' }
    }
    return $service
}
$script:stopped = $false
function Stop-Process { param($Id, [switch]$Force, $ErrorAction); $script:stopped = $true }
$script:automatic = $false
function Set-Service {
    param($Name, $StartupType)
    if ($Name -ne 'Mesh Agent' -or $StartupType -ne 'Automatic') { throw 'Expected automatic Mesh Agent service.' }
    $script:automatic = $true
}
function Start-Process {
    param($FilePath, [ValidateNotNullOrEmpty()][string[]]$ArgumentList,
          [switch]$PassThru, $WindowStyle, $RedirectStandardOutput, $RedirectStandardError)
    if ($ArgumentList.Count -eq 1 -and $ArgumentList[0] -eq '"-nodeid"') {
        if ($FilePath -ne 'C:\Mesh\MeshAgent.exe') { throw "Read the node id from the wrong agent: $FilePath" }
        # The first read comes before the agent has made its identity.
        $script:nodeIdReads++
        $id = if ($script:nodeIdReads -eq 1) { 'Node ID not defined' } else { 'abc$def@' * 8 }
        Set-Content -LiteralPath $RedirectStandardOutput -Value $id
        Set-Content -LiteralPath $RedirectStandardError -Value ''
        $process = [pscustomobject]@{Handle = 1; ExitCode = 0; Id = 124}
        $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Timeout); return $true }
        return $process
    }
    $script:launches++
    if ($script:launches -eq 1 -and ($ArgumentList.Count -ne 1 -or $ArgumentList[0] -ne '"-fullinstall"')) {
        throw 'The agent must install the service with -fullinstall.'
    }
    if ($script:launches -eq 2 -and ($ArgumentList.Count -ne 1 -or $ArgumentList[0] -ne '"value with spaces"')) {
        throw 'Nonempty arguments must retain quoting.'
    }
    if (-not $PassThru -or $WindowStyle -ne 'Hidden') { throw 'Missing process options.' }
    $message = if ($env:TEST_CASE -eq 'installer-error') { '[ERROR] cannot create service' } else { 'installer output' }
    Set-Content -LiteralPath $RedirectStandardOutput -Value $message
    Set-Content -LiteralPath $RedirectStandardError -Value ''
    $exitCode = if ($env:TEST_CASE -eq 'installer-failed') { 9 } else { 0 }
    $process = [pscustomobject]@{Handle = 1; ExitCode = $exitCode; Id = 123}
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Timeout); return ($env:TEST_CASE -ne 'timeout') }
    return $process
}
$caught = $false
try { . $env:TEST_MESH_SCRIPT } catch {
    if (-not $env:TEST_EXPECTED -or $_.Exception.Message -notmatch [regex]::Escape($env:TEST_EXPECTED)) { throw }
    $caught = $true
}
if ($env:TEST_EXPECTED) {
    if (-not $caught) { throw 'Expected activation failure.' }
    if ($env:TEST_CASE -eq 'timeout' -and -not $script:stopped) { throw 'Hung installer was not terminated.' }
} else {
    $output = Invoke-MeshCommand -File 'test.exe' -Arguments @('value with spaces') -Step 'test'
    if ($output.Trim() -ne 'installer output') { throw 'Missing command output.' }
    if ($script:launches -ne 2) { throw 'Expected installer and argument-bearing command.' }
    if (-not $script:automatic) { throw 'The service was not set to automatic startup.' }
    if ($env:TEST_CASE -eq 'stopped' -and -not $script:started) { throw 'The stopped service was not started.' }
    if ($script:nodeIdReads -ne 2) { throw 'Expected the node id to be read until it was ready.' }
}
if (Get-ChildItem -LiteralPath $env:TEMP) { throw 'Temporary files were not cleaned up.' }
''')
    temp = tmp_path / 'temp'
    temp.mkdir()
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
        env={**os.environ, 'XDG_CACHE_HOME': str(tmp_path / 'cache'),
             'XDG_CONFIG_HOME': str(tmp_path / 'config'), 'XDG_DATA_HOME': str(tmp_path / 'data'),
             'TEST_TEMP': str(temp), 'TEST_CASE': scenario, 'TEST_EXPECTED': expected_error,
             'TEST_MESH_SCRIPT': str(ROOT / 'app/services/rmm_builtin/meshcentral_activate.ps1')},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if not expected_error:
        assert '##myportal[session.node_id]=' + 'abc$def@' * 8 in result.stdout


@pytest.mark.parametrize('script_name', ['meshcentral_activate.sh', 'meshcentral_activate_macos.sh'])
@pytest.mark.parametrize('installer_exit,service_running,printed_error', [
    (0, True, False), (9, False, False), (0, False, False), (0, True, True),
])
def test_unix_activation_uses_delivered_installer_and_cleans_up(tmp_path, script_name, installer_exit, service_running, printed_error):
    installer = tmp_path / 'delivered installer.sh'
    marker = tmp_path / 'ran'
    installer.write_text(
        '#!/bin/bash\n'
        '[[ "$#" == 2 && "$1" == -fullinstall && "$2" == --copy-msh=1 ]] || exit 98\n'
        '[ -s "$0.msh" ] || exit 97\n'
        f'printf ran > "{marker}"\n'
        + ('echo "[ERROR] cannot create service"\n' if printed_error else '')
        + f'exit {installer_exit}\n'
    )
    configuration = Path(str(installer) + '.msh')
    configuration.write_text('MeshID=company-group\nMeshServer=wss://mesh.test/agent.ashx\nServerID=server\n')
    commands = tmp_path / 'bin'
    commands.mkdir()
    for name in ('systemctl', 'pgrep', 'launchctl'):
        command = commands / name
        if name == 'launchctl':
            command.write_text('#!/bin/bash\necho "state = ' + ('running' if service_running else 'waiting') + '"\n')
        elif name == 'systemctl':
            command.write_text('#!/bin/bash\nexit ' + ('0' if service_running else '1') + '\n')
        else:
            command.write_text('#!/bin/bash\nexit 0\n')
        command.chmod(0o700)
    sleep = commands / 'sleep'
    sleep.write_text('#!/bin/bash\nexit 0\n')
    sleep.chmod(0o700)
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
    expected_exit = installer_exit or (0 if service_running and not printed_error else 1)
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert marker.read_text() == 'ran'
    assert not installer.exists()
    assert not configuration.exists()


@pytest.mark.parametrize('script_name', ['meshcentral_activate.sh', 'meshcentral_activate_macos.sh'])
@pytest.mark.parametrize('agent_output,reported', [
    ('abc$def@' * 8, True), ('Node ID not defined', False), ('', False),
])
def test_unix_activation_reports_the_agents_node_id(tmp_path, script_name, agent_output, reported):
    installer = tmp_path / 'installer'
    installer.write_text('#!/bin/bash\nexit 0\n')
    Path(str(installer) + '.msh').write_text('MeshID=company-group\n')
    # The installed agent, found through the service as Tactical RMM's agent finds it.
    agent = tmp_path / 'mesh services' / 'meshagent'
    agent.parent.mkdir()
    agent.write_text('#!/bin/bash\n[[ "$1" == -nodeid ]] || exit 98\n' + f"printf '%s\\r\\n' '{agent_output}'\n")
    agent.chmod(0o700)
    commands = tmp_path / 'bin'
    commands.mkdir()
    stubs = {
        'systemctl': f'[[ "$1" == show ]] && echo "ExecStart={{ path={agent} ; argv[]={agent} ; }}"\nexit 0',
        'launchctl': f'printf "state = running\\n        program = %s\\n" "{agent}"',
        'sleep': 'exit 0',
        'id': 'echo 0',
    }
    for name, body in stubs.items():
        command = commands / name
        command.write_text('#!/bin/bash\n' + body + '\n')
        command.chmod(0o700)
    result = subprocess.run(
        ['bash', str(ROOT / 'app/services/rmm_builtin' / script_name)],
        env={**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH'],
             'MESH_INSTALLER_PATH': str(installer), 'MESH_GROUP_ID': 'company-group'},
        capture_output=True, text=True, timeout=10,
    )
    # Without an id the activation still succeeds; MyPortal matches the host name.
    assert result.returncode == 0, result.stdout + result.stderr
    if reported:
        assert ('##myportal[session.node_id]=' + agent_output + '\n') in result.stdout
    else:
        assert '##myportal[session.node_id]' not in result.stdout
        assert 'find the device by its host name' in result.stdout
