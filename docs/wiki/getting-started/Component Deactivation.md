# Component Deactivation

MyPortal makes every bundled feature pack and module available unless an
operator explicitly excludes it. Deployment exclusions are useful when an
integration must not expose routes, start jobs, make external calls, or appear
in selectors and navigation.

## Configuration

The [Onboarding Wizard](Onboarding%20Wizard.md) asks about every feature pack
and module and maintains these variables for you; disabling a feature there
keeps its settings in the file.

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

### Core component slugs

These sections are served by the core application or share a feature pack with
other screens. They are disabled through the same `DISABLED_FEATURE_PACKS`
variable. A disabled core component is removed from navigation and every URL
under its prefixes returns 404. The **Admin → Feature packs** page lists each
one with its current status.

| Component | Slug | Route prefixes | Also disabled when |
| --- | --- | --- | --- |
| Network Devices | `network_devices` | `/devices`, `/network-scan` | pack `assets` is disabled |
| IPAM | `ipam` | `/ipam`, `/devices/ipam-preview`, `/devices/ipam-import`, `/api/infrastructure/networks`, `/api/infrastructure/addresses` | pack `assets` is disabled |
| Racks | `racks` | `/racks`, `/api/infrastructure/racks`, `/api/infrastructure/rack-equipment`, `/api/infrastructure/rack-reservations` | pack `assets` is disabled |
| Windows Defender | `defender` | `/defender`, `/api/defender`, `/api/tray/defender` | — |
| Office 365 (all sub menus) | `office365` | `/m365`, `/licenses`, `/api/licenses` | — |
| Shared Credentials | `shared_credentials` | `/shared-credentials`, `/credential-share`, `/api/vault` | — |
| Backup History | `backup_history` | `/admin/backup-jobs`, `/api/backup-jobs` | pack `backups` is disabled |
| Backup Summary | `backup_summary` | `/admin/backup-summary` | pack `backups` is disabled |
| RAG Index | `rag_index` | `/admin/rag`, `/rag`, `/api/rag` | — |
| AI Quality | `ai_quality` | `/admin/ai-quality` | — |
| AI Tag Synonyms | `ai_tag_synonyms` | `/admin/chat/ai-tag-synonyms`, `/chat/configuration` | — |
| Tray Agent / Tray Settings | `tray` | `/admin/tray`, `/api/tray`, `/tray` | — |
| Outlook Contacts (My Profile) | `outlook_contacts` | `/admin/profile/m365-contacts`, `/api/profile/m365-contacts` | — |
| Notification Contact (My Profile) | `notification_contact` | — (profile card only) | — |
| Email Signature (My Profile) | `email_signature` | — (profile card only) | — |
| Click to Call | `click_to_call` | `/api/click-to-call` | — |
| Forms | `forms` | `/myforms`, `/forms`, `/admin/forms`, `/api/forms` | — |
| SMB1001 Compliance | `smb1001` | `/compliance`, `/api/smb1001`, `/admin/marketing/smb1001-help-links` | pack `compliance` is disabled |
| Essential 8 Compliance (legacy) | `essential8` | `/compliance/essential8`, `/compliance/control`, `/compliance/requirements`, `/api/essential8`, `/admin/marketing/essential8-help-links` | pack `compliance` is disabled |
| GMP/GLP Compliance Checks | `gmp_glp` | — (content filter) | — |

My Profile components: `outlook_contacts` removes the Outlook contacts card
and the ticket "Check Outlook contacts" button; `click_to_call` removes the
click-to-call card and stops phone numbers being turned into call links;
`notification_contact` and `email_signature` remove their My Profile cards
only. Stored values are kept, so an existing email signature remains available
to automations.

`smb1001` removes the SMB1001 menu entry, the SMB1001 dashboard and its API,
the "SMB1001 help links" marketing page, and the SMB1001 report sections and
reporting queries.
Because the legacy Essential 8 pages live under `/compliance`, they are hidden
too. Compliance Checks stays available.

`essential8` removes the legacy Essential 8 pages and API, the "Essential 8
help links" marketing page, the "Legacy Essential 8 records" and "Import
Essential 8 progress" buttons on the SMB1001 page, and the Essential 8
reporting queries from company overview reports. SMB1001 and Compliance Checks
stay available. Disabling the whole `compliance` pack disables both.

`gmp_glp` hides the GMP and GLP categories, their predefined checks, company
assignments of those checks and the "Re-seed GMP/GLP" button from Compliance
Checks, and excludes them from compliance summaries. Custom categories and the
rest of Compliance are unaffected, and no rows are deleted.

Disabling `tray` also stops the tray agent API, so installed tray agents cannot
check in, and blocks the tray Defender endpoints. Disabling `office365` hides
the Office 365 menu with all of its sub menus (Configuration, Best Practices,
mailboxes, signatures, licenses, diagnostics, Out of Office and Spam Search &
Purge). Prefixes are matched per path segment, so `/devices` does not match
`/devices-report`.

```dotenv
DISABLED_FEATURE_PACKS=ipam,racks,defender,tray
```

Background jobs that rebuild the RAG index or sync Office 365 data are not
controlled by these slugs; manage those under **Admin → Scheduled Tasks** or
with the owning module (`m365-admin`, `m365-mail`).

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
