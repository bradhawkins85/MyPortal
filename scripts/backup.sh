#!/usr/bin/env bash
# MyPortal backup script for systemd (non-Docker) installs.
#
# Dumps the configured database (MySQL/MariaDB), archives the shared uploads
# directories, and prunes old backups to the configured retention count.
# Designed to be called directly, by the pre-upgrade hook in
# scripts/upgrade.sh, or by the myportal-backup.timer systemd timer.
#
# Credentials are read from the protected environment file and passed to
# mysqldump through a temporary --defaults-extra-file (mode 0600) so they
# never appear in process arguments or journal logs.
#
# Usage:
#   backup.sh [options]
#   backup.sh --restore BACKUP_FILE.sql.gz
#
# See --help for options.

set -Eeuo pipefail

info() { printf '==> %s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
MyPortal backup script (systemd / non-Docker installs)

Usage: backup.sh [options]
       backup.sh --restore BACKUP_FILE.sql.gz

Options:
  --label LABEL     Label for this backup set (default: scheduled)
  --db-only         Back up the database only
  --files-only      Back up the uploads directory (and Gitea) only
  --restore FILE    Restore a database backup and exit
  --dry-run         Print actions without executing
  -h, --help        Show this help

Environment:
  MYPORTAL_ENV_FILE          Path to the environment file
                             (default: /etc/myportal.env)
  BACKUP_DIR                 Backup output directory
                             (default: /opt/myportal/backups)
  MYPORTAL_BACKUPS_TO_KEEP   Number of backups to keep per kind
                             (default: 10)
  MYPORTAL_SHARED_ROOT       Shared data root (private_uploads/, uploads/)
                             (default: /opt/myportal/shared)
  MYPORTAL_GITEA_HOME        Gitea data for RMM scripts, backed up with the
                             files when present (default: /var/lib/gitea)
  SKIP_PRE_UPGRADE_BACKUP    Set to true to skip the pre-upgrade
                             database backup (not recommended)
EOF
}

LABEL="scheduled"
DB_ONLY=false
FILES_ONLY=false
DRY_RUN=false
RESTORE_FILE=""

while (($#)); do
  case "$1" in
    --label)      LABEL="${2:-}"; shift 2 ;;
    --db-only)    DB_ONLY=true; shift ;;
    --files-only) FILES_ONLY=true; shift ;;
    --restore)    RESTORE_FILE="${2:-}"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "unknown option: $1 (see --help)" ;;
  esac
done

if [[ -n "$RESTORE_FILE" ]] && { [[ "$DB_ONLY" == true || "$FILES_ONLY" == true ]]; }; then
  die "--restore cannot be combined with --db-only or --files-only."
fi
if [[ "$DB_ONLY" == true && "$FILES_ONLY" == true ]]; then
  die "--db-only and --files-only are mutually exclusive."
fi

ENV_FILE="${MYPORTAL_ENV_FILE:-/etc/myportal.env}"
BACKUP_DIR="${BACKUP_DIR:-/opt/myportal/backups}"
GITEA_HOME="${MYPORTAL_GITEA_HOME:-/var/lib/gitea}"
GITEA_CONFIG_DIR="${MYPORTAL_GITEA_CONFIG_DIR:-/etc/gitea}"
MYPORTAL_BACKUPS_TO_KEEP="${MYPORTAL_BACKUPS_TO_KEEP:-10}"
SHARED_ROOT="${MYPORTAL_SHARED_ROOT:-/opt/myportal/shared}"

if ! [[ "$MYPORTAL_BACKUPS_TO_KEEP" =~ ^[1-9][0-9]*$ ]]; then
  die "MYPORTAL_BACKUPS_TO_KEEP must be a positive integer (got: ${MYPORTAL_BACKUPS_TO_KEEP})."
fi

