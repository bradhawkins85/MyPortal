# RMM Scripts

**Scripts** (left menu → Scripts, `/rmm/scripts`) runs PowerShell, Bash and zsh scripts on a
company's devices through the MyPortal RMM agent, and shows each run's exit code, output and
any values the script sent back.

Scripts are written and reviewed in a **Gitea** repository. MyPortal loads each script, works
out which values it needs, and gives technicians a field for each one when they run it. The only
thing MyPortal writes to the repository is its folder layout (see
[Repository layout](#repository-layout)).

Scripts can be pushed manually, run on a schedule, or run automatically when the RMM agent is
first installed on a device (see [Schedules and onboarding](#schedules-and-onboarding)). They
also switch on RustDesk and MeshCentral for [Remote Control](Remote%20Control.md). Windows
devices also send their event logs to MyPortal (see [RMM Event Logs](RMM%20Event%20Logs.md)),
every device sends its hardware and installed software (see [RMM Inventory](RMM%20Inventory.md)), and
[RMM Checks](RMM%20Checks.md) watch disk space, CPU, memory, services and more.
[Patch management](RMM%20Patch%20Management.md) configures scoped Windows Update,
Chocolatey application updates and reboot policies.

## Gitea

### The bundled Gitea

The bare-metal installer and the Docker installer both run a Gitea for you, so scripts work
straight after installing:

- Gitea is served at **`/gitea`** on the portal's own address, for example
  `https://portal.example.com/gitea/`. Choose **Open Gitea** on the Scripts page to go there.
- The installer creates a Gitea administrator called `myportal`, a private repository
  `myportal/rmm-scripts`, and a token for MyPortal that can write to repositories (so MyPortal can
  create its folders), and fills in the `GITEA_*` settings
  below. The administrator's password is in `/etc/gitea/admin-credentials` (bare metal) or
  `/opt/myportal-docker/gitea-admin.txt` (Docker). Technicians don't need it: they sign in with
  MyPortal (see [Signing in to Gitea](#signing-in-to-gitea)).
- Self-registration is off. Public repositories can be read without signing in; the script and `Private-Files` repositories stay private.
- Repository uploads accept all file types up to **1 GB (1024 MiB) per file**, including
  installers and other supporting files. This limit is applied on install and upgrade for
  both bare-metal and Docker deployments. An external Gitea server needs its own upload
  and reverse proxy limits configured separately.
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

### Public Files and Private Files

Besides the script repository, the installers keep two file repositories under the same owner:

- **`myportal/Public-Files`** is public. All staff and users can open **Downloads** in the
  menu (`/downloads`), and anyone with the repository link can browse and download files
  without signing in. Technicians with **Script editing → Write**, including super
  administrators, can upload files using Gitea's **Add file → Upload file** action. Share a
  file's **Raw** link for a direct download.
- **`myportal/Private-Files`** is private, with a `Common/` folder and a folder per company
  under `Companies/` (see [Repository layout](#repository-layout)). It has no public link:
  its files reach endpoints only when a script lists them with **Add file** (see
  [Associated files](#associated-files)). Technicians get the same read or write access to it
  as to the script repository, so they can upload files there.

Installations made before this change had a public repository named `Public`; the next install
or upgrade renames it to `Public-Files`, and Gitea redirects old links to the new name.
MyPortal also moves an existing `Files/` folder from the script repository into
`Private-Files` (files over 50 MB stay behind and are logged so you can move them by hand) and
updates script file associations to the new paths. Runs queued before the move must be queued
again.

Changes to technician roles update access to both repositories during the usual account
reconciliation, and existing technicians receive access on their next visit or background
check. For an external Gitea server, create a public repository named `Public-Files` (allow
anonymous repository viewing) and a private repository named `Private-Files`, both with a
`main` branch under the configured script repository owner; the installers leave external
servers alone.

### Signing in to Gitea

Technicians open the bundled Gitea already signed in with their MyPortal account; there is no
separate Gitea password. Who can sign in is set by the **Script editing** permission (RMM group)
in Administration → Roles:

| Script editing | In Gitea |
| --- | --- |
| No access | The Gitea sign-in page; **Open Gitea** is hidden on the Scripts page |
| Read | Can view and download the script repository |
| Write | Can change scripts, create branches and merge pull requests |

Super administrators always have write access. The permission covers the whole repository,
including every company folder, and any one of a technician's company roles is enough.

- The first visit creates the technician's Gitea account, named after the start of their email
  address and their MyPortal user id (for example `jsmith-12`), and adds it to the script
  repository.
- Gitea keeps no session of its own for these accounts. Signing out of MyPortal, or losing the
  permission, signs the technician out of Gitea on their next click.
- Every ten minutes MyPortal also lowers or removes the account's repository access to match the
  technician's roles, which stops any access token they created in Gitea from reaching the
  scripts.
- The `myportal` administrator still signs in with its password.

This works through the proxy that serves `/gitea`: it asks MyPortal who is signed in and passes
that to Gitea, which accepts it only from the proxy. It needs the bundled Gitea; a Gitea server
you connect yourself keeps its own accounts.

### Connecting another Gitea server

1. Create a repository in Gitea for your scripts and a personal access token with the
   `write:repository` scope. A read-only token still loads scripts, but MyPortal cannot create
   its folders and sync shows a warning.
2. Set these values in `.env` (the **Gitea scripts** module, `gitea`, is on by default under
   Administration → Modules):

   | Setting | Meaning |
   | --- | --- |
   | `GITEA_BASE_URL` | Address of your Gitea server, e.g. `https://git.example.com` |
   | `GITEA_PUBLIC_URL` | Address technicians open, when it differs from `GITEA_BASE_URL` (a path such as `/gitea` is relative to the portal) |
   | `GITEA_API_TOKEN` | Token with `write:repository` access to the repository |
   | `GITEA_SCRIPTS_REPOSITORY` | `owner/name` of the repository |
   | `GITEA_SCRIPTS_BRANCH` | Branch to load (default `main`) |
   | `GITEA_SCRIPTS_PATH` | Optional folder inside the repository that holds `Common` and `Companies`; leave blank for the repository root |
   | `GITEA_VERIFY_SSL` | `false` only for a self-signed test server |

3. On the Scripts page, a super admin chooses **Sync from Gitea**.

Sync loads every `.ps1`, `.sh`, `.bash` and `.zsh` file (up to 1 MB) in the folders below.
Subfolders become groups in the library. Unchanged files are skipped, and a script deleted from
Gitea disappears from the library while its past runs keep the exact version that ran.

### Repository layout

MyPortal keeps these folders at the top of the repository (or of `GITEA_SCRIPTS_PATH`):

- **`Common/`**: scripts any company can run. Organise them in subfolders as you like.
- **`Companies/`**: one folder per MyPortal company, for example `Companies/Contoso Ltd/`.
  Scripts in a company's folder, and its subfolders, are offered and run only on that company's
  devices.

The **`Private-Files`** repository has the same layout for the files scripts deploy:

- **`Common/`**: files shared across companies, such as installers, executables, documents
  and supporting scripts.
- **`Companies/`**: one folder per MyPortal company for its stored files, for example
  `Companies/Contoso Ltd/`. These folders have their own `.myportal-company` markers, so
  they can be renamed independently of the script folders.

MyPortal creates these folders itself, along with a folder for every company, on each sync and
every ten minutes, so a new company gets its folder without anyone touching Gitea. Archived
companies do not get a new folder. Git cannot store an empty folder, so each company folder
holds a small `.myportal-company` file that records which company it belongs to; leave it in
place. Because of that file you can rename a company folder in Gitea and it stays linked. A
folder you make yourself under `Companies/` is linked to the company with the same name.
The same folder maintenance applies to `Private-Files`.

Upload and download stored files directly in Gitea. The bundled Gitea allows all repository
upload types, including `.exe` and `.vbs`, on both bare-metal and Docker installations.
This uses `[repository.upload]` settings `ENABLED = true` and `ALLOWED_TYPES = */*`, applied
on install and upgrade. Gitea applies these settings to the whole instance; file size and
upload count limits still apply (by default 50 MB per file and five files per upload).
For an externally managed Gitea server, configure these settings in its `app.ini` and restart
Gitea yourself.

Files in `Private-Files` are not imported into MyPortal's runnable script library, even when
they have a supported script extension. The existing **Script editing** permission controls
access to the whole repository, including all stored company files; company folders organise
files and do not restrict who can view them in Gitea.

Scripts anywhere else in the repository, and scripts in a `Companies/` folder that matches no
company, are not loaded; sync lists them as skipped.

Installations set up before this layout was added gave MyPortal a read-only token. With the
bundled Gitea, the next install or upgrade replaces that token with one that has the
`write:repository` scope. With another Gitea server, Sync and the file editor say the token
needs write access: create a new token with the `write:repository` scope (Gitea → Settings →
Applications) and put it in `GITEA_API_TOKEN`. Then move existing scripts into `Common/` or a
company folder.

## Writing scripts

### Associated files

Open a script in **Scripts**, then use its **Files** section to add a `Private-Files` file and
an absolute endpoint file path, including the filename. The editor lists the files this script
can use, and creates any missing `Private-Files` folders first. **Script editing → Write** permission
is required to add, edit or remove these associations. They are stored in MyPortal and kept
when the script is synced again.

For example, associate `Common/setup.exe` with `C:\Tools\setup.exe`, then have the
PowerShell script use `C:\Tools\setup.exe`. A shell script might associate
`Common/helper.sh` with `/opt/myportal/helper.sh`. Paths are relative to the top of
`Private-Files`; `GITEA_SCRIPTS_PATH` applies only to the script repository.

- Common scripts can use files under `Common/`. Company scripts can also use files under
  their own company's folder in `Companies/`, including folders linked by a
  `.myportal-company` marker. Files from another company cannot be associated or delivered.
- Common scripts can also use a company's files through `Companies/{company}/`, for
  example `Companies/{company}/setup.exe`. The file picker lists the files of the
  company you are viewing in this form. Each run uses `{company}` as the folder of the company
  it runs for, so every company that runs the script needs its own copy of the file; a run
  for a company without it fails before the script starts.
- Each script may associate up to 20 files, up to 50 MB each. Every destination must be a
  different absolute local path for the endpoint's operating system. Windows paths must
  include the drive letter. UNC paths, relative paths and `..` are rejected.
- When a run is queued, MyPortal reads each Gitea file and calculates its MD5, SHA-256 and
  size. The run keeps that exact Git file version, so a later Gitea update does not change an
  already queued run's files.
- If Gitea omits a file's content because of its blob API size threshold, MyPortal
  downloads the raw file at the queued commit and verifies its Git hash. No Gitea
  size-setting change is needed for files within the 50 MB limit. Runs queued before
  commit pinning was added must be queued again if Gitea omits their file content.
- Before starting the script, the RMM agent compares the file already at each destination
  with the queued checksums and size. Matching files are reused without a download.
  Missing or different files are downloaded through MyPortal with the agent's token;
  endpoints never receive Gitea credentials.
- Downloads go to a temporary file beside the destination. The agent checks the downloaded
  size, MD5 and SHA-256 on disk before replacing the destination, and checks the final file
  again. An interrupted or corrupt download leaves the previous file intact and prevents
  the script from starting. Parent folders are created automatically. Existing file
  permissions are preserved; new files use owner-only read, write and execute permissions
  on Unix. Prepared files remain at their destinations after the script ends.
- All required files must be ready before the script runs. Preparation is included in the
  run timeout. File-bearing runs execute one at a time on an endpoint to prevent concurrent
  runs from replacing one another's dependencies. Run output records whether each file was
  reused or downloaded and verified.

Install the updated RMM agent on endpoints before using associated files. Agents advertise
support at enrolment and check-in; MyPortal refuses file-bearing runs for older agents.
Manual, scheduled, onboarding, tray, event log and check-triggered runs all use this flow.

The file APIs are:

| Method and path | Purpose |
| --- | --- |
| `GET /api/rmm/scripts/{id}/files` | List saved associations and available `Private-Files` paths; requires Script editing write access |
| `PUT /api/rmm/scripts/{id}/files` | Replace the list with `{"files": [{"source_path": "Common/setup.exe", "destination": "C:\\Tools\\setup.exe"}]}`; an empty list removes all associations |
| `GET /api/rmm/agent/runs/{id}/files/{index}` | Download the pinned file at the zero-based index; requires the assigned agent's bearer token and a non-expired dispatched or running job |

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

Remote control activation scripts also print `session` values, such as a RustDesk ID; see
[Remote Control](Remote%20Control.md).

The agent also sets `MYPORTAL_RUN_ID` for every run.

## Browsing scripts

The Scripts page is laid out like **Edit company**. The list on the left shows the Gitea folders
as a tree (for example **Common** › **disk**), each with the number of scripts inside. Folders
start collapsed, apart from the ones holding the open script; select a folder to expand or
collapse it, or use **Expand all** and **Collapse all**. Typing in **Find a script** opens the
folders with matching scripts and hides the rest; clearing it (or pressing Esc) puts the folders
back as they were. On wide screens the list stays in place while the script details scroll. **Overview** shows the totals, recent
runs and the devices with the RMM agent. Choose a script to see, on the right:

- **Description**, from the script's comment-based help (`.SYNOPSIS`) or leading comment, with
  its default timeout and when it was last synced.
- **AI summary**: what the script does on the device, what it leaves behind and anything to
  check before running it. It is written with the Ollama module the first time a technician
  with write access opens the script, and again once the script changes in Gitea. Choose
  **Regenerate** to write a new one. Without the Ollama module the card says so.
- **Inputs**: the parameters and environment variables it asks for, their types, defaults
  (sensitive defaults are hidden) and help text.
- **Recent runs** of that script on the company's devices.
- **Script**: the source devices receive, as loaded at the last sync, with **Copy** and, for
  technicians who can sign in to Gitea, **Edit in Gitea**.

## Timeouts and platforms

Each script has its own **Offline wait timeout** on `/rmm/scripts`. Super
administrators can choose at least one minute, or **0 for Forever** (the default).
This controls how long a queued script waits for an offline agent to collect it:
300 minutes allows five hours; 2880 minutes allows two days. Once that window
expires, the script will not run when the device returns.

Technicians can override **Offline wait timeout** when running a script or saving
a schedule or onboarding step. Leave it blank to inherit the script's current
setting; enter 0 to wait forever even if the script has a finite default. These
settings survive Gitea sync. Existing queued runs retain the wait selected when
queued. A schedule does not queue another copy while its previous run is pending.

**Execution timeout** is separate and keeps its existing behavior. It starts when
the agent collects the script, regardless of how long the script waited offline.
For example, a script can wait two days and still have a ten-minute execution
limit. MyPortal allows five extra minutes for the agent to report the result.
Forever applies only to the offline wait and needs no agent update.

Open each script to set its **Allowed platforms**: Windows, Mac, Linux or BSD.
PowerShell scripts default to Windows; Bash and zsh scripts default to Mac and Linux.
These settings survive Gitea sync. Clear all platforms to prevent execution everywhere.
Device script pickers show only compatible scripts, and script device selectors and
scheduled targets exclude incompatible platforms. Onboarding skips incompatible steps
and continues. The server checks platform permissions again before dispatching queued
runs. A compatible shell must still be installed, including PowerShell (`pwsh`) on
Mac/Linux if you explicitly enable a PowerShell script there.

## Running a script

Scripts default to **Sequential** execution on each device. A super administrator
can open a script, choose its **Execution mode**, and select **Save execution setting**:

- **Sequential** waits for active sequential and parallel scripts to finish, runs
  alone, and keeps later ordinary scripts queued. Use this for tasks such as chkdsk
  and defrag that must not overlap.
- **Parallel** shares up to four ordinary agent slots with other parallel scripts
  and waits behind earlier sequential scripts.
- **Run Anyway** bypasses the queue and starts regardless of other active scripts,
  even when all four ordinary slots are occupied. Other scripts do not wait for it.
  Use this for scripts that have no impact on other tasks or urgent actions such as
  rebooting. A reboot may interrupt other running scripts.

Each device has its own queue. These modes apply to manual and scheduled runs;
onboarding always runs sequentially, overriding the script's mode. Settings apply
to newly queued runs and survive Gitea sync. Existing scripts remain sequential
unless an administrator changes their mode.

Run Anyway requires migration 495 and an updated RMM agent. Deploy MyPortal first,
then update agents. An older agent fails a Run Anyway run with an update message.
Sequential and parallel scripts continue to work on older agents. Run Anyway still
verifies script checksums and required files and applies the configured timeout;
it bypasses the ordinary file-preparation lock, so choose it only when associated
file destinations can also be used independently.

Choose **Run** on a script, **Run a script** at the top of the page, or **Run script** on
an asset's page. Then:

1. Tick the devices to run it on. Assets without an agent are also listed, labelled **Waiting for RMM agent**.
2. Fill in each field with a value, or pick **Use a variable…** to insert a MyPortal variable
   such as `{{asset.custom.BitLocker}}`, `{{company.variables.Tenant ID}}`, `{{asset.name}}` or
   `{{company.name}}`. Variables are filled in separately for each device, so one run can give
   every device its own value. Leaving an optional field empty uses the script's default.
3. Set how long the script may run before the device stops it.
4. Choose who it runs as under **Run as**, then run it.

### Queue before the agent is installed

Technicians can queue scripts from an asset's **Scripts** card or the script library
before the RMM agent is installed, including while the asset is offline. The run
appears in history as **Queued** and can be cancelled there. Installing the agent
is a separate action; queueing a script does not install it.

When an active agent for the same asset and company connects and polls for work,
it collects the pending runs through the normal device queue. The script content,
entered values and resolved variables are captured when queued. Platform, shell,
associated file paths and agent capabilities are checked before execution; an
incompatible script is not sent to the device.

The **Offline wait timeout** starts when the script is queued, including time
waiting for installation. Choose **Forever** (0) to wait indefinitely, or set a
limit so the run expires if the agent does not collect it in time. The execution
timeout starts separately when the agent collects the script.

### Run as System or the logged in user

Every manual run, schedule and onboarding step has a **Run as** setting:

- **System** (the default) runs the script as the agent's own account: LocalSystem on
  Windows, root on macOS and Linux. Use it for installs, services, machine-wide settings
  and anything that needs administrator rights.
- **Logged in user** runs the script as whoever is signed in to the device, with their
  rights, profile, `HKEY_CURRENT_USER`, mapped drives and environment. On Windows the
  agent uses the user at the console, or the first active Remote Desktop session when
  nobody is at the console, and starts the script hidden on that user's desktop. On macOS
  it uses the owner of the console; on Linux, the active graphical session on `seat0`.
  If nobody is signed in, the run fails with *No user is logged in on this device* rather
  than running as System. Associated files are still downloaded by the agent's own
  account before the script starts, so put them where the user can read them.

The choice is saved with schedules and onboarding steps, captured on each queued run,
shown in the run's details, and kept when you **Retry** a run. Event log rules, the tray
menu, remote control, patching and Windows Update jobs always run as System.

Running as the logged-in user needs migration 508 and an updated RMM agent: agents report
`run_as_user_supported: true` on check-in. Deploy MyPortal first, then update agents. An
older agent fails a **Logged in user** run with an update message instead of running it as
System. System runs work on every agent.

The panel on the right shows what each device receives. Values are encrypted until the device
collects them and are deleted when the run finishes.

A run is **Waiting for device** until the device collects it, then **Sent**, **Running**, and
finally **Completed** (exit code 0), **Failed**, or **Timed out**. A run the device has not
collected within 24 hours **expires**. Choose **Cancel** in the asset's Scripts list (or in a
run's details) while it is still **Waiting for device**. Once the agent has picked it up,
including **Sent** runs that have not started executing yet, it cannot be cancelled.
Choose **View** on a run to see its output, errors, the values sent, and which custom values
were updated.

**Retry** is available for **Expired**, **Timed out**, and **Failed** runs. It opens the run
editor with the original device, timeout, run as setting and saved inputs selected. Review the values and
re-enter any secret values typed directly into the previous run; variable references are
kept and resolved again. Retrying uses the current script and creates a new manual attempt,
keeping the original result in history. Run and cancel actions require Scripts write access.

### Execute a command

To run something that is not a saved script, open the device's asset page and choose
**RMM** > **Execute command**. Pick the shell (**PowerShell** or **Command Prompt** on
Windows; **Bash**, **Zsh**, or **PowerShell** where `pwsh` is installed on macOS and
Linux), type the command, and choose **Run as** **System** or **Logged in user** as for
a script. Several lines run as one script, and the exit code of the last command is the
run's exit code. The run opens in the result viewer with live output, appears in the
device's script history as **Command**, and shows the command under **Values sent**.

Commands need the same Scripts write access as running a script, and every command is
recorded in the audit log (`rmm.command.run`) with the shell, run as setting and the
command text. Command Prompt needs an updated RMM agent, which reports `cmd` among its
shells; an older agent refuses with an update message. Commands have no parameters or
associated files and cannot be retried; run them again instead.

## Schedules and onboarding

Choose **Schedules and onboarding** on the Scripts page (`/rmm/automation`). Both use the same
editor as a manual run: pick a script and fill in its values, typing them or using MyPortal
variables. The values are saved encrypted, and variables are filled in for each device every
time the script runs, so a schedule always sends a device its current custom field values.
Secret values show as `••••••` when you edit them again; leave them like that to keep the saved
secret.

### Schedules

RMM schedules are separate from MyPortal's scheduled tasks. Each schedule has:

- **Devices**: every device with the RMM agent, devices with any of the chosen
  [tags](Tags.md) (a tag on a company counts for all of its devices), devices you pick,
  or **Devices that match rules** using the same fields and operators as [Checks](RMM%20Checks.md#advanced-device-rules).
  Devices added later are included automatically, except when you pick devices.
- **When**: every hour, every day, every Monday, the 1st of the month, or your own cron
  expression (`minute hour day month weekday`), in the time zone you choose (`CRON_TIMEZONE`
  by default). The editor shows the next three times it will run.

MyPortal checks for due schedules every 30 seconds. A device that is still working on (or has
not yet collected) the previous run from the same schedule is skipped rather than given a
second one. Offline devices wait for the script’s offline wait timeout or its schedule override (indefinitely with Forever). If
MyPortal was down when a schedule was due, it runs once when MyPortal is back, not once per
missed time. **Run now** runs a schedule straight away, **Turn off** pauses it, and the
**Last run** column shows how many devices it reached and why any were skipped. Runs started
by a schedule show the schedule's name in **Started by** in the run history.

### Onboarding scripts

Onboarding scripts run once, after a device's RMM agent enrols with MyPortal and sends
its first hardware and software inventory snapshot. A reinstall that keeps the agent's
identity does not run them again. Each company has its own ordered list of steps, and the steps run one at a time in that order: the next step is sent
only after the previous one reports back.

Each step can run on **every device**, devices with **any of the chosen tags**, or
**devices that match rules**. Existing steps keep their tag filters. A tag on the company
counts for all of its devices. Devices outside the selection skip the step.
Step targeting is evaluated after that first snapshot has been saved. Pending onboarding
survives service restarts.
Automatic tags are refreshed before tag matching; device rules use current inventory
just before each step is queued.

If a step fails, times out, expires or is cancelled, the steps after it are not run and the
onboarding is marked **Stopped**, unless that step is set to **Carry on with the next step if
this one fails**. A step whose script has been removed from Gitea fails the same way, and a
step deleted while a device is part-way through is skipped.

Tick **Skip existing assets** on a step to run it only on devices enrolled after the option
was turned on. Devices that were already enrolled, for example existing devices getting the
RMM agent for the first time, skip the step, so setup scripts meant for new machines never
run on devices you already manage. A device counts from when it first enrolled with the tray
app (or, without one, when its RMM agent enrolled). Editing the step keeps the original
cut-off; turning the option off runs the step on every device again. Apply migration **512**
first.

**Add Chocolatey packages** adds a step that installs apps instead of running a script:
tick apps from the downloaded Chocolatey app list (or enter package IDs, up to 20) and
choose every device or devices with any of the chosen tags. The step runs the built-in patch
job with the company's enabled Chocolatey sources, updates apps that are already installed,
never restarts the device and shows in Scripts run history. Only Windows devices run it;
others skip it. Apply migration **506** first. See
[RMM Patch Management](RMM%20Patch%20Management.md) for how packages are installed.

**Onboarding history** shows each device's progress; choose **View** to see every step and
open its run. **Run onboarding** runs the current steps on a device again, for example after
adding a step, and **Stop onboarding** cancels the rest of a running sequence.

Schedules for every company can only use scripts in `Common/` and are added and changed only
by super admins; everyone else sees them read-only.

## Acting on a script's result

**Script result rules** (Scripts → **Script result rules**, `/rmm/scripts/result-rules`) read
what a script reports when it finishes on a device, and then raise a ticket or run another
script. Open a script and use its **When it finishes** section to see the rules that watch it
or to add one.

Each rule has:

- **When this script finishes**: one script, or **Any script**. Rules apply to every run of the
  script however it started: manually, on a schedule, during onboarding, from an event log rule,
  check, ticket automation, the tray menu or another result rule. MyPortal's own jobs (patching,
  Windows Update scans, remote control, tray repair and **Execute command**) are never watched.
  Runs that expire or are cancelled never report a result, so they do not trigger rules.
- **Applies to**: runs on this company's devices, or (super admins only) every company. Rules
  for every company can only use scripts in `Common/`.
- **Conditions**, matching **all** or **any** of them:

  | Check | Comparisons |
  | --- | --- |
  | Output, Error output, Output or error output | contains, does not contain, is, is not, starts with, ends with, is one of, is not one of, matches the regex, does not match the regex, is less than, is at most, is greater than, is at least, is empty, is not empty |
  | Exit code | is, is not, is one of, is not one of, is less than, is at most, is greater than, is at least |
  | Result | is, is not, is one of, is not one of: Completed, Failed or Timed out |
  | Returned value | as for Output, for a value the script returned (see [Returning values to MyPortal](#returning-values-to-myportal)); enter its name. Values count even when no custom field or variable has that name |

  Text comparisons ignore case. Number comparisons read the whole text as a number, or else the
  first number in it, so `Free: 12.5 GB` is 12.5. Regular expressions ignore case, `^` and `$`
  match at each line, and they read the first 200,000 characters. A pattern that takes longer
  than half a second gives up and does not match, so a slow pattern cannot hold up MyPortal.
  A timed-out run has no exit code, so only "is not" style exit code checks match it.
- **Then**:
  - **Create a ticket** for the device's company with the device linked. The description
    includes the script, device, result, exit code, output, error output and returned values
    (each output up to 20,000 characters). Choose the priority and, optionally, a subject using
    variables such as `{{asset.name}}`, `{{result.exit_code}}` or `{{result.groups.1}}`.
    **Update the open ticket for the device** (the default) adds later matches as internal
    updates while the ticket is not closed; **Raise a new ticket every time** does what it says.
  - **Run another script** on the same device as an ordinary run, shown as **Script result
    rule** in run history. Save the rule once to see the script's fields, then fill them in with
    values or variables:

    | Variable | Value |
    | --- | --- |
    | `{{result.output}}`, `{{result.errors}}` | The output and error output (first 8,000 characters) |
    | `{{result.exit_code}}`, `{{result.status}}` | The exit code and result (Completed, Failed or Timed out) |
    | `{{result.script}}`, `{{result.run_id}}` | The script that finished and its run |
    | `{{result.values.Name}}` | A value the script returned, such as `{{result.values.BitLocker Status}}` |
    | `{{result.match}}`, `{{result.groups.1}}` | What the first matching regex condition found, and its groups (named groups work too: `{{result.groups.drive}}`) |

    The asset and company variables work as usual. Tick **Pass the result to the script as
    environment variables** to also give the script `MYPORTAL_PREVIOUS_OUTPUT`,
    `MYPORTAL_PREVIOUS_ERRORS`, `MYPORTAL_PREVIOUS_EXIT_CODE`, `MYPORTAL_PREVIOUS_STATUS`,
    `MYPORTAL_PREVIOUS_SCRIPT`, `MYPORTAL_PREVIOUS_RUN_ID` and `MYPORTAL_PREVIOUS_VALUES` (the
    returned values as JSON). These are sent encrypted like other values and need no agent
    update.

    The follow-up run records the run and rule that started it. A chain of result rules stops
    after five scripts in a row, and the rule's last result then says it was not run, so rules
    cannot keep running each other.

For example, to clean up a disk when a check script reports less than 10 GB free, watch
`Check-Disk` with **Output is less than 10**, and run `Cleanup-Disk` with its `Drive` field set
to `{{result.values.Drive}}`. To raise a ticket whenever any script fails, watch **Any script**
with **Exit code is not 0**.

Each rule's list entry shows how often it matched and what it last did. Read access shows the
rules; write access adds, changes, turns off and deletes the company's rules. Every change is
recorded in the audit trail (`rmm.script_result_rule.*`). Apply migration **518** first.

## Running scripts from ticket automations

The **My Portal RMM Script** ticket automation action runs a script on the devices linked to a
ticket, so an automation can try to fix a problem before a technician touches the machine. For
example, a *Ticket created* automation filtered to low disk space tickets (such as those raised by
a [disk space check](RMM%20Checks.md) or an [event log rule](RMM%20Event%20Logs.md)) can run your
disk clean-up script and move the ticket to **Resolved** if it works.

Add the action to an automation (**Automation → Ticket automations**) with a payload such as:

```json
{
  "ticket_id": "{{ ticket.id }}",
  "script": "Cleanup-Disk",
  "values": {"Drive": "C"},
  "success_status": "resolved"
}
```

| Field | What it does |
| --- | --- |
| `script` | The script to run: its Gitea path (`rmm/Common/Cleanup-Disk.ps1`), its name (`Cleanup-Disk`) or its ID. A script in the company's own folder wins over one in `Common/` with the same name. The form lists every active script. |
| `asset_id` | Run only on this linked asset. Without it the script runs on every linked device with the RMM agent (up to 20). |
| `values` | The script's parameters and environment variables, keyed by name (`Drive`) or as in the run form (`param:Drive`, `env:SITE_URL`). Values may use `{{ticket.*}}`, `{{asset.*}}` and `{{company.variables.*}}`; they are filled in for each device like a schedule's. A blank field whose script default is a MyPortal variable is filled from that default. |
| `timeout_seconds` | Defaults to the script's own timeout. |
| `run_as` | `system` (default) or `user`, the user logged in to the device. |
| `success_status` | Ticket status to set once the script completes successfully (exit code 0) on every device it was queued on. Leave it out to leave the status alone. |

The script is queued as an ordinary RMM run and shows as **Ticket automation** in run history.
When the device reports back, the outcome, exit code and the end of the output are added to the
ticket as an internal note. A run that expires before the device collects it (24 hours), never
reports back or is cancelled gets a note too. Notes and status changes made this way do not fire
further ticket automations.

The action is **skipped** when the ticket has no linked asset, no linked device has the RMM agent,
the same script from a ticket automation is still queued or running on the device, or the script
cannot run on the device's platform. It **fails** when the script cannot be found for the
ticket's company, a value does not match a script field, a mandatory value is missing, or
`success_status` is not a ticket status. The action is hidden when the RMM feature is disabled.
Apply migration **509** first.

## Who can use it

The **Scripts** permission (RMM group in Roles) controls access per company. Read access shows
the library, runs, schedules and onboarding; write access also runs and cancels scripts and
manages schedules and onboarding steps for the company. Only super admins sync from Gitea and
manage every-company schedules. Generating an AI summary needs write access. Every sync, run,
cancel, AI summary and change to a schedule or onboarding step is recorded in the audit trail.

## The RMM agent

The RMM agent (`myportal-rmm`) is a separate program from the tray app, built for Windows,
macOS and Linux by the **Build RMM agent** workflow. It enrols with the tray device's token so
it joins the same company and asset, then waits for runs.

### Removing PawnIO

RMM agents released before the PawnIO fix installed the PawnIO driver, which caused
`SYSTEM_THREAD_EXCEPTION_NOT_HANDLED` (0x7E) crashes. Updated agents remove it at startup. To
remove it sooner, add `docs/examples/rmm/Remove-PawnIO.ps1` from the MyPortal repository to
`Common/` in Gitea, sync, and run it as System on the affected Windows devices. It runs
PawnIO's registered uninstaller silently and fails (exit code 1) if PawnIO is still registered
afterwards. It also removes PawnIO that other software installed. An agent released before
the fix reinstalls PawnIO when its service next starts, so update the agent as well.

The tray app's [Health Check](Health%20Check.md) may install PawnIO on a physical computer for
the few minutes it takes to read hardware sensors, and removes it before it reports back. If
the RMM agent starts during that time it removes PawnIO early and the check reports fewer
sensors.

### Repairing an asset's agent

Choose **Repair RMM agent** from an asset's Actions menu or its Scripts card.
The action works even when MyPortal still has an agent record for a machine whose
agent has been removed. It also installs agents that have never been installed.
An active MyPortal tray registration on Windows or macOS and RMM Scripts write
access are required. Offline devices collect the queued repair when their tray
app reconnects. Repair attempts appear in the asset's install history and audit trail.

The tray downloads the RMM release matching its own version and checks its SHA-256
before replacing anything. It reinstalls the service, refreshes enrolment using the
tray's token, and checks that the service is running and an authenticated check-in
reaches MyPortal. A failed repair reports the error; when an old binary exists,
the installer restores it and attempts to restart its service.

Tray restarts, including those after updates, check the RMM version immediately
and retry every 30 minutes. Previously enrolled agents whose binary has disappeared
are restored from the matching release. Devices without saved RMM enrolment remain
subject to the company's automatic deployment setting or a manual push.

Deploy the portal change together with tray and RMM builds from the same release.
The new tray build provides refreshed enrolment and verification; the matching RMM
build provides the `verify` command. Older tray builds perform their existing install
behavior until updated. Publish matching RMM assets and checksums alongside the tray
installers; unavailable or mismatched releases are rejected and retried.

### Pushing the agent from MyPortal

The tray app installs the agent on Windows and macOS devices, so a device needs the tray app
first:

- **One device**: on **Assets**, open the device's **Actions** menu, or its page's **Scripts**
  card, and choose **Repair RMM agent** (write access to RMM scripts). The asset page shows
  whether the install is queued, running or failed, and why.
- **A whole company**: super admins tick **Install the RMM agent automatically** on the
  company's tray settings (**Edit company > Tray settings**). MyPortal then pushes the agent to
  every device of the company without it, and to each new device when its tray app connects. A
  failed install is retried once a day.

Only tray app 0.8.4 and later can install the agent. When a device runs an older tray app,
MyPortal holds the install and tells the tray app to update itself to the newest version
published under **Admin > Tray > Versions**; the install goes out once the updated tray app
reconnects. If no new enough tray version is published there, the push is refused with that
reason, so publish one first. A push reaches a connected device straight away; an offline device gets it when it next
connects. The tray app downloads the agent build from MyPortal, checks its SHA-256, puts it in
`%ProgramFiles%\MyPortal RMM` (Windows) or `/Library/Application Support/MyPortal RMM` (macOS),
enrols it and installs its service. MyPortal fetches the builds from the newest GitHub release
that has them (`GITHUB_TRAY_MSI_REPO`) with the **Update tray icon installer** scheduled task,
or on the first push. Each push is a tray command, recorded in the audit trail.

### Install history

Every attempt to install the agent is kept for the device, whoever started it (a technician, a
company-wide push or an automatic install when the tray app connects), including pushes
MyPortal could not send (no tray app, a tray app too old with no newer version published, or
no agent build yet) with the reason. Each attempt shows one of:

| Result | Meaning |
| --- | --- |
| Not sent | MyPortal refused the push; the details say why. |
| Waiting for device | Queued until the device's tray app connects. |
| Waiting for tray update | The tray app was told to update itself first. |
| Installing | The tray app has the install and has not reported back yet. |
| No report | The tray app was sent the install over 2 hours ago and never reported back. |
| Failed | The tray app reported a failure; the details hold its error. |
| Installed | The tray app installed the agent. |

The asset page's **Scripts** card lists the latest ten attempts under **RMM agent install
history** (open while the device has no agent). On **Assets**, a device without the agent shows
its latest attempt under the **RMM agent** indicator; hover it for the reason. The history is
also at `GET /api/rmm/assets/{id}/agent-installs`.

### RMM actions on the Assets list

Once a device has the agent, technicians with write access to RMM scripts get its RMM actions in
the device's **Actions** menu on **Assets**:

- **Run script**: opens the run editor with the device already chosen.
- **RustDesk** and **MeshCentral** remote desktop, file transfer and terminal sessions, when
  those providers are switched on under [Remote Control](Remote%20Control.md). A status box in
  the corner of the page follows the session until it opens.
- **RMM agent and install history**: the device's Scripts card.

To install it by hand, as an administrator:

```text
MYPORTAL_TRAY_TOKEN=<tray device token> myportal-rmm enrol --url https://portal.example.com
myportal-rmm install
```

It runs scripts as the service account (SYSTEM on Windows, root on macOS and Linux). On
Windows it uses Windows PowerShell; on macOS and Linux it runs PowerShell scripts only when
`pwsh` is installed. Shell scripts do not run on Windows.

### Device rules for scripts

Schedules and onboarding steps share the Checks rule editor. Combine nested **AND** and
**OR** groups across asset and agent details, company details, hardware, operating system,
IP subnets, installed software, tags, asset custom fields and company variables. Numeric
ranges, version comparisons, list matching and presence checks use the same semantics as
Checks. Choose **Preview matching devices** to see current matches in the selected scope
that can use the script. Missing inventory cannot satisfy negative comparisons; use
**Is not set** explicitly when you want to match missing values.

Schedules evaluate rules each time they run, including **Run now**. Onboarding evaluates
rules before each step runs. Devices added later are included when they match.
