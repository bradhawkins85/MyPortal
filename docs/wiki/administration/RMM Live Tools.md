# RMM Live Tools

**Live tools** give a technician a real-time, interactive session on a device
straight from its asset page, without RustDesk or MeshCentral. One session
covers a terminal, a task manager, a service manager, a file browser, a
Windows registry editor, a disk usage scanner and live CPU and memory graphs.
Remote desktop is not part of live tools; for that, use
[Remote Control](Remote%20Control.md).

Everything runs through the device's existing **MyPortal RMM agent** — the
same agent that runs [Scripts](RMM%20Scripts.md) — so there is nothing extra
to install on the device.

## Opening live tools

On a device's asset page, choose **Live tools** from the actions menu (under
**RMM**). The action appears when:

- the device has the RMM agent installed and **connected**, and
- the agent is new enough to support live tools (upgrade it from the device's
  **Scripts** section if the option is missing).

You need the same permission as running scripts (**RMM scripts**, write
access) on the device's company. The tools are scoped to that one device.

On the page, choose **Connect**. MyPortal asks the agent to open a session and
then relays everything between your browser and the device. Choose
**Disconnect**, or leave the page, to end the session. A session also ends on
its own after 30 minutes of inactivity.

## The tools

### Terminal

A real shell on the device, running as the agent's account (SYSTEM on Windows,
root on Linux and macOS). Pick the shell (PowerShell, `cmd`, Bash or zsh as
available) and open up to four terminals at once. Closing a terminal tab ends
that shell.

### Task manager

Every running process with its CPU and memory use, owner and thread count.
Filter by name, PID or user, sort any column, and **End** a process — with a
prompt to end its child processes too. Core operating-system processes and the
agent itself cannot be ended.

### Service manager

Lists the device's services (Windows services, systemd units or launchd jobs).
**Start**, **Stop** or **Restart** a service, and change its **start type**
(for example Automatic, Manual or Disabled on Windows; Enabled or Disabled on
Linux).

### File browser

Browse drives and folders. Rename, delete and create folders, **download** a
file to your computer and **upload** a file to the device. Transfers go in
verified chunks and each file's checksum is confirmed before it is put in
place.

### Registry editor (Windows)

Browse the registry hives, read and edit values (string, expandable string,
multi-string, DWORD, QWORD and binary), and create or delete keys. The hive
roots and the top-level keys beneath them are protected from deletion.

### Disk usage

Scan a drive or folder to find what is using space, like `ncdu`/`gncdu`. The
scan totals every folder so the biggest items float to the top; drill in to
follow the space down, and delete a file or folder from the results. By
default the scan stays on the drive or filesystem it starts on.

### Live CPU and memory

Per-core CPU, memory and swap/page-file use, network throughput, process count
and uptime, updated once a second while the Overview is open.

## Security and privacy

- Live tools use the device's authenticated, company-scoped RMM agent. Access
  follows the same roles and permissions as running scripts.
- The browser connects with a one-time token issued when the session starts,
  and the connection is checked against the portal's own origin.
- Nothing that flows through a session — terminal output, file contents,
  registry values — is stored. Only that a session happened, who started it
  and when (and when it closed) is kept, in the audit log and on the asset
  page's recent-sessions list.

## Deployment notes

- Deploy MyPortal with **migration 544** before updating RMM agents.
- Agents advertise `live_tools_supported` once upgraded; older agents simply
  do not offer live tools.
- When MyPortal runs several workers, sessions are relayed between them through
  Redis when `REDIS_URL` is configured, and otherwise through the database.
  Configuring Redis is recommended for the lowest-latency terminals.
