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
