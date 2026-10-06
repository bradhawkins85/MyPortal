package agent

// logQueryScript emits one bounded line per event so the prompt stays
// greppable. It is read-only (Get-WinEvent never mutates the logs).
//
// Get-WinEvent has no -Since parameter, so the window goes through
// -FilterHashtable's StartTime. A window with no events raises
// NoMatchingEventsFound, which is an empty result rather than a failure.
// Progress output is silenced so first-run module loading does not end up on
// stderr as CLIXML.
const logQueryScript = `$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
  Get-WinEvent -FilterHashtable @{ LogName = '%s'; StartTime = [datetime] '%s' } -MaxEvents %d |
    ForEach-Object {
      $msg = if ($null -ne $_.Message) { ($_.Message -replace "\r?\n", ' ') } else { '' }
      '{0:yyyy-MM-dd HH:mm:ss} [{1}] id={2}: {3}' -f $_.TimeCreated, $_.LevelDisplayName, $_.Id, $msg.Substring(0, [Math]::Min(400, $msg.Length))
    }
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { return }
  throw
}`
