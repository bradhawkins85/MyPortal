# Backups

MyPortal provides database and file backups for both Docker and systemd
(non-Docker) production installs. Backups protect your data before upgrades
and on a scheduled basis.

## Systemd (non-Docker) installs

### Automated scheduled backups

A `myportal-backup.timer` systemd timer runs `scripts/backup.sh` daily
(installed automatically by `install_environment.sh` and refreshed by
`upgrade.sh`). The timer is enabled on first install and on every upgrade.

Check its status:

```bash
systemctl status myportal-backup.timer
systemctl list-timers myportal-backup.timer
```

Run a backup manually:

```bash
sudo myportal-backup --label manual
```

### What is backed up

| Kind | Pattern | Contents |
|------|---------|----------|
| Database | `db-<label>-<UTC timestamp>.sql.gz` | Full `mysqldump` of the configured database (single-transaction, routines, triggers, events) |
| Event logs | `eventlog-<label>-<UTC timestamp>.sql.gz` | Only with `EVENT_LOG_DB_BACKUP=true`: `mysqldump` of the [RMM event log database](RMM%20Event%20Logs.md). Made with the database backup. |
| Stats | `stats-<label>-<UTC timestamp>.sql.gz` | Only with `STATS_DB_BACKUP=true`: `mysqldump` of the [RMM stats database](RMM%20Checks.md#machine-stats-and-the-stats-database). Made with the database backup. |
| Files | `files-<label>-<UTC timestamp>.tar.gz` | Archive of `private_uploads/` and `uploads/` under the shared root |
| Gitea | `gitea-<label>-<UTC timestamp>.tar.gz` | The RMM script library: `/var/lib/gitea` and `/etc/gitea`, when this host runs Gitea. Made with the files backup. |

The RMM event log database (`EVENT_LOG_DB_*`) is left out by default: it is
large and agents can send the logs again. Set `EVENT_LOG_DB_BACKUP=true` in the
environment file to back it up too. Restore one with
`gunzip -c eventlog-….sql.gz | mysql` using the event log database's user.

The RMM stats database (`STATS_DB_*`) is left out by default too: it only holds
history for the charts on asset pages. Set `STATS_DB_BACKUP=true` to back it up,
and restore it the same way from a `stats-….sql.gz` file.

Backups are written to `BACKUP_DIR` (default: `/opt/myportal/backups`)
with directory mode `0700` and file mode `0600`.

Each database backup also saves the current
[erasure ledger](../../security/gdpr-erasure.md) as `erasure-ledger.jsonl`
in the same directory. The ledger records every anonymised account, so a
restored backup can't bring one back. The saved copy is never replaced by a
shorter one. Copy it off the server with the environment file.

### Pre-upgrade backup

Every `upgrade.sh` run takes a database-only backup before migrations execute.
The backup is labelled `before-<first 12 chars of the target git SHA>`.
If the backup fails, the upgrade is **aborted** and the previous release
continues serving. Set `SKIP_PRE_UPGRADE_BACKUP=true` in the environment
file to disable this safety net (not recommended).

### Retention and rotation

After each backup, `scripts/backup.sh` prunes old backups:
- Keeps the newest `MYPORTAL_BACKUPS_TO_KEEP` files per kind (default: 10).
- Deletes older files automatically.

Configure retention in the environment file:

```
MYPORTAL_BACKUPS_TO_KEEP=14
```

### Restoring a database backup

1. Stop the MyPortal services:

   ```bash
   sudo systemctl stop myportal@blue.service myportal@green.service
   ```

2. Apply the backup:

   ```bash
   sudo myportal-backup --restore /opt/myportal/backups/db-before-abc123def456-20260101T000000Z.sql.gz
   ```

   This decompresses the `.sql.gz` file and pipes it to `mysql` using the
   credentials from the environment file. The services are stopped and
   restarted automatically.

3. If you restored manually (step 1 done separately), restart the services:

   ```bash
   sudo systemctl start myportal@blue.service myportal@green.service
   ```

### Restoring and anonymised accounts

A database backup taken before an account was anonymised still holds that
person's details. When MyPortal starts after a restore, it applies every
anonymisation recorded in the erasure ledger again before it serves requests.
If the live ledger is missing, as on a rebuilt server, `--restore` first puts
back the copy saved in `BACKUP_DIR`. Check **Admin → Account anonymisation**
afterwards for accounts that need a decision. See
[Account erasure and backups](../../security/gdpr-erasure.md).

### Restoring uploaded files

Extract the files backup to the shared root:

```bash
tar -xzf /opt/myportal/backups/files-scheduled-20260101T000000Z.tar.gz -C /opt/myportal/shared/
```

### Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_DIR` | `/opt/myportal/backups` | Directory for backup output |
| `MYPORTAL_BACKUPS_TO_KEEP` | `10` | Number of backups to retain per kind |
| `EVENT_LOG_DB_BACKUP` | `false` | Set `true` to also back up the RMM event log database (`eventlog-*.sql.gz`) |
| `STATS_DB_BACKUP` | `false` | Set `true` to also back up the RMM stats database (`stats-*.sql.gz`) |
| `SKIP_PRE_UPGRADE_BACKUP` | `false` | Set `true` to skip the pre-upgrade database backup |
| `MYPORTAL_SHARED_ROOT` | `/opt/myportal/shared` | Shared data root containing upload directories |
| `PRIVACY_ERASURE_LEDGER_PATH` | `$MYPORTAL_SHARED_ROOT/data/privacy/erasure-ledger.jsonl` | Erasure ledger saved with each database backup |

## Docker installs

Docker installs use the `myportal-docker backup` command:

```bash
sudo myportal-docker backup
```

This backs up the database, the shared files volume and, when the installation
runs Gitea, its data (`gitea-*.tar.gz`), and with `EVENT_LOG_DB_BACKUP=true` in
`myportal.env` the RMM event log database (`eventlog-*.sql.gz`, dumped from the
`db` container, which must be able to reach `EVENT_LOG_DB_HOST`) and with
`STATS_DB_BACKUP=true` the RMM stats database (`stats-*.sql.gz`, likewise), then prunes old backups using the same
`MYPORTAL_BACKUPS_TO_KEEP` setting. Backups are
stored in the directory configured for the Docker deployment.

To restore a database backup in a Docker install:

```bash
sudo myportal-docker restore-db /path/to/db-backup.sql.gz
```

`myportal-docker backup` saves the erasure ledger as `erasure-ledger.jsonl`
in the backup directory. `restore-db` merges that copy back into the live
ledger and re-applies recorded anonymisations once MyPortal is running.

## Development installs

The development installer does not install the backup timer. Run backups
manually with:

```bash
MYPORTAL_ENV_FILE=/path/to/.env MYPORTAL_SHARED_ROOT=/path/to/shared bash scripts/backup.sh --label dev
```

Development backups are separate from production and should not be mixed.

## Verifying backup health

```bash
# Check the timer is active
systemctl is-active myportal-backup.timer

# Check the most recent backup
ls -lt /opt/myportal/backups/ | head -5

# Verify a database backup is non-empty and gzipped
file /opt/myportal/backups/db-scheduled-*.sql.gz

# Dry-run to see what a backup would do without executing
sudo myportal-backup --dry-run
```
