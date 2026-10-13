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

## Develop

```sh
go test ./...
GOOS=windows go vet ./...
go build ./cmd/myportal-rmm
```
