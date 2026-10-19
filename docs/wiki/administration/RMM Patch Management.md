# RMM Patch Management

Open **Patch management** to configure Windows Update and Chocolatey application policies.
Apply migration **491** with the portal deployment. These jobs use the existing RMM
agent's PowerShell runner; no new agent version is required. Windows endpoints need
Windows PowerShell, an active RMM agent running as LocalSystem, and access to their
configured Windows Update service. Chocolatey must already be installed for app policies.

## Scope and device rules

Policies belong to the currently selected company or to every company. Global
policies require a super admin; company changes require Scripts write permission.
Global policies are visible but read-only to other technicians.

Policies reuse Checks' complete advanced rule builder, including nested AND/OR,
asset/company custom fields, software, hardware and network predicates. Rules use
current server inventory and can be previewed before saving. Only Windows agents
appear in the preview or execute patches.

For each device, a matching company policy replaces a matching global policy for
Windows Update or for the same Chocolatey package ID. At the same scope the
highest policy ID wins. A disabled matching policy disables that target, including
its inherited policy. An unmatched company rule leaves the global policy in place.
A malformed saved rule matches no devices; malformed settings never execute.
Create separate application policies to give different apps different device filters.

## Schedule and results

Configure a cron expression and IANA time zone. The automation tick claims due
policies once. Missed schedules older than an hour are skipped; queued patch jobs
expire after 15 minutes. Offline devices wait for the next policy run. **Run now**
also honors scope, targeting, enabled state and overrides. Only one active patch
job is queued per device; an endpoint mutex prevents concurrent patch operations.

At dispatch, the portal checks current inventory, policy versions and overrides.
Changed, deleted, disabled or newly overridden policies cancel queued work. Changes
after dispatch take effect on future runs. Multiple due policies on a device share
one patch job, with a two-hour execution limit. Results, selected policy names,
per-update outcomes, app commands and reboot decisions appear in **Scripts** run
history as **Patch policy**. Failures return a failed run; successful installation
is distinct from a completed restart. A run that times out may need review because
an installer can continue outside the PowerShell process.

## Windows Update

