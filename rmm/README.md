# MyPortal RMM agent

`myportal-rmm` runs scripts pushed from MyPortal's **Scripts** page and reports
their exit code, output and custom values back. It is a separate program from
the tray app, with its own version, build workflow
(`.github/workflows/build-rmm-agent.yml`) and service:

| Platform | Service | State and work folder |
| --- | --- | --- |
| Windows | `MyPortalRMMAgent` (LocalSystem) | `%ProgramData%\MyPortal RMM` |
| macOS | LaunchDaemon (root) | `/Library/Application Support/MyPortal RMM` |
| Linux | systemd unit (root) | `/var/lib/myportal-rmm` |

## Install

The tray app installs the agent when MyPortal pushes it: from the Assets page
for one device, or automatically for every device of a company that has
**Install the RMM agent automatically** on (see
`docs/wiki/administration/RMM Scripts.md`). By hand, as an administrator:

```sh
MYPORTAL_TRAY_TOKEN=<tray device token> myportal-rmm enrol --url https://portal.example.com
myportal-rmm install
```

Enrolment uses the tray device's token, so the agent joins the tray device's
company and asset. The token is read from the environment (or
`--tray-token-file`), never the command line. `myportal-rmm uninstall` removes
the service.

## How it works

The agent long-polls `GET /api/rmm/agent/jobs`, runs up to four scripts at a
time and reports each result to `/api/rmm/agent/runs/{id}/result`.
Scripts default to sequential execution per device. A super administrator can
choose **Sequential**, **Parallel**, or **Run Anyway** under **Execution mode** on
its Scripts detail page. Parallel runs share four ordinary agent slots. Sequential
runs wait for ordinary active runs and block later ordinary runs. Run Anyway
bypasses queued scripts, sequential runs, all four occupied slots, and the ordinary
file-preparation lock. It does not hold up other scripts. Use it for independent
tasks or urgent actions such as rebooting; it still verifies script checksums and
associated files and observes the script timeout.

Onboarding runs always execute sequentially, overriding every script mode.
Execution settings are captured when runs are queued and survive Gitea sync.
Deploy MyPortal and migration 495 before updating RMM agents for Run Anyway.
Agents advertise `run_anyway_supported: true` on enrolment and check-in; Run Anyway
runs fail with an update message on older agents. Sequential and parallel execution
continue to work without an agent update.
While executing, it sends cumulative stdout/stderr snapshots once a second to
`/api/rmm/agent/runs/{id}/output`. The technician's result view refreshes every
second, follows new lines when scrolled to the bottom, and preserves the scroll
position when reading earlier output. Failed snapshots retry on the next tick;
the final result always includes captured output. Output remains capped at 1 MiB
per stream on the agent and 1,000,000 characters in MyPortal, with a visible
truncation notice. Session credential markers are masked before live storage.

Deploy MyPortal before updating RMM agents to enable live reporting. Older agents
continue to report final results; MyPortal also decodes their PowerShell CLIXML
errors, including previously stored runs. New agents use a PowerShell file wrapper
and text output to avoid serialized error records. See
`internal/runner` for how parameters, environment variables and custom values
are passed, and `docs/wiki/administration/RMM Scripts.md` for the script
conventions.

Scripts may also have associated Gitea files and absolute endpoint destinations.
The agent advertises `script_files_supported: true` on enrolment and check-in.
Before running a job it reuses destination files whose size, MD5 and SHA-256
match the manifest, or downloads the pinned version from
`GET /api/rmm/agent/runs/{id}/files/{index}` using its bearer token. Downloads
are verified in a temporary file and at the final destination. Any failure
stops the run before the script starts; existing files survive failed downloads.
File-bearing runs are serialized and file preparation shares the run timeout.
Gitea credentials stay on MyPortal.

On Windows it also sends event logs: it asks `GET /api/rmm/agent/event-logs/config`
which logs to collect (Application and System, plus extras chosen per company
or device), reads new entries with `wevtutil` and posts them to
`/api/rmm/agent/event-logs`, remembering its place in `eventlog-cursors.json`.
See `internal/eventlog` and `docs/wiki/administration/RMM Event Logs.md`.

