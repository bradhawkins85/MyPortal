# MyPortal immediate Tactical RMM / Tray Agent linking script.
# Configure AgentID with Tactical RMM's agent ID runtime variable and store the
# API key as a protected script variable. The key only needs POST permission on
# /api/tray/trmm-sync.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PortalURL,
    [Parameter(Mandatory)][string]$APIKey,
    [Parameter(Mandatory)][string]$AgentID,
    [int]$WaitSeconds = 90
)

$ErrorActionPreference = 'Stop'
$statePath = Join-Path $env:ProgramData 'MyPortal\tray\tray-state.json'
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    if (Test-Path $statePath) {
        try {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            $trayAgentID = [string]$state.device_uid
            if ($trayAgentID) { break }
        } catch {
            # The service may be replacing the state file while we read it.
        }
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if (-not $trayAgentID) {
    throw "Tray Agent did not enrol within $WaitSeconds seconds ($statePath not ready)."
}

$body = @{ agent_id = $AgentID; tray_agent_id = $trayAgentID } | ConvertTo-Json
try {
    $result = Invoke-RestMethod -Method Post `
        -Uri "$($PortalURL.TrimEnd('/'))/api/tray/trmm-sync" `
        -Headers @{ 'X-API-Key' = $APIKey } `
        -ContentType 'application/json' -Body $body
} catch {
    $responseBody = $null
    if ($_.Exception.Response) {
        try {
            $stream = $_.Exception.Response.GetResponseStream()
            $reader = [System.IO.StreamReader]::new($stream)
            $responseBody = $reader.ReadToEnd()
        } catch { }
    }
    if (-not $responseBody -and $_.ErrorDetails.Message) {
        $responseBody = $_.ErrorDetails.Message
    }
    throw "MyPortal TRMM sync failed: $responseBody"
}

Write-Host "Linked TRMM agent $AgentID to MyPortal asset $($result.asset_id) and tray device $trayAgentID."
