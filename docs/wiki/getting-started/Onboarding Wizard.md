# Onboarding Wizard

The onboarding wizard is a console command that walks an administrator through
the initial configuration of a MyPortal server and writes the answers to its
environment file. Run it from the server console, or from the Docker host for
Docker installs.

| Install type | Command | File it edits |
| --- | --- | --- |
| Docker (`myportal-docker`) | `sudo myportal-docker setup` | `/opt/myportal-docker/myportal.env` |
| Production (systemd) | `sudo python3 scripts/onboarding_wizard.py` | `/etc/myportal.env` |
| Development checkout | `python3 scripts/onboarding_wizard.py` | `.env` in the checkout |

Use `--env-file PATH` (or the `MYPORTAL_ENV_FILE` variable) to edit a different
file. The script only needs the Python standard library.

## What it asks

1. **Core settings** — application name, environment, portal URL, time zone,
   reverse proxies, session and encryption keys, database, Redis and the SMTP
   server. Missing session and encryption keys are generated for you. On Docker
   installs the database settings are managed by `myportal-docker` and are not
   shown.
2. **Every feature pack and module**, grouped into portal features, My Profile
   components, AI, telephony and messaging, and integrations. For each one it
   asks whether to enable it and, when enabled, prompts for its settings
   (for example the Xero client id and secret, or the Hudu URL and API key).
   Features that depend on another feature (for example IPAM on Assets) are
   skipped while their parent is disabled.

At each prompt:

- the current value is shown in `[brackets]`; press **Enter** to keep it;
- `*` marks a value that is required while the feature is enabled;
- `g` generates a random value for secrets that support it;
- `-` clears an optional value;
- secret values are typed without echo and shown masked.

Nothing is written until you confirm the summary of changes at the end. Press
**Ctrl+C** at any time to quit without saving.

## Re-running the wizard

The wizard is designed to be run again at any time:

- **Verify the file** — `--check` reports missing required values, placeholder
  or weak secrets (in production), invalid values, unknown slugs in the
  disabled lists and duplicated keys, without prompting. It exits with status
  1 when something needs attention, so it can be used in scripts or after an
  upgrade. Add `--verbose` to also list settings left at their defaults.
- **See feature status** — `--list` shows every feature and whether it is
  enabled, disabled or needs configuration.
- **Change or correct settings** — run the wizard again. Sections that are
  already configured ask "Review its settings?"; answer `y` to step through
  them with the current values pre-filled. Features with missing required
  values are always prompted.
- **Work on one feature** — `--feature xero` (repeatable, accepts feature keys
  or pack/module slugs; `--feature core` for the core settings) skips
  everything else.

```bash
sudo myportal-docker setup --check
sudo myportal-docker setup --feature xero --feature hudu
sudo python3 scripts/onboarding_wizard.py --list
```

## Disabling features

Answering **no** to "Enable …?" adds the feature's slugs to
`DISABLED_FEATURE_PACKS` and/or `DISABLED_MODULES` (see
[Component Deactivation](Component%20Deactivation.md)). The feature's settings
are **not** removed from the file, so enabling it again later removes the slugs
and restores the previous configuration.

## How the file is updated

- Existing lines, comments and ordering are preserved; changed keys are
  rewritten in place.
- New keys are appended at the end under a dated
  `# Added by the MyPortal onboarding wizard` heading, grouped by feature.
- The previous file is copied to `<file>.bak-<timestamp>` first, and the file
  keeps its permissions and owner.

Restart MyPortal to apply changes (`sudo myportal-docker restart` or
`sudo systemctl restart myportal`).
