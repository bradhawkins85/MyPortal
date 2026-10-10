# RMM Scripts

**Scripts** (left menu → Scripts, `/rmm/scripts`) runs PowerShell, Bash and zsh scripts on a
company's devices through the MyPortal RMM agent, and shows each run's exit code, output and
any values the script sent back.

Scripts are written and reviewed in a **Gitea** repository. MyPortal only reads them: it loads
each script, works out which values it needs, and gives technicians a field for each one when
they run it.

Scripts are pushed manually for now. Scheduled and triggered runs come later.

## Gitea

### The bundled Gitea

The bare-metal installer and the Docker installer both run a Gitea for you, so scripts work
straight after installing:

- Gitea is served at **`/gitea`** on the portal's own address, for example
  `https://portal.example.com/gitea/`. Choose **Open Gitea** on the Scripts page to go there.
- The installer creates a Gitea administrator called `myportal`, a private repository
  `myportal/rmm-scripts`, and a read-only token for MyPortal, and fills in the `GITEA_*` settings
  below. The administrator's password is in `/etc/gitea/admin-credentials` (bare metal) or
  `/opt/myportal-docker/gitea-admin.txt` (Docker). Sign in with it, then create accounts for your
  technicians under Site administration.
- Self-registration is off and every page needs a sign-in.
- Every upgrade checks Gitea: it is installed if missing, moved to the Gitea version the release
  ships with, and its address follows `PORTAL_URL`. Its data is kept, and a Gitea problem never
  stops a MyPortal upgrade (the next one retries).
- Backups include Gitea's data (`gitea-*.tar.gz`; see [Backups](Backups.md)).

On bare metal Gitea runs as the `gitea` service (`systemctl status gitea`) with its data in
`/var/lib/gitea`. On Docker it is the `gitea` container (`myportal-docker logs gitea`) with its
data in the `myportal_gitea_data` volume.

To use your own Gitea server instead, set `GITEA_BASE_URL` (and the other settings below) to it;
the installers then leave Gitea alone. Set `GITEA_PROVISION=false` to stop them running Gitea
without connecting another one.

### Connecting another Gitea server

1. Create a repository in Gitea for your scripts and a personal access token with read access
   to it.
2. Set these values in `.env` (the **Gitea scripts** module, `gitea`, is on by default under
   Administration → Modules):

   | Setting | Meaning |
   | --- | --- |
   | `GITEA_BASE_URL` | Address of your Gitea server, e.g. `https://git.example.com` |
   | `GITEA_PUBLIC_URL` | Address technicians open, when it differs from `GITEA_BASE_URL` (a path such as `/gitea` is relative to the portal) |
   | `GITEA_API_TOKEN` | Token with read access to the repository |
   | `GITEA_SCRIPTS_REPOSITORY` | `owner/name` of the repository |
   | `GITEA_SCRIPTS_BRANCH` | Branch to load (default `main`) |
   | `GITEA_SCRIPTS_PATH` | Optional folder inside the repository; leave blank for all of it |
   | `GITEA_VERIFY_SSL` | `false` only for a self-signed test server |

3. On the Scripts page, a super admin chooses **Sync from Gitea**.

Sync loads every `.ps1`, `.sh`, `.bash` and `.zsh` file (up to 1 MB). Folders become groups in
the library. Unchanged files are skipped, and a script deleted from Gitea disappears from the
library while its past runs keep the exact version that ran.

## Writing scripts

### Parameters

PowerShell scripts use a normal `param()` block. MyPortal reads each parameter's type,
default, `Mandatory`, `ValidateSet` choices, and its help from comment-based help
(`.PARAMETER`), `HelpMessage`, or a comment on the line above or beside it.

```powershell
<#
.SYNOPSIS
  Checks free disk space.
.PARAMETER Drive
  Drive letter to check.
#>
param(
    [Parameter(Mandatory)] [string] $Drive,
    [int] $MinFreeGB = 10,          # Warn below this
    [ValidateSet('Low', 'High')] [string] $Level = 'Low',
    [switch] $Force
)
```

