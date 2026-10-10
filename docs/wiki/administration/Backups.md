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
| Files | `files-<label>-<UTC timestamp>.tar.gz` | Archive of `private_uploads/` and `uploads/` under the shared root |
| Gitea | `gitea-<label>-<UTC timestamp>.tar.gz` | The RMM script library: `/var/lib/gitea` and `/etc/gitea`, when this host runs Gitea. Made with the files backup. |

Backups are written to `BACKUP_DIR` (default: `/opt/myportal/backups`)
with directory mode `0700` and file mode `0600`.

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
| `SKIP_PRE_UPGRADE_BACKUP` | `false` | Set `true` to skip the pre-upgrade database backup |
| `MYPORTAL_SHARED_ROOT` | `/opt/myportal/shared` | Shared data root containing upload directories |

## Docker installs

Docker installs use the `myportal-docker backup` command:

```bash
sudo myportal-docker backup
```

This backs up the database, the shared files volume and, when the installation
runs Gitea, its data (`gitea-*.tar.gz`), then prunes old backups using the same
`MYPORTAL_BACKUPS_TO_KEEP` setting. Backups are
stored in the directory configured for the Docker deployment.

To restore a database backup in a Docker install:

```bash
sudo myportal-docker restore /path/to/db-backup.sql.gz
```

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
