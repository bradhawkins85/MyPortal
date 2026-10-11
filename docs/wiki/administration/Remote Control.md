# Remote Control

Technicians connect to a device with **RustDesk** or **MeshCentral** from the asset's
**Scripts** card, or beside each asset linked to a ticket (`/admin/tickets/<id>`). Neither tool
has to be running all the time: MyPortal runs an **activation script** on the device through the
RMM agent, the script switches the tool on and reports how to connect, and MyPortal opens the
session for the technician.

## RustDesk

RustDesk works out of the box with MyPortal's **built-in activation script**. Add the RustDesk
server config to MyPortal's `.env` file:

```bash
# RustDesk > Settings > Network > Export server config
RUSTDESK_CFG=0nI9...
# Optional: installer used on Windows devices without RustDesk
RUSTDESK_INSTALLER_URL=https://downloads.example.com/rustdesk-x86_64.exe
```

When a technician starts a RustDesk session, the RMM agent:

1. installs RustDesk from `RUSTDESK_INSTALLER_URL` if it is missing (Windows only);
2. applies `RUSTDESK_CFG` (`rustdesk --config`), so the device uses your RustDesk server;
3. starts the RustDesk service;
4. sets a new one-time password that MyPortal generated for this session;
5. reports the RustDesk ID.

MyPortal then opens the session in the technician's RustDesk app. Every session sets a new
password, so an earlier session's password stops working. The built-in script runs on Windows
(PowerShell) and on macOS and Linux (Bash). Without `RUSTDESK_CFG` the device keeps its current
RustDesk server settings.

To do something different, choose your own activation script instead (see
[Your own activation script](#your-own-activation-script)).

## Setting it up

Super admins set it up under **Scripts → Remote control** (`/rmm/remote-control`). For each
provider:

1. Turn it on. RustDesk uses the built-in script unless you choose another; MeshCentral needs
   a script that reports the node ID, or the ID custom field below.
2. To use your own script, add it to the Gitea repository's `Common/` folder and choose
   **Sync from Gitea**, then choose it and save. Only `Common/` scripts are offered, as they
   run on every company's devices. The script's parameters then appear under **Script
   values**. Fill them in with typed values or MyPortal variables, such as
   `{{company.variables.RustDeskConfig}}`, so each company can use its own settings. Secret
   values are kept encrypted; leave them blank to keep the saved value.
3. Optionally name an **ID custom field**. MyPortal uses that asset custom field when a device
   has no RMM agent, or when the script does not report an ID.

MeshCentral also needs:

- **MeshCentral address**, such as `https://mesh.example.com`.
- **MeshCentral user** that technicians sign in as.
- **Login token key**, printed by `node node_modules/meshcentral --logintokenkey` on the
  MeshCentral server. When moving from Tactical RMM, the same user and key Tactical uses work
  here.

## Connecting

Beside each asset (on its **Scripts** card, and in a ticket's asset list):

- **RustDesk** starts a remote desktop session. The **▾** beside it offers **Remote desktop**,
  **File transfer** and **Terminal** sessions (terminal needs RustDesk 1.4.1 or later on the
  technician's computer).
- The **RMM** menu holds MeshCentral (desktop, files or terminal), **Run script** on the asset
  page, and future RMM actions.

MyPortal shows the activation script's progress, then opens the session as soon as the device
reports back: RustDesk through a `rustdesk://` link carrying the ID and password (the browser may
ask to open RustDesk the first time), MeshCentral in a new tab already signed in on the device.
An **Open … again** button stays in case the browser blocked it.

The device must be online: an activation script the device has not collected within 10 minutes
expires. A session's link and password are kept encrypted for 30 minutes and only shown to the
technician who started it (and super admins).

Connecting needs write access to **Scripts** for the device's company (on a ticket, the
ticket's company, whichever company is selected). Each connection is recorded in the
audit trail as `rmm.remote_control.start`, and the activation run appears in the device's run
history as **Remote control**.

## Your own activation script

Your own script decides what the device needs, for example installing the tool with the
company's settings when it is missing, otherwise resetting the access password, then
reporting how to connect.

### What the script reports

Print `session` values the same way as other [returned values](RMM%20Scripts.md#returning-values-to-myportal):

| Line | Provider | Meaning |
| --- | --- | --- |
| `##myportal[session.id]=123456789` | RustDesk | The RustDesk ID (spaces are ignored) |
| `##myportal[session.password]=...` | RustDesk | The password to connect with (optional) |
| `##myportal[session.node_id]=...` | MeshCentral | The device's MeshCentral node ID |

Or write `[{"scope": "session", "name": "id", "value": "123456789"}]` to
`$MYPORTAL_RESULT_FILE`. The values are blanked out of the run's output and shown masked in run
history. Other scripts cannot use `session` values.

### Example: RustDesk (Windows)

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

### Example: MeshCentral (Windows)

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
