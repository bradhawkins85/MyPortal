<#
.SYNOPSIS
  Removes the PawnIO driver that RMM agents released before the PawnIO fix installed.
.DESCRIPTION
  PawnIO caused SYSTEM_THREAD_EXCEPTION_NOT_HANDLED (0x7E) crashes. This runs the
  uninstall command PawnIO registered with Windows, silently, and confirms PawnIO
  is no longer registered. It also removes PawnIO that other software installed,
  such as FanControl.

  An RMM agent released before the fix reinstalls PawnIO when its service next
  starts, for example after a reboot. Update the agent for a permanent fix; agents
  with the fix remove PawnIO themselves at startup.

  Exit codes: 0 PawnIO removed or not installed, 1 removal failed.
#>

$ErrorActionPreference = 'Stop'
$keyPath = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO'

# PawnIO can register in either registry view.
function Get-PawnIOUninstallCommand {
    foreach ($view in [Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32) {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
        try {
            $key = $base.OpenSubKey($keyPath)
            if ($null -eq $key) { continue }
            try {
                foreach ($name in 'QuietUninstallString', 'UninstallString') {
                    $command = [string]$key.GetValue($name)
                    if ($command.Trim()) {
                        return [pscustomobject]@{ Command = $command.Trim(); Version = [string]$key.GetValue('DisplayVersion') }
                    }
                }
                throw 'PawnIO is registered without an uninstall command.'
            } finally { $key.Close() }
        } finally { $base.Close() }
    }
    return $null
}

# Splits a registered command line into the program and its arguments, adding
# PawnIO's -uninstall and -silent switches when they are missing.
function Split-UninstallCommand([string] $Command) {
    if ($Command.StartsWith('"')) {
        $end = $Command.IndexOf('"', 1)
        if ($end -lt 0) { throw "Unterminated quote in uninstall command: $Command" }
        $program = $Command.Substring(1, $end - 1)
        $arguments = $Command.Substring($end + 1)
    } else {
        $end = $Command.ToLowerInvariant().IndexOf('.exe')
        if ($end -lt 0) { throw "Unexpected uninstall command: $Command" }
        $program = $Command.Substring(0, $end + 4)
        $arguments = $Command.Substring($end + 4)
    }
    if (-not $program.ToLowerInvariant().EndsWith('.exe')) { throw "Unexpected uninstall command: $Command" }
    $arguments = $arguments.Trim()
    foreach ($flag in '-uninstall', '-silent') {
        if ($arguments.ToLowerInvariant() -notlike "*$flag*") { $arguments = "$arguments $flag".Trim() }
    }
    [pscustomobject]@{ Program = $program; Arguments = $arguments }
}

try {
    $installed = Get-PawnIOUninstallCommand
    if ($null -eq $installed) {
        Write-Output 'PawnIO is not installed.'
        exit 0
    }
    Write-Output "Found PawnIO $($installed.Version)."
    $uninstall = Split-UninstallCommand $installed.Command
    if (-not (Test-Path -LiteralPath $uninstall.Program -PathType Leaf)) {
        throw "PawnIO uninstaller not found: $($uninstall.Program)"
    }
    Write-Output "Running: `"$($uninstall.Program)`" $($uninstall.Arguments)"
    $process = Start-Process -FilePath $uninstall.Program -ArgumentList $uninstall.Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(120000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw 'The PawnIO uninstaller did not finish within two minutes.'
    }
    Write-Output "Uninstaller exit code: $($process.ExitCode)"
    if ($null -ne (Get-PawnIOUninstallCommand)) {
        throw 'PawnIO is still registered after running its uninstaller.'
    }
    Write-Output 'PawnIO removed.'
    exit 0
} catch {
    Write-Output "Removing PawnIO failed: $($_.Exception.Message)"
    exit 1
}
