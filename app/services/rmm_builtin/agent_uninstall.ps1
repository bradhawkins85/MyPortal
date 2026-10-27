# Removes the MyPortal Tray Agent and the MyPortal RMM agent from this computer.
#
# The RMM agent runs this script, so it cannot remove itself while the script is
# still running: the script writes the removal steps to a file in the Windows
# temp folder and starts them as a one-off SYSTEM scheduled task, outside the RMM
# agent's process tree, then exits. The task waits for the RMM agent to report
# this run to MyPortal, removes the tray (its MSI, service, files, settings and
# enrolment) and then the RMM agent (service, files and enrolment), and deletes
# itself. Its log is kept at %WINDIR%\Temp\myportal-uninstall.log.
$taskName = 'MyPortal Agent Uninstall'
$temp = Join-Path $env:WINDIR 'Temp'
$cleanupPath = Join-Path $temp 'myportal-uninstall.ps1'
$logPath = Join-Path $temp 'myportal-uninstall.log'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'The uninstall must run as System. Queue it again without "Run as logged in user".'
    exit 1
}

$cleanup = @'
$ErrorActionPreference = 'Continue'
$taskName = 'MyPortal Agent Uninstall'
$programData = if ($env:ProgramData) { $env:ProgramData } else { 'C:\ProgramData' }
$programFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } elseif ($env:ProgramFiles) { $env:ProgramFiles } else { 'C:\Program Files' }
$logPath = Join-Path (Join-Path $env:WINDIR 'Temp') 'myportal-uninstall.log'

function Log([string]$message) {
    $line = '{0:u} {1}' -f (Get-Date).ToUniversalTime(), $message
    Add-Content -LiteralPath $logPath -Value $line -ErrorAction SilentlyContinue
}

function Remove-Service([string]$name) {
    if (-not (Get-Service -Name $name -ErrorAction SilentlyContinue)) { return }
    # Disabled first, so its restart-on-failure actions cannot bring it back.
    & sc.exe config $name start= disabled | Out-Null
    & sc.exe stop $name | Out-Null
    Start-Sleep -Seconds 5
    $service = Get-CimInstance Win32_Service -Filter "Name='$name'" -ErrorAction SilentlyContinue
    if ($service -and $service.ProcessId) {
        Stop-Process -Id $service.ProcessId -Force -ErrorAction SilentlyContinue
    }
    & sc.exe delete $name | Out-Null
    Log "Removed the $name service"
}

function Remove-SafeBoot([string]$name) {
    foreach ($mode in @('Minimal', 'Network')) {
        Remove-Item -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\SafeBoot\$mode\$name" -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Remove-Folder([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $path)) { Log "Deleted $path"; return }
        Start-Sleep -Seconds 3
    }
    Log "Could not delete $path"
}

Log 'Waiting for the RMM agent to report the uninstall run'
Start-Sleep -Seconds 20

# 1. The tray, first, so it cannot reinstall the RMM agent.
Get-Process -Name 'myportal-tray-ui', 'myportal-tray-chat' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
$uninstallKeys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$entries = Get-ItemProperty -Path $uninstallKeys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'MyPortal Tray*' }
foreach ($entry in $entries) {
    if ($entry.PSChildName -match '^\{[0-9A-Fa-f-]{36}\}$') {
        Log "Uninstalling $($entry.DisplayName) $($entry.DisplayVersion) ($($entry.PSChildName))"
        $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/x', $entry.PSChildName, '/qn', '/norestart') -Wait -PassThru -WindowStyle Hidden
        Log "msiexec exited with $($process.ExitCode)"
    } elseif ($entry.QuietUninstallString) {
        # The setup bundle; its MSI is removed above.
        Log "Removing the $($entry.DisplayName) setup bundle"
        Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $entry.QuietUninstallString) -Wait -WindowStyle Hidden
    }
}
Remove-Service 'MyPortalTrayService'
Remove-SafeBoot 'MyPortalTrayService'
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name 'MyPortalTrayUI' -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKLM:\SOFTWARE\MyPortal\Tray' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Folder (Join-Path $programFiles 'MyPortalTray')
Remove-Folder (Join-Path $programData 'MyPortal\tray')

# 2. The RMM agent.
$rmmDir = Join-Path $programFiles 'MyPortal RMM'
$rmm = Join-Path $rmmDir 'myportal-rmm.exe'
if (Test-Path -LiteralPath $rmm) {
    $output = & $rmm uninstall 2>&1 | Out-String
    Log "myportal-rmm uninstall: $($output.Trim())"
}
Remove-Service 'MyPortalRMMAgent'
Remove-SafeBoot 'MyPortalRMMAgent'
Get-Process -Name 'myportal-rmm' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Folder $rmmDir
Remove-Folder (Join-Path $programData 'MyPortal RMM')

# Empty parents left behind.
foreach ($path in @((Join-Path $programData 'MyPortal'), 'HKLM:\SOFTWARE\MyPortal')) {
    if ((Test-Path -LiteralPath $path) -and -not (Get-ChildItem -LiteralPath $path -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
}

Log 'The MyPortal Tray Agent and RMM agent have been removed'
& schtasks.exe /Delete /TN $taskName /F | Out-Null
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
'@

try {
    Set-Content -LiteralPath $cleanupPath -Value $cleanup -Encoding UTF8 -ErrorAction Stop
} catch {
    Write-Error "Could not write ${cleanupPath}: $($_.Exception.Message)"
    exit 1
}

# No quotes: Windows PowerShell does not escape them for native commands, and the
# Windows temp folder has no spaces.
$command = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File ' + $cleanupPath
$created = & schtasks.exe /Create /TN $taskName /TR $command /SC ONCE /ST 00:00 /RU SYSTEM /RL HIGHEST /F 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Could not create the uninstall task: $created"
    exit 1
}
$started = & schtasks.exe /Run /TN $taskName 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Could not start the uninstall task: $started"
    & schtasks.exe /Delete /TN $taskName /F | Out-Null
    exit 1
}

Write-Output "Started the '$taskName' task. In about 30 seconds it removes the MyPortal Tray Agent and then the MyPortal RMM agent."
Write-Output "Its log is kept at $logPath."
exit 0
