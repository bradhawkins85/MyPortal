# Generic RMM scripts

Scripts for deploying the MyPortal Tray Agent from any RMM (NinjaOne, Datto,
N-able, Atera, Syncro, Intune, ...). For Tactical RMM see `../tacticalrmm`.

## `install-tray-windows.ps1`

Installs or upgrades the tray agent on Windows and waits for it to enrol.
Add it once to your RMM's script library and set the values per company.

| Parameter | Environment variable | Required | Notes |
|---|---|---|---|
| `-PortalURL` | `MYPORTAL_URL` | yes | e.g. `https://portal.example.com` |
| `-EnrolToken` | `ENROL_TOKEN` | yes | The company's tray install token. Store it as a secret. |
| `-AutoUpdate` | `AUTO_UPDATE` | no | `true` (default) or `false` |
| `-MsiUrl` | `MYPORTAL_MSI_URL` | no | Defaults to `<PortalURL>/static/tray/myportal-tray.msi` |
| `-WaitSeconds` | `WAIT_SECONDS` | no | Enrolment wait, default `120`; `0` skips the check |
| `-Force` | | no | Reinstall even when already enrolled with this portal |

Run it as SYSTEM (or a local administrator):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File install-tray-windows.ps1 `
    -PortalURL 'https://portal.example.com' -EnrolToken '<company token>'
```

Re-running it is safe: a device already enrolled with the same portal is left
alone. Exit codes: `0` success, `1` bad parameters, `2` download failed,
`3` MSI failed (see `%TEMP%\myportal-tray-install.log`), `4` installed but not
enrolled in time, `5` not elevated.

## `install-tray-macos.sh`

Installs or upgrades the tray agent on macOS, checks the service and
menu-bar agent are loaded, and waits for the device to enrol. Run it as root
(the default for RMM scripts on macOS).

| Option | Environment variable | Required | Notes |
|---|---|---|---|
| `--portal-url` | `MYPORTAL_URL` | yes | e.g. `https://portal.example.com` |
| `--enrol-token` | `ENROL_TOKEN` | yes | The company's tray install token. Store it as a secret. |
| `--auto-update` | `AUTO_UPDATE` | no | `true` (default) or `false` |
| `--pkg-url` | `MYPORTAL_PKG_URL` | no | Defaults to `<portal-url>/static/tray/myportal-tray.pkg` |
| `--wait-seconds` | `WAIT_SECONDS` | no | Enrolment wait, default `120`; `0` skips the check |
| `--force` | `FORCE=true` | no | Reinstall even when already enrolled with this portal |

```bash
sudo bash install-tray-macos.sh --portal-url 'https://portal.example.com' --enrol-token '<company token>'
```

It does not need python3, so it works on a Mac without the developer tools.
Re-running it is safe. Exit codes match the Windows script: `0` success,
`1` bad parameters, `2` download failed, `3` pkg install failed (see
`/var/log/install.log` and `/Library/Logs/MyPortal/tray/postinstall.log`),
`4` installed but not enrolled in time, `5` not root. Output is also kept in
`/Library/Logs/MyPortal/tray/rmm-install.log`.