Select security, critical, rollup, general or definition updates and a deferral
period (0–90 days). The agent uses Windows Update COM APIs and the device's
configured update source (including WSUS), stable category identifiers, publication
age, EULA acceptance, download and installation. Hidden, installed, driver and
interactive updates are excluded, as are [blocked updates](#blocking-updates). Download, partial installation and per-update
failures are visible in run output. Feature upgrades are not included.

## Chocolatey applications

The policy form's application search uses stored Chocolatey matches for the apps
currently listed in Software on active Windows devices. The default **Update
Chocolatey app list** scheduled task refreshes those matches daily. It searches
each distinct installed application name, saves only unambiguous package
ID or title matches, and skips names without a verified match. Matching ignores
release numbers and architecture/language suffixes such as `24.09 (x64)` or
`(x64 en-US)`, and Chocolatey's `(Install)` title suffix. Editions and channels
remain distinct; ambiguous results are skipped. It does not
download the full Chocolatey catalog.

Super admins can run the task now, change its schedule, disable or delete it
under **Admin → Scheduled tasks**, and recreate it with the **Update Chocolatey
app list** command. The refresh saves its results every 25 applications and
starts with the names checked longest ago, so a run interrupted by a restart,
deployment or the community feed's rate limit keeps what it found and the next run
(including the task's automatic retries) carries on with the rest. A lookup that
Chocolatey rejects or that keeps failing keeps that application's previous match.
After five failed lookups in a row the run stops, keeps its progress and reports
how many lookups were saved (apply migration **505**). A complete pass removes
matches for software no longer in the current inventory. Portal searches
use only this stored list; before the first refresh, no applications are listed.
The task result reports checked, matched, unmatched, ambiguous, rejected and failed applications.
The original installed name is stored with each verified package match and is
used by both Software upgrade checks and the patch policy application picker.
After updating MyPortal, run **Update Chocolatey app list** again to populate
these mappings for existing inventory.

**Run now** starts the refresh in the background and returns straight away; a full refresh
can take much longer than a web request is allowed to wait. The Scheduled tasks page shows
**Running in the background…** and refreshes when the run is recorded in the task's history.

Each policy specifies an exact package ID and exact installed application display
name. The agent reads Chocolatey's local package list and machine-wide 32/64-bit
Windows uninstall registry inventory, then queries the latest stable version from
`https://community.chocolatey.org/api/v2/`.

- Already managed by Chocolatey and older: `choco upgrade <id> --version=<latest> --yes ...`.
- Installed externally and older: `choco install <id> --force --version=<latest> --yes ...`.
- Current or newer: skip; never intentionally downgrade.
- Absent: skip unless **Also install on devices where this app is missing** is enabled.

Commands pin the compared version and source. Registry names are exact,
case-insensitive matches. Ambiguous installations and version formats that cannot
be compared safely fail the app operation instead of guessing. Dotted numeric
versions are supported, with missing components padded by zero. Per-user apps are
outside the LocalSystem inventory. Chocolatey v1 and v2 local listing are supported.
Network/catalog/package errors are failures, never a signal that the app is absent.
Installer exit codes 1641 and 3010 indicate a required reboot; other nonzero
installation codes fail. Review packages before deployment: package scripts and
vendor installers can have their own restart behavior.

## Installing and updating apps without a script

Technicians with write access to `menu.rmm_scripts` can push Chocolatey packages
without writing a script. Both actions run the built-in patch job (shown in
**Scripts → Run history**) with the company's enabled Chocolatey sources, need an
active Windows agent, and never restart the device.

- **Asset page → Scripts → RMM → Install Choco Package**: opens a window to
  search the downloaded app list, choose an app (or enter a package ID) and push
  the install. Shown for Windows devices with the RMM agent.
- **Asset page → Software → Install an app with Chocolatey**: enter an exact
  package ID (suggestions come from the downloaded app list; any package in the
  company's enabled sources works). An installed app is updated to the latest
  version instead.
- **Script automation → Chocolatey apps**: tick up to 20 apps from the downloaded
  app list (or add package IDs), choose **Install, or update where already
  installed** or **Update only where already installed**, and push to every
  Windows device of the company or chosen devices. Devices already running a patch
  job are listed and skipped.

Apps that Software shows as installed are matched by their exact inventory name,
so current versions are skipped as described above. To keep apps updated on a
schedule, use a Chocolatey patch policy.

## Device Windows updates

Open a Windows asset with an RMM agent and choose **Windows updates** to see the updates the
device still needs and when each update was installed. Apply migrations **498** and **499** with the portal
deployment; no new agent version is required. Technicians with the `menu.rmm_scripts` permission
see the section; those who can run scripts can also scan and install.

- **Missing updates** lists what the device's configured update service (Windows Update,
  Microsoft Update or WSUS) still offers: classification, MSRC severity, release date, size, the
  date MyPortal first saw it missing and the last failed attempt with its error code. Drivers are
  not listed, matching patch policies. Updates hidden on the device by someone else are marked
  **Hidden on the device**.
- **Blocked updates** lists the updates the device needs that are blocked, what blocks them and
  whether the device has hidden them yet. **Blocks that reach this device** lists every block for
  the device, its company and every company, with **Unblock**.
- **Installed updates** shows each update's latest successful installation from the device's own
  Windows Update history, with who installed it (MyPortal, Windows automatic updates or another
  client). Updates later uninstalled are left out. Hotfixes Windows reports as installed but that
  are missing from its history, such as packages installed with DISM, are added with their install
  date.
- **Update history** lists every installation and uninstallation attempt with its result.
  Defender definition updates are limited to the latest 25 entries.

A built-in **Windows Update scan** runs through the RMM runner: once a day on connected
Windows devices, when a technician presses **Scan now** (the request waits up to 24 hours for an
offline device), and at the end of every patch job that installs Windows updates. Scans appear in
**Scripts** run history as **Windows Update scan**. A failed or uncollected scan is retried after six
hours. If part of a scan fails, for example the search for missing updates cannot reach WSUS, the
section shows the error and keeps what the device last reported for that part.

To push updates, select one or more missing updates (up to 100) and choose **Install selected
updates**. Choose whether the device may restart afterwards — never, only when no one is signed
in, or after warning signed-in users — and the warning time. The installation is a patch job, so it
follows the one-patch-job-per-device rule and its results appear in **Scripts** run history as
**Windows updates**. Patch policy categories, deferral and active hours do not apply to these
chosen updates. A restart that is already pending postpones installation; choose a restart option to
clear it first. Updates that need someone at the device cannot be installed remotely. Queued
installations expire if the device does not collect them within an hour.

### Blocking updates

Select missing updates and choose **Block selected updates** to block them for this device, this
company or every company (super admins only), with an optional reason. Patch management's
**Blocked Windows updates** card lists the blocks for the company, its devices and every company,
can block a KB number before any device reports it, and unblocks them. Device and company blocks
need Scripts write permission; blocks for every company need a super admin to add or remove.

A block names the update's KB number, which covers every revision and architecture of that
update; an update without a KB number is blocked by its update ID. Blocking a KB that is reused,
such as Defender's definition updates (KB2267602), blocks every update with that number.

- MyPortal never installs a blocked update: technicians cannot choose it and patch policies skip
  it, including jobs queued before the block (blocks are read when the device collects the job).
- The scan hides blocked updates on the device (`IUpdate.IsHidden`), so Windows automatic updates
  do not offer or install them, and shows again the updates MyPortal hid once they are unblocked.
  An update with a WSUS installation deadline can still be forced by WSUS; decline it there too. Updates someone else hid are left as they are. Blocking or unblocking from an asset
  page scans that device straight away; other devices apply the change at their next daily scan.
- Hidden updates stay hidden if the RMM agent is removed before they are unblocked; use Windows'
  *Show or hide updates* troubleshooter on the device in that case.

## Reboot management

The effective Windows Update policy controls reboot decisions for both Windows and
application patch jobs. Without an enabled matching Windows policy, app patching
never requests a restart. Defaults leave restarts to users.

Choose **Leave restart to the user**, **Restart only when no user is signed in**,
or **Restart even when users are signed in**. Set active hours in the device's
local time (including overnight intervals) and a 5–120 minute warning countdown.
MyPortal only requests a restart if the entire countdown falls outside active
hours. The no-user mode checks Windows sessions, including disconnected RDP
sessions; a query failure prevents the restart. This is a check when scheduling,
not a continuous sign-in restriction during the countdown.

Existing pending restarts defer installation. Pending restart flags and installer
results trigger the reboot decision; failed patch runs do not request a restart.
Deferred restarts are reconsidered on the next policy run. Windows shutdown
countdowns display a warning and can force applications closed when they expire.
The portal does not disable native Windows/GPO or third-party installer reboot
settings, which can also initiate restarts. Test policy settings on a pilot group
before broader rollout.
