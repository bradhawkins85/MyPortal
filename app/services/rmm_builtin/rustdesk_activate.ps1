# MyPortal built-in: switch RustDesk on for a remote session (Windows).
# Detect an existing installation, otherwise install the latest stable GitHub
# release (RUSTDESK_INSTALLER_URL optionally overrides the download URL).
# Then apply RUSTDESK_CFG, start the service and set MYPORTAL_RUSTDESK_PASSWORD.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$activationDeadline = (Get-Date).AddSeconds(280)

function Find-RustDesk {
    $service = Get-CimInstance Win32_Service -Filter "Name='RustDesk'" -ErrorAction SilentlyContinue
    if ($service -and $service.PathName -match '^"?(.+?\.exe)(?:"|\s|$)') {
        if (Test-Path -LiteralPath $Matches[1]) { return $Matches[1] }
    }
    foreach ($root in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($root) {
            $candidate = Join-Path $root 'RustDesk\rustdesk.exe'
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
    }
    $command = Get-Command rustdesk.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
}

function Invoke-RustDeskCommand {
    param([string]$File, [string[]]$Arguments, [string]$Step, [int]$TimeoutSeconds = 20)
    $remaining = [int]($activationDeadline - (Get-Date)).TotalSeconds
    if ($remaining -le 0) { throw "RustDesk activation timed out during: $Step." }
    $stdout = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName())
    $stderr = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName())
    try {
        # Start-Process joins arguments on Windows; quote each to retain spaces.
        $quoted = @($Arguments | ForEach-Object {
            if ($_ -match '["\r\n]') { throw "Invalid argument for: $Step." }
            '"' + $_ + '"'
        })
        $process = Start-Process -FilePath $File -ArgumentList $quoted -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $null = $process.Handle
        if (-not $process.WaitForExit([Math]::Min($TimeoutSeconds, $remaining) * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "RustDesk timed out during: $Step."
        }
        if ($process.ExitCode -ne 0) { throw "RustDesk failed during: $Step (exit code $($process.ExitCode))." }
        return Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    } finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

if (-not $env:MYPORTAL_RUSTDESK_PASSWORD) { throw 'The RustDesk session password is missing.' }
Write-Output 'Checking whether RustDesk is installed'
$exe = Find-RustDesk
if (-not $exe) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $installerUrl = $env:RUSTDESK_INSTALLER_URL
    if (-not $installerUrl) {
        Write-Output 'Finding the latest stable RustDesk release on GitHub'
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest' -Headers @{ 'User-Agent' = 'MyPortal-RMM'; 'Accept' = 'application/vnd.github+json' } -TimeoutSec 30
        $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        $assetArchitecture = switch ($architecture.ToUpperInvariant()) {
            'AMD64' { 'x86_64' }
            'ARM64' { 'aarch64' }
            'X86' { 'x86' }
            default { throw "Unsupported Windows architecture: $architecture." }
        }
        $asset = @($release.assets | Where-Object { $_.name -match "^rustdesk-[0-9].*-$assetArchitecture\.exe$" })
        if ($asset.Count -ne 1) { throw "The latest RustDesk release has no unique Windows $assetArchitecture installer." }
        $installerUrl = $asset[0].browser_download_url
        Write-Output "Installing RustDesk $($release.tag_name) ($assetArchitecture)"
    } else {
        Write-Output 'Installing RustDesk from the configured installer URL'
    }
    $installer = Join-Path $env:TEMP (([IO.Path]::GetRandomFileName()) + '.exe')
    try {
        Invoke-WebRequest -Uri $installerUrl -OutFile $installer -UseBasicParsing -TimeoutSec 90
        Invoke-RustDeskCommand -File $installer -Arguments @('--silent-install') -Step 'installation' -TimeoutSeconds 90 | Out-Null
        $deadline = (Get-Date).AddSeconds(30)
        do {
            $exe = Find-RustDesk
            if ($exe) { break }
            Start-Sleep -Seconds 2
        } while ((Get-Date) -lt $deadline -and (Get-Date) -lt $activationDeadline)
        if (-not $exe) { throw 'RustDesk did not finish installing.' }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Output 'Using the installed RustDesk client'
}

if (-not (Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue)) {
    Write-Output 'Installing the RustDesk service'
    Invoke-RustDeskCommand -File $exe -Arguments @('--install-service') -Step 'service installation' | Out-Null
    $deadline = (Get-Date).AddSeconds(15)
    while (-not (Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
}

if ($env:RUSTDESK_CFG) {
    Write-Output 'Applying the RustDesk server config'
    Invoke-RustDeskCommand -File $exe -Arguments @('--config', $env:RUSTDESK_CFG) -Step 'server configuration' | Out-Null
}

Write-Output 'Starting the RustDesk service'
Set-Service -Name 'RustDesk' -StartupType Automatic
# Use non-blocking service operations and explicitly bound the state waits.
$service = Get-Service -Name 'RustDesk'
if ($service.Status -ne 'Stopped') {
    $service.Stop()
    $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15))
}
$service.Start()
$service.WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
Start-Sleep -Seconds 3

Write-Output 'Setting a new session password'
Invoke-RustDeskCommand -File $exe -Arguments @('--password', $env:MYPORTAL_RUSTDESK_PASSWORD) -Step 'session password' | Out-Null

$id = ''
$deadline = (Get-Date).AddSeconds(45)
while (-not $id -and (Get-Date) -lt $deadline -and (Get-Date) -lt $activationDeadline) {
    $id = (Invoke-RustDeskCommand -File $exe -Arguments @('--get-id') -Step 'reading the device ID') -replace '\s', ''
    if ($id -and $id -notmatch '^\d+$') { throw 'RustDesk reported an invalid device ID.' }
    if (-not $id) { Start-Sleep -Seconds 3 }
}
if (-not $id) { throw 'RustDesk did not report its ID.' }
Write-Output "##myportal[session.id]=$id"
