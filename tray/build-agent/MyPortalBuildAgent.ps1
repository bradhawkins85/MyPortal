#Requires -Version 7.2
<#
.SYNOPSIS
    Builds company-specific MyPortal Tray installers (MSI and setup EXE).

.DESCRIPTION
    Polls MyPortal for queued deployment URL builds. For each job it builds the
    tray binaries for the job's release tag from source (cached per tag, signed
    with your certificate), builds myportal-tray.msi with the company's portal
    URL and install token baked in,
    wraps it in a Burn setup.exe, signs both, uploads them and marks the job
    complete. Failures are reported back so they show on the admin page.

    Installed and scheduled by Install-MyPortalBuildAgent.ps1. Runs once per
    invocation unless -Loop is passed. See
    docs/wiki/getting-started/Tray Build Server.md.
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:ProgramData 'MyPortalBuildAgent\config.json'),
    [switch]$Loop,
    [ValidateRange(15, 3600)][int]$PollSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
foreach ($name in 'PortalUrl', 'ApiKey', 'CertificateThumbprint') {
    if ([string]::IsNullOrWhiteSpace($config.$name)) { throw "$name is missing from $ConfigPath" }
}

$PortalUrl = $config.PortalUrl.TrimEnd('/')
$Headers = @{ 'x-api-key' = $config.ApiKey }
$GitHubRepo = if ($config.PSObject.Properties['GitHubRepo'] -and $config.GitHubRepo) { $config.GitHubRepo } else { 'bradhawkins85/MyPortal' }
$WorkDir = if ($config.PSObject.Properties['WorkDir'] -and $config.WorkDir) { $config.WorkDir } else { 'C:\MyPortalBuild' }
$Wix = if ($config.PSObject.Properties['WixPath'] -and $config.WixPath) { $config.WixPath } else { 'wix' }
$TimestampServer = if ($config.PSObject.Properties['TimestampServer']) { $config.TimestampServer } else { $null }

$PayloadDir = Join-Path $WorkDir 'payloads'
$JobsDir = Join-Path $WorkDir 'jobs'
$LogPath = Join-Path $WorkDir 'agent.log'
New-Item -ItemType Directory -Force -Path $PayloadDir, $JobsDir | Out-Null

function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line
}

function Invoke-Wix {
    $output = & $Wix @args 2>&1
    if ($LASTEXITCODE -ne 0) {
        $detail = ($output | Select-Object -Last 15) -join [Environment]::NewLine
        throw "wix $($args[0]) failed with exit code ${LASTEXITCODE}: $detail"
    }
}

function Initialize-Wix {
    $extensions = & $Wix extension list -g 2>&1
    if (-not ($extensions -match 'WixToolset\.BootstrapperApplications\.wixext')) {
        $version = ((& $Wix --version) -replace '\+.*$', '').Trim()
        Write-Log "Installing WixToolset.BootstrapperApplications.wixext/$version"
        Invoke-Wix extension add -g "WixToolset.BootstrapperApplications.wixext/$version"
    }
}

function Get-SigningCertificate {
    $cert = Get-Item -LiteralPath ("Cert:\LocalMachine\My\" + $config.CertificateThumbprint) -ErrorAction SilentlyContinue
    if (-not $cert) { $cert = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $config.CertificateThumbprint) -ErrorAction SilentlyContinue }
    if (-not $cert -or -not $cert.HasPrivateKey) {
        throw "Code-signing certificate $($config.CertificateThumbprint) with a private key was not found."
    }
    return $cert
}

function Set-Signature([string]$Path) {
    $params = @{ FilePath = $Path; Certificate = (Get-SigningCertificate); HashAlgorithm = 'SHA256' }
    if ($TimestampServer) { $params.TimestampServer = $TimestampServer }
    Set-AuthenticodeSignature @params | Out-Null
    $signature = Get-AuthenticodeSignature -FilePath $Path
    if ($signature.Status -in @('NotSigned', 'HashMismatch')) {
        throw "Signing $Path failed: $($signature.StatusMessage)"
    }
}

