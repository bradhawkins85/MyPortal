# MyPortal built-in: switch MeshCentral on for a remote session (Windows).
#
# MyPortal sets these for each run:
#   MESH_INSTALLER_PATH the installer delivered by MyPortal through the RMM
#   MESH_GROUP_ID the device group to install into (the device's company group)
#
# Installs the delivered MeshCentral agent as a background service so the device
# joins its company's group, then reports the agent's node id (meshagent -nodeid)
# so MyPortal opens that device; MyPortal checks it is in the company's group.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$activationDeadline = (Get-Date).AddSeconds(280)

if (-not $env:MESH_INSTALLER_PATH -or -not (Test-Path -LiteralPath $env:MESH_INSTALLER_PATH -PathType Leaf)) { throw 'The MeshCentral installer was not delivered by the RMM.' }
if (-not $env:MESH_GROUP_ID) { throw 'The MeshCentral device group is missing.' }

function Invoke-MeshCommand {
    param([string]$File, [string[]]$Arguments, [string]$Step, [int]$TimeoutSeconds = 120)
    $remaining = [int]($activationDeadline - (Get-Date)).TotalSeconds
    if ($remaining -le 0) { throw "MeshCentral activation timed out during: $Step." }
    $stdout = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName())
    $stderr = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName())
    try {
        $quoted = @($Arguments | ForEach-Object {
            if ($_ -match '["\r\n]') { throw "Invalid argument for: $Step." }
            '"' + $_ + '"'
        })
        $startParameters = @{
            FilePath = $File
            PassThru = $true
            WindowStyle = 'Hidden'
            RedirectStandardOutput = $stdout
            RedirectStandardError = $stderr
        }
        # Start-Process rejects an empty ArgumentList; omit it when unused.
        if ($quoted.Count -gt 0) { $startParameters.ArgumentList = $quoted }
        $process = Start-Process @startParameters
        $null = $process.Handle
        if (-not $process.WaitForExit([Math]::Min($TimeoutSeconds, $remaining) * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "MeshCentral timed out during: $Step."
        }
        $details = (Get-Content -LiteralPath $stdout, $stderr -Raw -ErrorAction SilentlyContinue) -join "`n"
        # Some MeshAgent installer failures print [ERROR] but return exit code 0.
        if ($process.ExitCode -ne 0 -or $details -match '\[ERROR\]') {
            throw "MeshCentral failed during: $Step (exit code $($process.ExitCode)). $details"
        }
        return Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

# Always re-run the full installer: re-running it
# scoped to this group is what moves the device into its company's group (a
# device that was in the generic TacticalRMM group is moved here, too).
$installer = $env:MESH_INSTALLER_PATH
try {
    Write-Output "Installing the MeshCentral agent (Windows, into group $env:MESH_GROUP_ID)"
    # A bare launch opens the interactive agent in session 0 and never installs.
    # -fullinstall copies the configured executable, registers and starts the service.
    Invoke-MeshCommand -File $installer -Arguments @('-fullinstall') -Step 'service installation' -TimeoutSeconds 120
    $deadline = (Get-Date).AddSeconds(60)
    do {
        if (Get-Service -Name 'Mesh Agent' -ErrorAction SilentlyContinue) { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline -and (Get-Date) -lt $activationDeadline)
    if (-not (Get-Service -Name 'Mesh Agent' -ErrorAction SilentlyContinue)) { throw 'The MeshCentral agent did not finish installing.' }
} finally {
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
}

# Make sure the service is running so the agent checks in.
Set-Service -Name 'Mesh Agent' -StartupType Automatic
$service = Get-Service -Name 'Mesh Agent'
if ($service.Status -ne 'Running') {
    $service.Start()
    $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
}
Write-Output 'The MeshCentral agent is installed and running.'

# Report the node id (as Tactical RMM reads it) so MyPortal can open this device
# directly. A fresh agent needs a moment to create its identity; without an id,
# MyPortal falls back to matching the device's host name. Reading the id never
# fails an activation that otherwise worked.
$nodeId = $null
try {
    $agentPath = "$env:ProgramFiles\Mesh Agent\MeshAgent.exe"
    try {
        $serviceInfo = Get-CimInstance -ClassName Win32_Service -Filter "Name='Mesh Agent'" -ErrorAction Stop
        if ($serviceInfo -and $serviceInfo.PathName -match '^\s*"?([^"]+?\.exe)') { $agentPath = $Matches[1] }
    } catch {
        # Fall back to the default install location.
    }
    for ($attempt = 0; $attempt -lt 10 -and -not $nodeId -and (Get-Date) -lt $activationDeadline; $attempt++) {
        if ($attempt) { Start-Sleep -Seconds 3 }
        try {
            $output = (Invoke-MeshCommand -File $agentPath -Arguments @('-nodeid') -Step 'reading the node id' -TimeoutSeconds 15) -as [string]
            $candidate = ($output -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
            if ($candidate) { $candidate = $candidate.Trim() }
            if ($candidate -match '^[A-Za-z0-9@$/_+=.-]{16,128}$') { $nodeId = $candidate }
        } catch {
            Write-Output "Could not read the node id yet: $($_.Exception.Message)"
        }
    }
} catch {
    Write-Output "Could not read the node id: $($_.Exception.Message)"
}
if ($nodeId) { Write-Output "##myportal[session.node_id]=$nodeId" }
else { Write-Output 'The node id is not available yet; MyPortal will find the device by its host name.' }