read_env_var() {
  local key="$1" default="${2:-}"
  if [[ ! -f "$ENV_FILE" ]]; then
    printf '%s' "$default"
    return 0
  fi
  local value
  value=$(awk -F'=' -v lookup="$key" '
    /^[[:space:]]*#/ { next }
    index($0, "=") == 0 { next }
    {
      name=$1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      if (name == lookup) {
        val=substr($0, index($0, "=") + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", val)
        if (length(val) >= 2 && ((substr(val,1,1) == "\"" && substr(val,length(val),1) == "\"") \
                                || (substr(val,1,1) == "\x27" && substr(val,length(val),1) == "\x27"))) {
          val=substr(val, 2, length(val)-2)
        }
        print val
        exit
      }
    }
  ' "$ENV_FILE" 2>/dev/null || true)
  printf '%s' "${value:-$default}"
}

DB_HOST="${DB_HOST:-$(read_env_var DB_HOST localhost)}"
DB_PORT="${DB_PORT:-$(read_env_var DB_PORT 3306)}"
DB_USER="${DB_USER:-$(read_env_var DB_USER myportal)}"
DB_PASSWORD="${DB_PASSWORD:-$(read_env_var DB_PASSWORD "")}"
DB_NAME="${DB_NAME:-$(read_env_var DB_NAME myportal)}"

create_mysqld_defaults() {
  local path
  path=$(mktemp)
  chmod 600 "$path"
  {
    printf '[client]\n'
    printf 'host=%s\n' "$DB_HOST"
    printf 'port=%s\n' "$DB_PORT"
    printf 'user=%s\n' "$DB_USER"
    printf 'password=%s\n' "$DB_PASSWORD"
  } >"$path"
  printf '%s' "$path"
}

restore_db() {
  local file="$1"
  [[ -f "$file" ]] || die "backup file not found: ${file}"
  if [[ ! -r "$file" ]]; then
    die "backup file is not readable: ${file}"
  fi

  info "Restoring database from ${file}…"

  if [[ "$DRY_RUN" == true ]]; then
    info "[dry-run] Would stop myportal@blue.service and myportal@green.service."
    info "[dry-run] Would decompress and apply ${file} to ${DB_NAME}@${DB_HOST}:${DB_PORT}."
    info "[dry-run] Would start myportal@blue.service and myportal@green.service."
    return 0
  fi

  command -v mysql >/dev/null 2>&1 || \
    die "the mysql client is not installed; it is required to restore a backup."

  local defaults_file
  defaults_file=$(create_mysqld_defaults)

  info "Stopping MyPortal services…"
  systemctl stop myportal@blue.service myportal@green.service 2>/dev/null || true

  info "Applying database dump…"
  gunzip -c "$file" | mysql --defaults-extra-file="$defaults_file"

  rm -f "$defaults_file"

  info "Starting MyPortal services…"
  systemctl start myportal@blue.service myportal@green.service 2>/dev/null || true
  info "Database restored."
}

if [[ -n "$RESTORE_FILE" ]]; then
  restore_db "$RESTORE_FILE"
  exit 0
fi

if [[ "$DRY_RUN" != true ]]; then
  install -d -m 0700 "$BACKUP_DIR"
fi
info "Backup directory: ${BACKUP_DIR}"

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)

backup_database() {
  local label="$1"
  local file="${BACKUP_DIR}/db-${label}-${TIMESTAMP}.sql.gz"
  info "Backing up database ${DB_NAME}@${DB_HOST}:${DB_PORT} to ${file}…"

  if [[ "$DRY_RUN" == true ]]; then
    info "[dry-run] Would run: mysqldump --single-transaction --databases ${DB_NAME} | gzip > ${file}"
    return 0
  fi

  command -v mysqldump >/dev/null 2>&1 || \
    die "mysqldump is not installed; it is required to back up the database."

  local defaults_file
  defaults_file=$(create_mysqld_defaults)

  local err_file="${file}.err"
  if ! mysqldump --defaults-extra-file="$defaults_file" \
    --single-transaction --routines --triggers --events \
    --add-drop-database --databases "$DB_NAME" 2>"$err_file" | gzip >"$file"; then
    local err
    err=$(cat "$err_file" 2>/dev/null || true)
    rm -f "$defaults_file" "$err_file" "$file"
    die "database backup failed.${err:+ stderr: ${err}}"
  fi
  rm -f "$defaults_file" "$err_file"

  if [[ ! -s "$file" ]]; then
    rm -f "$file"
    die "the database backup is empty; aborting."
  fi
  chmod 0600 "$file"
  info "Database backup complete: ${file}"
}

backup_uploads() {
  local label="$1"
  local file="${BACKUP_DIR}/files-${label}-${TIMESTAMP}.tar.gz"
  info "Backing up uploads to ${file}…"

  if [[ "$DRY_RUN" == true ]]; then
    info "[dry-run] Would archive ${SHARED_ROOT} uploads to ${file}"
    return 0
  fi

  local -a dirs=()
  [[ -d "${SHARED_ROOT}/private_uploads" ]] && dirs+=("private_uploads")
  [[ -d "${SHARED_ROOT}/uploads" ]] && dirs+=("uploads")

  if ((${#dirs[@]} == 0)); then
    warn "No upload directories found under ${SHARED_ROOT}; skipping files backup."
    return 0
  fi

  tar -czf "$file" -C "$SHARED_ROOT" "${dirs[@]}"
  chmod 0600 "$file"
  info "Uploads backup complete: ${file}"
}

backup_gitea() {
  # The RMM script library (scripts/provision_gitea.sh), when this host runs it.
  local label="$1"
  [[ -d "$GITEA_HOME" ]] || return 0
  local file="${BACKUP_DIR}/gitea-${label}-${TIMESTAMP}.tar.gz"
  info "Backing up the Gitea script library to ${file}…"

  if [[ "$DRY_RUN" == true ]]; then
    info "[dry-run] Would archive ${GITEA_HOME} and ${GITEA_CONFIG_DIR} to ${file}"
    return 0
  fi

  local -a paths=("${GITEA_HOME#/}")
  [[ -d "$GITEA_CONFIG_DIR" ]] && paths+=("${GITEA_CONFIG_DIR#/}")
  tar -czf "$file" -C / "${paths[@]}"
  chmod 0600 "$file"
  info "Gitea backup complete: ${file}"
}

prune_backups() {
  local keep="$MYPORTAL_BACKUPS_TO_KEEP"
  if [[ ! -d "$BACKUP_DIR" ]]; then
    return 0
  fi

  info "Pruning old backups (keeping ${keep} of each kind)…"

  local kind
  for kind in db files gitea; do
    # Collect all files of this kind, sorted by modification time (newest first).
    # The `|| true` prevents `set -e` from killing the subshell when no files match.
    local -a all_files=()
    while IFS= read -r f; do
      [[ -n "$f" ]] && all_files+=("$f")
    done < <(ls -1t "${BACKUP_DIR}"/${kind}-* 2>/dev/null || true)

    local total=${#all_files[@]}

    if ((total > keep)); then
      local i
      for ((i = keep; i < total; i++)); do
        if [[ "$DRY_RUN" == true ]]; then
          info "[dry-run] Would delete: ${all_files[$i]}"
        else
          rm -f "${all_files[$i]}"
        fi
      done
    fi

    local count
    count=$(find "$BACKUP_DIR" -maxdepth 1 -name "${kind}-*" 2>/dev/null | wc -l)
    info "Backups remaining (${kind}): ${count}"
  done
}

DO_DB=true
DO_FILES=true
[[ "$DB_ONLY" == true ]] && DO_FILES=false
[[ "$FILES_ONLY" == true ]] && DO_DB=false

if [[ "$DRY_RUN" == true ]]; then
  info "[dry-run] mode: no changes will be made."
  info "[dry-run] Database:  ${DB_NAME}@${DB_HOST}:${DB_PORT}  (back up: ${DO_DB})"
  info "[dry-run] Files:     ${SHARED_ROOT}  (back up: ${DO_FILES})"
  info "[dry-run] Label:     ${LABEL}"
  info "[dry-run] Retain:    ${MYPORTAL_BACKUPS_TO_KEEP} per kind"
fi

if [[ "$DO_DB" == true ]]; then
  backup_database "$LABEL"
fi

if [[ "$DO_FILES" == true ]]; then
  backup_uploads "$LABEL"
  backup_gitea "$LABEL"
fi

prune_backups

if [[ "$DRY_RUN" != true ]]; then
  info "Backup complete."
fi
