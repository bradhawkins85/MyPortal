# Component Deactivation

MyPortal makes every bundled feature pack and module available unless an
operator explicitly excludes it. Deployment exclusions are useful when an
integration must not expose routes, start jobs, make external calls, or appear
in selectors and navigation.

## Configuration

Set either optional variable in the deployment `.env` file (or the systemd
environment file):

| Variable | Value | Empty default |
| --- | --- | --- |
| `DISABLED_FEATURE_PACKS` | Comma-separated feature-pack slugs | all packs available |
| `DISABLED_MODULES` | Comma-separated module slugs | all modules available |

Whitespace and duplicate entries are ignored. Slugs are case-sensitive and an
unknown slug prevents startup with an actionable configuration error rather
than silently producing a partially reduced deployment. Changes require an
application restart; hot reload does not change deployment availability.

To disable one feature pack:

```dotenv
DISABLED_FEATURE_PACKS=trello
DISABLED_MODULES=
```

To disable several packs and modules:

```dotenv
DISABLED_FEATURE_PACKS=trello,xero,uptimekuma
DISABLED_MODULES=smtp2go,whisperx,unifi-talk
```

An environment exclusion has higher precedence than database `enabled` flags
and administrator settings. An administrator cannot re-enable an excluded
component. The exclusion is a runtime availability policy only: it does not
delete or rewrite database rows, settings, credentials, or historical data.
Removing the slug and restarting therefore restores the component with its
previous state. Normal authorization still applies after restoration.

## Feature-pack and module dependencies

Some modules own a feature pack. Disabling either side makes **both** the module
and its associated pack unavailable. For example, disabling module `trello`
prevents pack `trello` from loading, while disabling pack `m365_mail` makes
module `m365-mail` unavailable. Module-only capabilities (for example
`smtp2go`) are still suppressed when their module is disabled. Shared
capabilities remain available while at least one available module owns them;
unrelated components are unaffected.

Unavailable components do not register active feature-pack routes, are omitted
from module catalogues and UI discovery, cannot dispatch scheduled commands or
automations, and cannot call their owned external services. Their database
configuration is intentionally retained.

### Valid feature-pack slugs

`api_keys`, `assets`, `automations`, `backups`, `call_recordings`, `calls`,
`cart`, `chat`, `chatgpt_mcp`, `companies`, `compliance`, `continuity`, `dmarc`,
`help`, `hudu`, `huntress`, `imap`, `invoices`, `issue_tracker`,
`knowledge_base`, `m365_admin`, `m365_mail`, `marketing`, `matrix_chat_assign`,
`message_templates`, `notifications`, `ntfy`, `ollama`, `orders`,
`password_pusher`, `quotes`, `receive_sms`, `reporting`, `reports`,
`reprocess_ai`, `service_status`, `shop`, `sms_gateway`, `smtp`, `solidtime`,
`staff`, `subscriptions`, `syncro`, `tacticalrmm`, `tickets`, `trello`,
`uptimekuma`, `voice_monitor`, `webhooks`, `xero`.

The runtime source of truth is the directories under `app/features/`; a new
release may add slugs.

### Valid module slugs

`syncro`, `ollama`, `smtp`, `smtp2go`, `m365-direct-delivery`, `imap`,
`receive-sms`, `calls`, `voice-monitor`, `m365-mail`, `tacticalrmm`, `ntfy`,
`apprise`, `uptimekuma`, `chatgpt-mcp`, `ollama-mcp`, `xero`, `sms-gateway`,
`m365-admin`, `call-recordings`, `whisperx`, `unifi-talk`, `reprocess-ai`,
`password-pusher`, `hudu`, `huntress`, `trello`, `solidtime`, and
`matrix-chat-assign`.

The runtime source of truth is `DEFAULT_MODULES` in `app/services/modules.py`;
a new release may add slugs. Feature-pack identifiers use underscores while
module identifiers generally use hyphens, so copy the slug from the relevant
list rather than converting it.

## Install, upgrade, and rollback

New installations receive both variables with empty defaults. Re-running the
installer adds a missing key but never replaces an operator-provided value.
Blue/green upgrades link each release to the persistent environment file, so
exclusions survive staging, health checks, cutover, shutdown of the old
instance, and rollback. Keep the environment file outside immutable release
directories and include it in the usual protected configuration backup.

After changing exclusions:

1. Restart or perform the normal blue/green upgrade.
2. Confirm startup has no unknown-slug or dependency errors.
3. Confirm `/healthz` succeeds.
4. Confirm excluded routes return unavailable/not found, owned scheduled work
   and automations do not dispatch, and excluded UI entries/selectors are gone.
5. Exercise an unrelated API and UI page before shutting down the old process.
6. To restore a component, remove its slug, restart, and verify its prior
   settings and historical records are present.
