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

The tray app installs the agent. By hand, as an administrator:

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
time and reports each result to `/api/rmm/agent/runs/{id}/result`. See
`internal/runner` for how parameters, environment variables and custom values
are passed, and `docs/wiki/administration/RMM Scripts.md` for the script
conventions.

## Develop

```sh
go test ./...
GOOS=windows go vet ./...
go build ./cmd/myportal-rmm
```
