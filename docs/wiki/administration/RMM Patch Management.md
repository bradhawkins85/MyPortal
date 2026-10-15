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
interactive updates are excluded. Download, partial installation and per-update
failures are visible in run output. Feature upgrades are not included.

## Chocolatey applications

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

## Device Windows updates

Open a Windows asset with an RMM agent and choose **Windows updates** to see the updates the
device still needs and when each update was installed. Apply migration **496** with the portal
deployment; no new agent version is required. Technicians with the `menu.rmm_scripts` permission
see the section; those who can run scripts can also scan and install.

- **Missing updates** lists what the device's configured update service (Windows Update,
  Microsoft Update or WSUS) still offers: classification, MSRC severity, release date, size, the
  date MyPortal first saw it missing and the last failed attempt with its error code. Hidden updates
  and drivers are not listed, matching patch policies.
- **Installed updates** shows each update's latest successful installation from the device's own
  Windows Update history, with who installed it (MyPortal, Windows automatic updates or another
  client). Updates later uninstalled are left out. Hotfixes Windows reports as installed but that
  are missing from its history, such as packages installed with DISM, are added with their install
  date.
- **Update history** lists every installation and uninstallation attempt with its result.
  Defender definition updates are limited to the latest 25 entries.

A built-in, read-only **Windows Update scan** runs through the RMM runner: once a day on connected
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
clear it first. Updates that need someone at the device cannot be selected. Queued installations
expire if the device does not collect them within an hour.

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