Bash and zsh have no param block, so declare one with the same syntax in the comment header
at the top of the script. The agent passes the values as `--Name value` pairs in this order; a
switch that is on is passed as `--Name` alone, and list values are joined with commas.

```bash
#!/bin/bash
# Restarts a service and reports its status.
#
# param(
#   [Parameter(Mandatory)] [string] $Service,  # Service to restart
#   [int] $Wait = 5
# )
```

Field types: text, whole number, number, yes/no (`[bool]`), switch, choice (`ValidateSet`),
list (`[string[]]`) and secret (`[securestring]`, `[pscredential]`, or any name containing
password, secret, token or API key). Secrets are shown as a password box and masked in run
history.

### Environment variables

Environment variables are found automatically: `$env:NAME` in PowerShell, and `$NAME`,
`${NAME}` or `${NAME:-default}` in Bash and zsh (upper-case names only). Variables the script
sets itself before reading, and standard system variables such as `PATH` or `TEMP`, are not
offered.

### Returning values to MyPortal

A script can update **asset custom fields** (Administration → Asset custom fields) on the
device it ran on, and **company variables** (Edit company → Variables) of the device's company.
Either print lines like:

```powershell
Write-Output "##myportal[asset.BitLocker Status]=On"
Write-Output "##myportal[company.Tenant ID]=contoso"
```

or write JSON to the file named by the `MYPORTAL_RESULT_FILE` environment variable, as a list
or grouped by scope:

```json
[{"scope": "asset", "name": "BitLocker Status", "value": "On"}]
```

```json
{"asset": {"BitLocker Status": "On"}, "company": {"Tenant ID": "contoso"}}
```

Names match the field or variable name, ignoring case. Checkbox fields take `true`/`false`,
and date fields take a `YYYY-MM-DD` date. Values for names that don't exist, and image fields,
are skipped and listed on the run, so create the field or variable first.

The agent also sets `MYPORTAL_RUN_ID` for every run.

## Running a script

Choose **Run** next to a script, **Run a script** at the top of the page, or **Run script** on
an asset's page. Then:

1. Tick the devices to run it on. Only devices with the RMM agent are listed.
2. Fill in each field with a value, or pick **Use a variable…** to insert a MyPortal variable
   such as `{{asset.custom.BitLocker}}`, `{{company.variables.Tenant ID}}`, `{{asset.name}}` or
   `{{company.name}}`. Variables are filled in separately for each device, so one run can give
   every device its own value. Leaving an optional field empty uses the script's default.
3. Set how long the script may run before the device stops it, then run it.

The panel on the right shows what each device receives. Values are encrypted until the device
collects them and are deleted when the run finishes.

A run is **Waiting for device** until the device collects it, then **Sent**, **Running**, and
finally **Completed** (exit code 0), **Failed**, or **Timed out**. A run the device has not
collected within 24 hours **expires**, and one that is still waiting can be cancelled from its
details. Choose **View** on a run to see its output, errors, the values sent, and which custom
values were updated.

## Who can use it

The **Scripts** permission (RMM group in Roles) controls access per company. Read access shows
the library and runs; write access also runs and cancels scripts. Only super admins sync from
Gitea. Every sync, run and cancel is recorded in the audit trail.

## The RMM agent

The RMM agent (`myportal-rmm`) is a separate program from the tray app, built for Windows,
macOS and Linux by the **Build RMM agent** workflow. It enrols with the tray device's token so
it joins the same company and asset, then waits for runs:

```text
MYPORTAL_TRAY_TOKEN=<tray device token> myportal-rmm enrol --url https://portal.example.com
myportal-rmm install
```

It runs scripts as the service account (SYSTEM on Windows, root on macOS and Linux). On
Windows it uses Windows PowerShell; on macOS and Linux it runs PowerShell scripts only when
`pwsh` is installed. Shell scripts do not run on Windows.