For checks it asks `GET /api/rmm/agent/checks/config` what to measure (CPU,
memory, disks, network, ping targets and services) every five minutes and, while
the device has such checks, posts the measurements to
`/api/rmm/agent/checks/report` once a minute. A service check can have it start,
restart or stop the service. See `internal/checks` and
`docs/wiki/administration/RMM Checks.md`.

Every platform also sends a hardware and software inventory: once shortly after
the service starts, then whenever `GET /api/rmm/agent/inventory/config` says one
is due (daily, or when a technician presses **Refresh now** on the asset page).
See `internal/inventory` and `docs/wiki/administration/RMM Inventory.md`.

## Patch management

Windows Update and Chocolatey policies run as built-in PowerShell jobs through the
existing runner, with policy targeting rechecked before dispatch. They appear in
Scripts run history. Chocolatey must already be installed. See
`docs/wiki/administration/RMM Patch Management.md` for scope, schedule and reboot behavior.
Deploy MyPortal and migration 491 to enable policies; no agent upgrade is required.

## Develop

```sh
go test ./...
GOOS=windows go vet ./...
go build ./cmd/myportal-rmm
```

## Temperatures

At service startup and every regular detailed check-in (approximately 15 minutes),
the agent sends available endpoint temperature sensors in Celsius. Each reading
has an identifier, sensor name and hardware name. Collection is bounded to 20
seconds and 256 sensors; missing hardware, inaccessible sensors and failed reads
never prevent check-in. macOS currently reports collection as unsupported.

Linux reads hwmon sensors from sysfs, falling back to ACPI thermal zones when
hwmon is absent. Windows release builds embed LibreHardwareMonitorLib 0.9.6
(.NET Framework 4.7.2) and its dependencies, with locked NuGet package hashes and
original license notices. The agent expands its embedded bundle into a versioned
folder under `%ProgramData%\MyPortal RMM\LibreHardwareMonitor` on first check-in.
The existing agent download checksum covers the complete bundle. Install/update
and rollback continue to deliver a single executable. No GUI or separate download
is needed on endpoints; Windows PowerShell and .NET Framework 4.7.2 or later
(4.8.1 on ARM64) are required. Sensor availability varies with hardware, drivers and Windows security
settings. Low-level CPU and motherboard sensors use PawnIO. Windows builds also
embed its upstream 2.1.0 installer, pinned by SHA-256, along with its license and
corresponding sources. At service startup the agent checks the installed version
and runs `PawnIO_setup.exe -install -silent` if 2.1.0 or newer is absent. It keeps
newer installations and verifies the registered version after setup. Setup runs
in the background with a two-minute limit; failures are logged, leave check-in
working and retry at the next service start. A fresh temperature check-in follows
setup completion. PawnIO is shared system software, so agent rollback or removal
does not downgrade or uninstall it.

MyPortal keeps the latest snapshot for the asset's Scripts section. It also stores
temperature samples in the existing stats database independently of enabled
checks, and displays sensor graphs in Machine stats with its 24-hour, 7-day and
30-day ranges and configured retention. History uses server receipt time and the
configured stats sample period. If the stats database is unavailable, check-in
and the latest snapshot still work. Missing readings do not become zero-degree
samples. Older agents and job polling preserve the last snapshot and its timestamp.
Deploy MyPortal and migration 487 before updating agents.

For a local Windows build, install .NET SDK 8 and run from `rmm/`:

```sh
python3 thermal-bundle/build.py
go build ./cmd/myportal-rmm
```

The release workflow prepares the bundle automatically for both Windows builds.
Keep the checked-in NuGet lock file and include all library notices when changing
versions. Upstream source and license links are preserved in the bundled notices.
The disposable Windows CI runner also verifies silent PawnIO installation.
That integration test requires `MYPORTAL_TEST_PAWNIO_INSTALL=1`; ordinary local
test runs validate the setup policy without installing a driver.

