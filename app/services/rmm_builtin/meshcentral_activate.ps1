# MyPortal built-in: switch MeshCentral on for a remote session (Windows).
#
# MyPortal sets these for each run:
#   MESH_INSTALLER_PATH the installer delivered by MyPortal through the RMM
#   MESH_GROUP_ID the device group to install into (the device's company group)
#
# Runs the self-installing MeshCentral agent delivered for that group,
# it, so the device joins its company's group. The node id is resolved by
# MyPortal afterwards (the agent has no command-line that prints it).
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
        # The self-installing agent takes no arguments. Start-Process rejects an
        # empty ArgumentList, so leave that parameter out for this invocation.
        if ($quoted.Count -gt 0) { $startParameters.ArgumentList = $quoted }
        $process = Start-Process @startParameters
        $null = $process.Handle
        if (-not $process.WaitForExit([Math]::Min($TimeoutSeconds, $remaining) * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "MeshCentral timed out during: $Step."
        }
        if ($process.ExitCode -ne 0) { throw "MeshCentral failed during: $Step (exit code $($process.ExitCode))." }
        return Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

# Always re-run the self-installing agent: it is idempotent, and re-running it
# scoped to this group is what moves the device into its company's group (a
# device that was in the generic TacticalRMM group is moved here, too).
$installer = $env:MESH_INSTALLER_PATH
try {
    Write-Output "Installing the MeshCentral agent (Windows, into group $env:MESH_GROUP_ID)"
    # The downloaded artifact is a self-installing, background installer.
    Invoke-MeshCommand -File $installer -Arguments @() -Step 'installation' -TimeoutSeconds 120 | Out-Null
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
$service = Get-Service -Name 'Mesh Agent'
if ($service.Status -ne 'Running') {
    $service.Start()
    $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
}
Write-Output 'The MeshCentral agent is installed and running.'
