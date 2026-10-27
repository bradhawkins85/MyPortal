# Remote Control

Technicians connect to a device with **RustDesk** or **MeshCentral** from the asset's
**Scripts** card, or beside each asset linked to a ticket (`/admin/tickets/<id>`). Neither tool
has to be running all the time: MyPortal runs an **activation script** on the device through the
RMM agent, the script switches the tool on and reports how to connect, and MyPortal opens the
session for the technician.

The buttons only appear for devices with the RMM agent installed. Each device shows its RMM agent
status: a green tick when the agent is connected, an **O** when it is installed but has not
checked in for the last couple of minutes, and a cross when it is not installed. The `/assets`
list has the same status in its **RMM agent** column.

## RustDesk

RustDesk works out of the box with MyPortal's **built-in activation script**. Add the RustDesk
server config to MyPortal's `.env` file:

```bash
# RustDesk > Settings > Network > Export server config
RUSTDESK_CFG=0nI9...
# Optional overrides: leave blank to use the latest stable GitHub release
RUSTDESK_INSTALLER_URL=https://downloads.example.com/rustdesk-x86_64.exe
RUSTDESK_MACOS_INSTALLER_URL=https://downloads.example.com/rustdesk-aarch64.dmg
```

When a technician starts a RustDesk session, the RMM agent:

1. checks for an existing RustDesk installation; if missing on Windows or macOS, downloads
   the latest stable release from `rustdesk/rustdesk` on GitHub for the device architecture
   (`x86_64`, `x86` or `aarch64` on Windows; Intel or Apple silicon on macOS) and installs it
   silently (`RUSTDESK_INSTALLER_URL` overrides the Windows `.exe` download and
   `RUSTDESK_MACOS_INSTALLER_URL` the macOS `.dmg`);
2. installs the RustDesk service when it is missing (on macOS, the same launch daemon and
   launch agent that RustDesk's own **Install** button creates);
3. applies `RUSTDESK_CFG` (`rustdesk --config`), so the device uses your RustDesk server;
4. starts the RustDesk service;
5. sets a new one-time password that MyPortal generated for this session;
6. reports the RustDesk ID.

MyPortal then opens the session in the technician's RustDesk app. Every session sets a new
password, so an earlier session's password stops working. The built-in script runs on Windows
(PowerShell) and on macOS and Linux (Bash). Without `RUSTDESK_CFG` the device keeps its current
RustDesk server settings. Windows and macOS downloads and client commands have time limits,
so a failed installation or configuration returns an error instead of waiting indefinitely.
Linux still requires RustDesk to be installed beforehand.

### macOS permissions

macOS only lets RustDesk see and control the screen once **Screen Recording** and
**Accessibility** are allowed for it, and no script can grant those. The first time, the
signed-in user approves RustDesk under **System Settings → Privacy & Security**, or you
pre-approve it with an MDM configuration profile (Privacy Preferences Policy Control) for
the bundle ID `com.carriez.rustdesk`. Until then a session connects but shows a black or
view-only screen.

To do something different, choose your own activation script instead (see
[Your own activation script](#your-own-activation-script)).

## Setting it up

Super admins set it up under **Scripts → Remote control** (`/rmm/remote-control`). For each
provider:

1. Turn it on. RustDesk and MeshCentral both use MyPortal's **built-in script** unless you
   choose another. The built-in MeshCentral script downloads the self-installing MeshCentral
   agent scoped to a device group named after the device's company, so the device lands in its
   company's group (a device already in the generic Tactical RMM group is moved here too); the
   node ID is then looked up in that group by the device's host name. Without the RMM agent,
   MeshCentral falls back to the ID custom field below, opening MeshCentral signed in.
2. To use your own script, add it to the Gitea repository's `Common/` folder and choose
   **Sync from Gitea**, then choose it and save. Only `Common/` scripts are offered, as they
   run on every company's devices. The script's parameters then appear under **Script
   values**. Fill them in with typed values or MyPortal variables, such as
   `{{company.variables.RustDeskConfig}}`, so each company can use its own settings. Secret
   values are kept encrypted; leave them blank to keep the saved value.
3. Optionally name an **ID custom field**. MyPortal uses that asset custom field when the
   script does not report an ID.

MeshCentral is configured once, in the `.env` file (one MeshCentral server per deployment, like
RustDesk):

- `MESHCENTRAL_SERVER_URL`, the site address, such as `https://mesh.example.com`.
- `MESHCENTRAL_USERNAME`, the user that technicians sign in as.
- `MESHCENTRAL_LOGIN_TOKEN_KEY`, printed by `node node_modules/meshcentral --logintokenkey` on
  the MeshCentral server. When moving from Tactical RMM, the same user and key Tactical uses
  work here. The settings page shows whether each is set, but only the `.env` file holds the
  values.

The built-in script has the device download the agent straight from the MeshCentral server
(`…/meshagents?id=…&meshid=…`), so the server must **allow agent downloads**: if the
"Require login for agent downloads" setting is on (MeshCentral's `lockagentdownload`), the
device's anonymous download is rejected and installation fails. Leave it off, or the built-in
path is not usable; a custom activation script that authenticates first can still be used.

## Connecting

In a ticket's asset list, the **RustDesk** icon beside each asset starts a remote desktop
session straight away.

On an asset's **Scripts** card, the **RMM** menu holds RustDesk and MeshCentral **Remote
desktop**, **File transfer** and **Terminal** sessions (RustDesk terminal needs RustDesk 1.4.1 or
later on the technician's computer), **Run script**, and future RMM actions. In a ticket, the
**RMM** menu holds the MeshCentral sessions.

MyPortal shows the activation script's progress, then opens the session as soon as the device
reports back: RustDesk through a `rustdesk://` link carrying the ID and password (the browser may
ask to open RustDesk the first time), MeshCentral in a new tab already signed in on the device.
The built-in scripts install the tool on first use: the RustDesk script applies `RUSTDESK_CFG`
and sets a one-time password, and the MeshCentral script downloads and runs the agent into the
device's company group. An **Open … again** button stays in case the browser blocked it.

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
