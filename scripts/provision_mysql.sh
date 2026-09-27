#!/usr/bin/env bash
set -euo pipefail

# Install and initialise a local MariaDB server and the application's database
# account (run as root). Remote database hosts and SQLite configurations are
# left alone.
ENV_FILE="${1:-}"
if [[ -z "$ENV_FILE" || ! -f "$ENV_FILE" ]]; then
  echo "Error: provision_mysql.sh requires the path to an environment file." >&2
  exit 1
fi

PYTHON_BIN=$(command -v python3 || command -v python || true)
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: Python 3 is required to read ${ENV_FILE}." >&2
  exit 1
fi

read_env_value() {
  ENV_LOOKUP_FILE="$ENV_FILE" ENV_LOOKUP_KEY="$1" "$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path

key = os.environ["ENV_LOOKUP_KEY"]
for raw_line in Path(os.environ["ENV_LOOKUP_FILE"]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        print(value, end="")
        break
PY
}

DB_HOST=$(read_env_value DB_HOST)
DB_USER=$(read_env_value DB_USER)
DB_PASSWORD=$(read_env_value DB_PASSWORD)
DB_NAME=$(read_env_value DB_NAME)

if [[ -z "$DB_HOST" && -z "$DB_USER" && -z "$DB_NAME" ]]; then
  echo "Database settings are empty; using the application's SQLite fallback." >&2
  exit 0
fi

case "${DB_HOST,,}" in
  localhost|127.0.0.1|::1) ;;
  *)
    echo "Database host ${DB_HOST:-<empty>} is not local; skipping MySQL server provisioning." >&2
    exit 0
    ;;
esac

if [[ ! "$DB_USER" =~ ^[A-Za-z0-9_.-]+$ || ! "$DB_NAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "Error: DB_USER and DB_NAME may contain only letters, numbers, dot, underscore, and hyphen." >&2
  exit 1
fi
if [[ -z "$DB_PASSWORD" ]]; then
  echo "Error: DB_PASSWORD must be set before provisioning MySQL." >&2
  exit 1
fi

server_binary() {
  command -v mariadbd 2>/dev/null || command -v mysqld 2>/dev/null || true
}

# Migrations call functions such as RANDOM_BYTES() that MariaDB added in
# 10.10. Ubuntu 22.04 ships 10.6, Ubuntu 24.04 and Debian 12 ship 10.11.
MIN_MARIADB_VERSION="10.10"

version_at_least() {
  printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

require_supported_mariadb() {
  local version="$1" origin="$2"
  if [[ -z "$version" ]] || ! version_at_least "$version" "$MIN_MARIADB_VERSION"; then
    echo "Error: ${origin} provides MariaDB ${version:-<unknown>}; MyPortal requires MariaDB ${MIN_MARIADB_VERSION} or later." >&2
    echo "Use Ubuntu 24.04 or Debian 12 (or newer), install MariaDB 10.11 LTS from https://mariadb.org/download/, or point DB_HOST at a supported server." >&2
    exit 1
  fi
}

# The schema migrations use MariaDB syntax (for example
# "ALTER TABLE ... ADD COLUMN IF NOT EXISTS"), which Oracle MySQL rejects.
# Ubuntu's default-mysql-server package is Oracle MySQL, so always ask for
# MariaDB explicitly.
if [[ -z "$(server_binary)" ]]; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Error: No local MariaDB server found. Install MariaDB 10.6 or later and rerun the installer." >&2
    exit 1
  fi
  echo "No local database server found; installing MariaDB…" >&2
  apt-get update -qq || echo "Warning: apt-get update failed." >&2
  candidate=$(apt-cache policy mariadb-server 2>/dev/null | awk '/Candidate:/ {print $2}' | sed -E 's/^[0-9]+://; s/[-+~].*//')
  [[ "$candidate" == "(none)" ]] && candidate=""
  # Check before installing so an unsupported release fails without leaving
  # a half-migrated database behind.
  require_supported_mariadb "$candidate" "The distribution's mariadb-server package"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mariadb-server mariadb-client
fi

if ! "$(server_binary)" --version 2>/dev/null | grep -qi mariadb; then
  echo "Error: the local database server is Oracle MySQL ($("$(server_binary)" --version 2>/dev/null))." >&2
  echo "MyPortal migrations require MariaDB. Replace it with the mariadb-server package, or point DB_HOST at a MariaDB server, and rerun the installer." >&2
  exit 1
fi
require_supported_mariadb \
  "$("$(server_binary)" --version 2>/dev/null | grep -oE 'Ver [0-9]+\.[0-9]+(\.[0-9]+)?' | awk '{print $2}')" \
  "The installed database server"

if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now mariadb >/dev/null 2>&1 || systemctl enable --now mysql
elif command -v service >/dev/null 2>&1; then
  service mariadb start 2>/dev/null || service mysql start
else
  echo "Error: MariaDB was installed but no supported service manager was found." >&2
  exit 1
fi

MYSQL_BIN=$(command -v mariadb || command -v mysql || true)
if [[ -z "$MYSQL_BIN" ]]; then
  echo "Error: MySQL client was not installed with the server." >&2
  exit 1
fi

# Pass SQL on stdin rather than the command line. This prevents the database
# password from appearing in process listings. Values are escaped by Python;
# identifiers have already been restricted to a conservative character set.
SQL=$(DB_PROVISION_USER="$DB_USER" DB_PROVISION_PASSWORD="$DB_PASSWORD" \
  DB_PROVISION_NAME="$DB_NAME" "$PYTHON_BIN" - <<'PY'
import os

def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

user = os.environ["DB_PROVISION_USER"]
password = sql_string(os.environ["DB_PROVISION_PASSWORD"])
database = os.environ["DB_PROVISION_NAME"]
print(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
for host in ("localhost", "127.0.0.1"):
    print(f"CREATE USER IF NOT EXISTS {sql_string(user)}@{sql_string(host)} IDENTIFIED BY {password};")
    print(f"ALTER USER {sql_string(user)}@{sql_string(host)} IDENTIFIED BY {password};")
    print(f"GRANT ALL PRIVILEGES ON `{database}`.* TO {sql_string(user)}@{sql_string(host)};")
print("FLUSH PRIVILEGES;")
PY
)
printf '%s\n' "$SQL" | "$MYSQL_BIN" --protocol=socket
echo "Local MySQL database and application account are ready." >&2
