# MyPortal built-in: switch RustDesk on for a remote session (Windows).
#
# MyPortal sets these for each run:
#   MYPORTAL_RUSTDESK_PASSWORD  one-time password for this session
#   RUSTDESK_CFG                server config string (RUSTDESK_CFG in MyPortal's .env)
#   RUSTDESK_INSTALLER_URL      installer used when RustDesk is missing (optional)
#
# Applies the config, starts the service, sets the password and reports the ID.
$ErrorActionPreference = 'Stop'
$exe = Join-Path $env:ProgramFiles 'RustDesk\rustdesk.exe'

if (-not (Test-Path $exe)) {
    if (-not $env:RUSTDESK_INSTALLER_URL) {
        throw 'RustDesk is not installed on this device. Set RUSTDESK_INSTALLER_URL in MyPortal to install it automatically.'
    }
    Write-Output 'Installing RustDesk'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $installer = Join-Path $env:TEMP 'myportal-rustdesk-setup.exe'
    Invoke-WebRequest -Uri $env:RUSTDESK_INSTALLER_URL -OutFile $installer -UseBasicParsing
    Start-Process -FilePath $installer -ArgumentList '--silent-install' -Wait
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-Path $exe) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }
    if (-not (Test-Path $exe)) { throw 'RustDesk did not finish installing.' }
}

if (-not (Get-Service -Name 'RustDesk' -ErrorAction SilentlyContinue)) {
    Write-Output 'Installing the RustDesk service'
    Start-Process -FilePath $exe -ArgumentList '--install-service' -Wait
    Start-Sleep -Seconds 5
}

if ($env:RUSTDESK_CFG) {
    Write-Output 'Applying the RustDesk server config'
    & $exe --config $env:RUSTDESK_CFG | Out-Null
}

Write-Output 'Starting the RustDesk service'
Set-Service -Name 'RustDesk' -StartupType Automatic -ErrorAction SilentlyContinue
Restart-Service -Name 'RustDesk' -Force
Start-Sleep -Seconds 3

Write-Output 'Setting a new session password'
& $exe --password $env:MYPORTAL_RUSTDESK_PASSWORD | Out-Null

$id = ''
$deadline = (Get-Date).AddSeconds(45)
while (-not $id -and (Get-Date) -lt $deadline) {
    $id = ((& $exe --get-id | Out-String) -replace '\s', '')
    if (-not $id) { Start-Sleep -Seconds 3 }
}
if (-not $id) { throw 'RustDesk did not report its ID.' }
Write-Output "##myportal[session.id]=$id"