The `myportal-rmm verify` command checks that the agent service is running and
sends an authenticated check-in using its saved enrolment. The tray uses this
after repairs and reports failure if either check fails.

### Chocolatey application catalog and company sources

Deploy MyPortal with migration **494** before updating the RMM agents. Chocolatey
must already be installed on Windows endpoints. Patch management now browses and
searches the stable Chocolatey community catalog. Selecting an application fills
its package ID and display name; adjust the display name to match device inventory.
The asset Software table checks visible rows against that catalog and offers
**Upgrade** for a unique exact name/package match with a newer numeric version.
Ambiguous names, unknown version formats, per-user applications, and versions
already current or newer are not automatically upgraded. One-off upgrades are
verified against current inventory again at dispatch and appear in Scripts run
history. They do not reboot the device.

Define available repositories in MyPortal's `.env` (or process environment):

```dotenv
CHOCO_SOURCE_DEFAULT='-n=chocolatey -s="https://community.chocolatey.org/api/v2/"'
CHOCO_SOURCE_CUSTOM='-n=nexus -s="https://repo.example.com/repository/chocolatey/" -u username -p "password with spaces"'
CHOCO_SOURCE_CUSTOM1='-n=internal -s="https://packages.example.com/chocolatey/"'
CHOCO_SOURCE_CUSTOM2='-n=another -s="https://another.example.com/chocolatey/"'
```

Enabled numbered sources are tried in numeric order (`CUSTOM1`, `CUSTOM2`,
`CUSTOM3`, and so on), followed by unnumbered `CUSTOM`, with `DEFAULT` last.
Disabled sources are skipped. Agents assign and verify positive Chocolatey source
priorities in that order during daily checks and before app management. Package
lookups use the first enabled source containing the package, even if a later feed
has a newer version. Installation is pinned to the selected version; Chocolatey
uses the configured source priorities to resolve that version and its dependencies.
Default is used only when no preferred source contains the package; a repository
error stops the job instead of silently changing feeds.

`CUSTOM` and any positive numbered suffix are supported, including gaps. Source
names (`-n`) must be unique. Each company starts with Default enabled and every
Custom source disabled. Company administrators can manage their toggles at
`/admin/companies/{company_id}/chocolatey-sources`; super admins also see them in
the company edit settings. Names are displayed, and URLs/passwords stay out of
those settings and audit records. Store real credentials only in the deployment
ENV, not in version control. Shell-quote values containing spaces; source strings
are parsed as data and never evaluated as commands.

The Windows RMM agent checks sources on startup and every 24 hours without
requiring a patch policy or queued action, retrying failures after 15 minutes.
Before app management it fetches current company sources again, reconciles and
verifies them under the same mutex as app installation, and stops on failure.
Repositories outside the enabled company list are disabled, including locally
added repositories. Enabled sources are recreated to apply URL and credential
changes. All-disabled companies perform no app management. Source definitions
and credentials are refreshed at dispatch as well for older agents running new
portal jobs; **the updated agent is required for the independent daily check**.

Catalog listings and asset upgrade checks use stored Chocolatey matches for
applications currently listed in Software on active Windows agents (migrations
**496** and **497**). The **Update Chocolatey app list** scheduled task is created
by default (daily at 04:23 in the scheduler's time zone). It deduplicates installed
application names across devices and versions, then searches Chocolatey for each
name. Only an unambiguous, exact package ID or title match is saved; missing and
ambiguous matches are skipped. It does not enumerate the public repository.

Super admins can run, reschedule, disable or delete the task under **Admin →
Scheduled tasks**, or recreate it with the **Update Chocolatey app list** command.
A failed lookup keeps the previous list; a successful refresh removes matches no
longer in the current inventory. Task results report applications checked,
matched, unmatched and ambiguous, plus packages stored and removed. Searches in
the portal use only the stored matches, including before the first refresh,
when the list is empty. Run the task after receiving the first Software inventory.

Installation/version resolution still uses the company's enabled feeds. A
private-only company therefore needs matching packages in its own feed.
The portal needs outbound HTTPS access to `community.chocolatey.org` for catalog
listings.