function Invoke-Native([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

# Builds the signed tray binaries for a release tag from source, the same way
# the Build MSI workflow does, without touching the GitHub release itself.
function Get-Payload([string]$Tag) {
    if ($Tag -notmatch '^[A-Za-z0-9][A-Za-z0-9._/+-]{0,127}$') { throw "Invalid release tag: $Tag" }
    $dir = Join-Path $PayloadDir ($Tag -replace '[^A-Za-z0-9._-]', '_')
    if (Test-Path -LiteralPath (Join-Path $dir '.complete')) { return $dir }

    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
    $src = Join-Path $dir 'src'
    $bin = Join-Path $dir 'bin'
    $installer = Join-Path $dir 'installer'
    New-Item -ItemType Directory -Force -Path $dir, $bin, $installer | Out-Null

    Write-Log "Building tray binaries for $Tag"
    git clone --quiet --depth 1 --branch $Tag "https://github.com/$GitHubRepo.git" $src 2>&1 | Out-Null
    Invoke-Native "git clone of $Tag"

    $wxs = Join-Path $src 'tray\installer\windows\myportal-tray.wxs'
    $bundle = Join-Path $src 'tray\installer\windows\myportal-tray-bundle.wxs'
    if (-not (Test-Path -LiteralPath $bundle) -or -not (Select-String -LiteralPath $wxs -Pattern 'DefaultPortalUrl' -Quiet)) {
        throw "Release $Tag predates deployment URL support. Publish a newer tray release, then press Rebuild."
    }
    Copy-Item -LiteralPath $wxs, $bundle -Destination $installer

    $tray = Join-Path $src 'tray'
    Push-Location $tray
    try {
        $env:GOOS = 'windows'; $env:GOARCH = 'amd64'; $env:CGO_ENABLED = '0'
        $ldflags = "-X 'github.com/bradhawkins85/myportal-tray/internal/updater.AgentVersion=$Tag'"
        go build -ldflags $ldflags -o (Join-Path $bin 'myportal-tray-service.exe') .\service
        Invoke-Native 'go build service'
        $env:GOFLAGS = '-tags=nowebview'
        go build -ldflags "$ldflags -H windowsgui" -o (Join-Path $bin 'myportal-tray-ui.exe') .\ui
        Invoke-Native 'go build ui'
    } finally {
        Remove-Item Env:GOFLAGS -ErrorAction SilentlyContinue
        Pop-Location
    }

    Push-Location (Join-Path $tray 'chat-shell')
    try {
        npm ci --no-audit --no-fund | Out-Null
        Invoke-Native 'npm ci'
        npm run build:win | Out-Null
        Invoke-Native 'npm run build:win'
    } finally {
        Pop-Location
    }
    $unpacked = Join-Path $tray 'chat-shell\dist\win-unpacked'
    if (-not (Test-Path -LiteralPath $unpacked)) { throw 'electron-builder did not create chat-shell\dist\win-unpacked' }
    Copy-Item -LiteralPath $unpacked -Destination (Join-Path $bin 'chat-shell') -Recurse -Force

    foreach ($exe in Get-ChildItem -LiteralPath $bin -Filter '*.exe' -File -Recurse) {
        Set-Signature $exe.FullName
    }
    Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType File -Path (Join-Path $dir '.complete') | Out-Null
    return $dir
}

function Invoke-Portal([string]$Method, [string]$Path, $Body) {
    $params = @{ Method = $Method; Uri = "$PortalUrl$Path"; Headers = $Headers; UseBasicParsing = $true; TimeoutSec = 120 }
    if ($null -ne $Body) {
        $params.Body = ($Body | ConvertTo-Json -Compress)
        $params.ContentType = 'application/json'
    }
    return Invoke-WebRequest @params
}

function Send-Artifact([int]$BuildId, [string]$Kind, [string]$Path) {
    Invoke-WebRequest -Method Put -Uri "$PortalUrl/api/tray/build-agent/jobs/$BuildId/artifacts/$Kind" `
        -Headers $Headers -InFile $Path -ContentType 'application/octet-stream' `
        -UseBasicParsing -TimeoutSec 3600 | Out-Null
}

function Invoke-Build($Job) {
    $url = [string]$Job.portal_url
    $token = [string]$Job.enrol_token
    $version = [string]$Job.product_version
    if ($url -notmatch '^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~\-/]*)?$') { throw 'Portal URL from the server is not valid.' }
    if ($token -notmatch '^[A-Za-z0-9_\-]{16,128}$') { throw 'Install token from the server is not valid.' }
    if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "Product version $version is not valid." }

    $payload = Get-Payload ([string]$Job.release_tag)
    $out = Join-Path $JobsDir ([string][int]$Job.id)
    Remove-Item -LiteralPath $out -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    try {
        $msi = Join-Path $out 'myportal-tray.msi'
        Invoke-Wix build -acceptEula wix7 (Join-Path $payload 'installer\myportal-tray.wxs') -arch x64 `
            -d "BinDir=$(Join-Path $payload 'bin')" -d "ProductVersion=$version" `
            -d "DefaultPortalUrl=$url" -d "DefaultEnrolToken=$token" -o $msi
        Set-Signature $msi

        $exe = Join-Path $out 'setup.exe'
        $engine = Join-Path $out 'engine.exe'
        Invoke-Wix build -acceptEula wix7 (Join-Path $payload 'installer\myportal-tray-bundle.wxs') -arch x64 `
            -ext WixToolset.BootstrapperApplications.wixext `
            -d "MsiPath=$msi" -d "ProductVersion=$version" -o $exe
        # Burn bundles carry a separate engine that must be signed first.
        Invoke-Wix burn detach $exe -engine $engine
        Set-Signature $engine
        Invoke-Wix burn reattach $exe -engine $engine -o $exe
        Set-Signature $exe

        Send-Artifact $Job.id 'msi' $msi
        Send-Artifact $Job.id 'exe' $exe
        Invoke-Portal Post "/api/tray/build-agent/jobs/$($Job.id)/complete" $null | Out-Null
    } finally {
        # The working folder holds the company's install token.
        Remove-Item -LiteralPath $out -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Queue {
    while ($true) {
        $response = Invoke-Portal Post '/api/tray/build-agent/jobs/claim' $null
        if ($response.StatusCode -eq 204) { return }
        $job = $response.Content | ConvertFrom-Json
        Write-Log "Building job $($job.id) for $($job.company_name) ($($job.release_tag))"
        try {
            Invoke-Build $job
            Write-Log "Job $($job.id) complete"
        } catch {
            $message = $_.Exception.Message
            Write-Log "Job $($job.id) failed: $message"
            try {
                Invoke-Portal Post "/api/tray/build-agent/jobs/$($job.id)/fail" @{ error = $message } | Out-Null
            } catch {
                Write-Log "Could not report failure for job $($job.id): $($_.Exception.Message)"
            }
        }
    }
}

$mutex = [System.Threading.Mutex]::new($false, 'Global\MyPortalBuildAgent')
if (-not $mutex.WaitOne(0)) { Write-Host 'Another build agent run is in progress.'; exit 0 }
try {
    Initialize-Wix
    do {
        try {
            Invoke-Queue
        } catch {
            Write-Log "Could not reach MyPortal: $($_.Exception.Message)"
            if (-not $Loop) { exit 1 }
        }
        if ($Loop) { Start-Sleep -Seconds $PollSeconds }
    } while ($Loop)
} finally {
    $mutex.ReleaseMutex()
}
