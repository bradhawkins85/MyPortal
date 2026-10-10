# Remote Control

Technicians connect to a device with **RustDesk** or **MeshCentral** from the asset's
**Scripts** card. Neither tool has to be running all the time: MyPortal runs an **activation
script** on the device through the RMM agent, the script switches the tool on and reports how to
connect, and MyPortal opens the session.

The activation script decides what the device needs:

- **Already installed:** start the service, reset the access password and report the ID.
- **Not installed:** install it with the company's settings first, then do the same.

## Setting it up

Super admins set it up under **Scripts → Remote control** (`/rmm/remote-control`). For each
provider:

1. Add the activation script to the Gitea repository's `Common/` folder (examples below) and
   choose **Sync from Gitea**. Only `Common/` scripts are offered, as they run on every
   company's devices.
2. Turn the provider on, choose the script and save. The script's parameters then appear under
   **Script values**. Fill them in with typed values or MyPortal variables, such as
   `{{company.variables.RustDeskConfig}}`, so each company installs with its own settings.
   Secret values are kept encrypted; leave them blank to keep the saved value.
3. Optionally name an **ID custom field**. MyPortal uses that asset custom field when the
   script does not report an ID, or when no script is chosen at all.

MeshCentral also needs:

- **MeshCentral address**, such as `https://mesh.example.com`.
- **MeshCentral user** that technicians sign in as.
- **Login token key**, printed by `node node_modules/meshcentral --logintokenkey` on the
  MeshCentral server. When moving from Tactical RMM, the same user and key Tactical uses work
  here.

## Connecting

On an asset, **Connect with RustDesk** or **Connect with MeshCentral** queues the activation
script and shows its progress. When the script finishes, **Open RustDesk** (or **Open
MeshCentral**) appears:

- RustDesk opens the desktop app with a `rustdesk://` link carrying the ID and password.
- MeshCentral opens in a new tab already signed in and goes straight to the device's desktop.
  Without a node ID it opens MeshCentral signed in.

The device must be online: an activation script the device has not collected within 10 minutes
expires. A session's link, and any password the script reported, is kept encrypted for 30
minutes and is only shown to the technician who started it (and super admins).

Connecting needs write access to **Scripts** for the company. Each connection is recorded in the
audit trail as `rmm.remote_control.start`, and the activation run appears in the device's run
history as **Remote control**.

## What the script reports

Print `session` values the same way as other [returned values](RMM%20Scripts.md#returning-values-to-myportal):

| Line | Provider | Meaning |
| --- | --- | --- |
| `##myportal[session.id]=123456789` | RustDesk | The RustDesk ID (spaces are ignored) |
| `##myportal[session.password]=...` | RustDesk | The password to connect with (optional) |
| `##myportal[session.node_id]=...` | MeshCentral | The device's MeshCentral node ID |

Or write `[{"scope": "session", "name": "id", "value": "123456789"}]` to
`$MYPORTAL_RESULT_FILE`. The values are blanked out of the run's output and shown masked in run
history. Other scripts cannot use `session` values.

## Example: RustDesk (Windows)

```powershell
<#
.SYNOPSIS
Switches RustDesk on for a remote session. Installs it when missing, sets a new
password, then reports the ID and password to MyPortal.
#>
param(
    [string]$InstallerUrl,  # RustDesk installer .exe, used only when RustDesk is missing
    [string]$Config         # Server config string (RustDesk Settings > Network > Export)
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $env:ProgramFiles 'RustDesk\rustdesk.exe'
if (-not (Test-Path $exe)) {
    if (-not $InstallerUrl) { throw 'RustDesk is not installed and no installer URL is set.' }
    $installer = Join-Path $env:TEMP 'rustdesk-setup.exe'
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $installer -UseBasicParsing
    Start-Process $installer -ArgumentList '--silent-install' -Wait
    $deadline = (Get-Date).AddMinutes(2)
    while (-not (Test-Path $exe) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }
}
if (-not (Get-Service RustDesk -ErrorAction SilentlyContinue)) {
    & $exe --install-service | Out-Null
    Start-Sleep -Seconds 5
}
Start-Service RustDesk
if ($Config) { & $exe --config $Config | Out-Null }
$bytes = New-Object byte[] 9
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$password = [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x'
& $exe --password $password | Out-Null
$id = (& $exe --get-id | Out-String).Trim()
Write-Output "##myportal[session.id]=$id"
Write-Output "##myportal[session.password]=$password"
```

## Example: MeshCentral (Windows)

```powershell
<#
.SYNOPSIS
Makes sure the MeshCentral agent is installed and running, then reports its node ID.
#>
param(
    [string]$InstallerUrl  # MeshCentral agent download link for the company's device group
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $env:ProgramFiles 'Mesh Agent\MeshAgent.exe'
if (-not (Test-Path $exe)) {
    if (-not $InstallerUrl) { throw 'The MeshCentral agent is not installed and no installer URL is set.' }
    $installer = Join-Path $env:TEMP 'meshagent.exe'
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $installer -UseBasicParsing
    Start-Process $installer -ArgumentList '-fullinstall' -Wait
    Start-Sleep -Seconds 10
}
Start-Service 'Mesh Agent'
$node = (& $exe -nodeid | Out-String).Trim()
Write-Output "##myportal[session.node_id]=$node"
```

Adjust both examples to match the scripts you already use to switch these tools on.
