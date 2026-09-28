#!/usr/bin/env bash
# MyPortal Docker installer and upgrader.
#
# A single, self-contained script: download it and run it; no git clone needed.
#
#   curl -fsSLO https://github.com/bradhawkins85/MyPortal/releases/latest/download/myportal-docker.sh
#   sudo bash myportal-docker.sh install
#
# It installs Docker Engine and the Compose plugin when they are missing,
# writes a docker-compose.yml (MyPortal + MariaDB) with generated secrets, and
# deploys the latest published GitHub release. Upgrades only ever move to a
# published GitHub release; they back up the database first and roll back to
# the previous release if the new one does not become healthy.
#
# After installation the script is available as "myportal-docker".
# Run "myportal-docker help" for all commands.
set -Eeuo pipefail
umask 027

SCRIPT_VERSION="1"

# ---------------------------------------------------------------------------
# Settings. Each may be overridden from the environment.
# ---------------------------------------------------------------------------
MYPORTAL_DIR="${MYPORTAL_DIR:-/opt/myportal-docker}"
MYPORTAL_REPO="${MYPORTAL_REPO:-bradhawkins85/MyPortal}"
MYPORTAL_GITHUB_API="${MYPORTAL_GITHUB_API:-https://api.github.com}"
MYPORTAL_GITHUB_URL="${MYPORTAL_GITHUB_URL:-https://github.com}"
MYPORTAL_IMAGE_REPO="${MYPORTAL_IMAGE_REPO:-ghcr.io/$(printf '%s' "$MYPORTAL_REPO" | tr '[:upper:]' '[:lower:]')}"
MYPORTAL_DB_IMAGE="${MYPORTAL_DB_IMAGE:-mariadb:11.4}"
# Optional: base image for local builds, and a CA bundle for networks that
# inspect TLS (passed to the build as a secret, never stored in the image).
MYPORTAL_BASE_IMAGE="${MYPORTAL_BASE_IMAGE:-}"
MYPORTAL_BUILD_CA_FILE="${MYPORTAL_BUILD_CA_FILE:-}"
# Optional network for local builds, e.g. "host" when the only proxy listens
# on localhost.
MYPORTAL_BUILD_NETWORK="${MYPORTAL_BUILD_NETWORK:-}"
MYPORTAL_HEALTH_TIMEOUT="${MYPORTAL_HEALTH_TIMEOUT:-600}"
MYPORTAL_BACKUPS_TO_KEEP="${MYPORTAL_BACKUPS_TO_KEEP:-10}"

INSTALLED_SCRIPT="/usr/local/bin/myportal-docker"
AUTO_UPGRADE_CRON="/etc/cron.d/myportal-docker-upgrade"
SCRIPT_ASSET="myportal-docker.sh"

COMPOSE_FILE="${MYPORTAL_DIR}/docker-compose.yml"
PROJECT_ENV="${MYPORTAL_DIR}/.env"            # compose variables (versions, port)
APP_ENV="${MYPORTAL_DIR}/myportal.env"        # application configuration
DB_ENV="${MYPORTAL_DIR}/mariadb.env"          # database container credentials
BACKUP_DIR="${MYPORTAL_DIR}/backups"

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
info() { printf '==> %s\n' "$*" >&2; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
MyPortal Docker installer and upgrader (script version ${SCRIPT_VERSION})

Usage: $(basename "$0") <command> [options]

Commands:
  install [--version TAG] [--port PORT] [--bind ADDRESS]
        Install Docker if needed and deploy MyPortal (default: latest release,
        port 80 on all addresses).
  upgrade [--version TAG] [--yes]
        Upgrade to the latest GitHub release (or TAG). Backs up the database
        first and rolls back if the new release is not healthy.
  check                Report the installed and latest release. Exits 0 when
                       up to date and 10 when an upgrade is available.
  status               Show the containers and the running release.
  self-update          Reinstall this script from the installed release, to
                       get commands that release added.
  logs [ARGS...]       Follow the application logs (docker compose logs ARGS).
  backup               Back up the database and uploaded files.
  restore-db FILE      Restore a database backup (.sql.gz) made by this script.
  restart              Restart MyPortal (applies changes to myportal.env).
  setup [--check | --list | --feature NAME]
        Run the onboarding wizard: choose which feature packs and modules are
        enabled and configure their settings in myportal.env. Re-run it at any
        time to review or correct settings; --check only reports missing or
        invalid values. Disabling a feature keeps its settings.
  auto-upgrade on|off  Check for and apply new releases daily.
  superadmin list      List the users with super administrator rights.
  superadmin grant USERNAME
        Grant super administrator rights to a user (USERNAME is the email
        address the user signs in with).
  superadmin revoke USERNAME [--force]
        Revoke super administrator rights. Refuses to remove the last active
        super administrator unless --force is given.
  superadmin create USERNAME [--first-name NAME] [--last-name NAME] [--generate-password]
        Create a new super administrator, for example when nobody can sign in.
  superadmin reset-password USERNAME [--generate-password] [--reset-2fa]
        Set a new password for a super administrator and sign it out of all
        sessions; --reset-2fa also removes its authenticator apps and passkeys.
        Passwords are prompted for, read from standard input when it is not a
        terminal, or generated and printed with --generate-password.
  user verify USERNAME Mark a user's email address as verified and activate the
                       account, bypassing the emailed verification link.
  help                 Show this help.

Installation directory: ${MYPORTAL_DIR} (override with MYPORTAL_DIR).
EOF
}

require_root() {
  [[ "${EUID:-$(id -u)}" == 0 ]] || die "run this command as root (for example with sudo)."
}

# ---------------------------------------------------------------------------
# Docker Engine and Compose
# ---------------------------------------------------------------------------
os_release() {
  # Print one field of /etc/os-release.
  [[ -r /etc/os-release ]] || return 0
  ( . /etc/os-release && eval "printf '%s' \"\${$1:-}\"" )
}

start_docker_daemon() {
  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    systemctl enable --now docker >/dev/null 2>&1 || systemctl start docker
  elif command -v service >/dev/null 2>&1; then
    service docker start >/dev/null 2>&1 || true
  fi
  local waited=0
  until docker info >/dev/null 2>&1; do
    ((waited < 30)) || die "the Docker daemon is not running. Start it and rerun this command."
    sleep 1
    waited=$((waited + 1))
  done
}

install_docker_apt() {
  local distro="$1" codename="$2"
  info "Installing Docker Engine from download.docker.com (${distro} ${codename})…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${distro}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$distro" "$codename" >/etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
}

install_docker_dnf() {
  local distro="$1" repo_file
  repo_file="https://download.docker.com/linux/${distro}/docker-ce.repo"
  info "Installing Docker Engine from download.docker.com (${distro})…"
  dnf -y -q install dnf-plugins-core >/dev/null 2>&1 || true
  # dnf 4 and dnf 5 spell "add a repository" differently.
  dnf config-manager --add-repo "$repo_file" >/dev/null 2>&1 \
    || dnf config-manager addrepo --from-repofile="$repo_file" >/dev/null
  dnf -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
}

install_docker_engine() {
  local id id_like codename
  id=$(os_release ID)
  id_like=" $(os_release ID_LIKE) "
  case "$id" in
    ubuntu|debian|raspbian)
      codename=$(os_release VERSION_CODENAME)
      [[ "$id" == raspbian ]] && id=debian
      install_docker_apt "$id" "$codename"
      ;;
    fedora|centos|rhel)
      install_docker_dnf "$id"
      ;;
    rocky|almalinux|ol)
      install_docker_dnf rhel
      ;;
    *)
      if [[ "$id_like" == *" ubuntu "* && -n "$(os_release UBUNTU_CODENAME)" ]]; then
        install_docker_apt ubuntu "$(os_release UBUNTU_CODENAME)"      # Mint, Pop!_OS, …
      elif [[ "$id_like" == *" debian "* && -n "$(os_release VERSION_CODENAME)" ]]; then
        install_docker_apt debian "$(os_release VERSION_CODENAME)"
      elif [[ "$id_like" == *" rhel "* || "$id_like" == *" fedora "* ]]; then
        install_docker_dnf rhel
      else
        info "Installing Docker Engine with Docker's convenience script (get.docker.com)…"
        curl -fsSL https://get.docker.com | sh
      fi
      ;;
  esac
}

install_compose_plugin() {
  # Docker is present (for example the distribution's docker.io package) but
  # the Compose v2 plugin is not.
  info "Installing the Docker Compose plugin…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf -y -q install docker-compose-plugin >/dev/null 2>&1 || true
  fi
  if ! docker compose version >/dev/null 2>&1; then
    local arch plugin_dir=/usr/local/lib/docker/cli-plugins
    arch=$(uname -m)
    install -d -m 0755 "$plugin_dir"
    curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${arch}" \
      -o "${plugin_dir}/docker-compose"
    chmod 0755 "${plugin_dir}/docker-compose"
  fi
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    install_docker_engine
  fi
  command -v docker >/dev/null 2>&1 || die "Docker installation failed; install Docker Engine manually and rerun."
  start_docker_daemon
  if ! docker compose version >/dev/null 2>&1; then
    install_compose_plugin
  fi
  docker compose version >/dev/null 2>&1 \
    || die "Docker Compose v2 is required (the 'docker compose' command). Install it and rerun."
}

ensure_buildx() {
  # Only needed when an image has to be built locally.
  docker buildx version >/dev/null 2>&1 && return 0
  info "Installing the Docker buildx plugin for local image builds…"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx-plugin >/dev/null 2>&1 \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-buildx >/dev/null 2>&1 || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf -y -q install docker-buildx-plugin >/dev/null 2>&1 || true
  fi
  docker buildx version >/dev/null 2>&1 || die "Docker buildx is required to build the image locally."
}

compose() {
  local -a files=(-f "$COMPOSE_FILE")
  # Naming the file explicitly disables Compose's automatic override lookup.
  [[ ! -f "${MYPORTAL_DIR}/docker-compose.override.yml" ]] \
    || files+=(-f "${MYPORTAL_DIR}/docker-compose.override.yml")
  docker compose --project-directory "$MYPORTAL_DIR" "${files[@]}" "$@"
}

# ---------------------------------------------------------------------------
# GitHub releases
# ---------------------------------------------------------------------------
latest_release_tag() {
  local body tag
  body=$(curl -fsSL -H 'Accept: application/vnd.github+json' \
    "${MYPORTAL_GITHUB_API}/repos/${MYPORTAL_REPO}/releases/latest") \
    || die "could not query the latest release of ${MYPORTAL_REPO} from GitHub."
  tag=$(printf '%s' "$body" | tr -d '\n' | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/')
  [[ -n "$tag" ]] || die "GitHub did not report a published release for ${MYPORTAL_REPO}."
  printf '%s' "$tag"
}

valid_tag() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]
}

version_newer() {
  # True when $1 sorts after $2 (ignoring a leading "v").
  [[ "$1" != "$2" ]] && [[ "$(printf '%s\n%s\n' "${1#v}" "${2#v}" | sort -V | tail -n1)" == "${1#v}" ]]
}

# Pull the published image for a release, or build it from the release's
# source archive when it cannot be pulled. Prints the image reference.
obtain_image() {
  local tag="$1" ref="${MYPORTAL_IMAGE_REPO}:$1"
  info "Pulling ${ref}…"
  if docker pull --quiet "$ref" >/dev/null 2>&1; then
    printf '%s' "$ref"
    return 0
  fi
  warn "could not pull ${ref}; building release ${tag} locally from its source archive."
  ensure_buildx
  local workdir archive source
  workdir=$(mktemp -d)
  archive="${workdir}/source.tar.gz"
  if ! curl -fsSL "${MYPORTAL_GITHUB_URL}/${MYPORTAL_REPO}/archive/refs/tags/${tag}.tar.gz" -o "$archive"; then
    rm -rf "$workdir"
    die "could not download the source archive for release ${tag}."
  fi
  tar -xzf "$archive" -C "$workdir"
  source=$(find "$workdir" -mindepth 1 -maxdepth 1 -type d | head -n1)
  if [[ ! -f "${source}/Dockerfile" ]]; then
    rm -rf "$workdir"
    die "release ${tag} predates Docker support (its source has no Dockerfile). Choose a newer release."
  fi
  local -a args=(--tag "myportal-local:${tag}" --build-arg "MYPORTAL_VERSION=${tag}")
  [[ -z "$MYPORTAL_BASE_IMAGE" ]] || args+=(--build-arg "BASE_IMAGE=${MYPORTAL_BASE_IMAGE}")
  [[ -z "$MYPORTAL_BUILD_CA_FILE" ]] || args+=(--secret "id=build_ca,src=${MYPORTAL_BUILD_CA_FILE}")
  [[ -z "$MYPORTAL_BUILD_NETWORK" ]] || args+=(--network "$MYPORTAL_BUILD_NETWORK")
  info "Building myportal-local:${tag} (this takes a few minutes)…"
  if ! docker buildx build --load "${args[@]}" "$source" >&2; then
    rm -rf "$workdir"
    die "building the image for release ${tag} failed."
  fi
  rm -rf "$workdir"
  printf '%s' "myportal-local:${tag}"
}

# ---------------------------------------------------------------------------
# Configuration files
# ---------------------------------------------------------------------------
random_secret() {
  # URL-safe random string of the requested length.
  local length="$1" value=""
  while ((${#value} < length)); do
    value+=$(head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9_-')
  done
  printf '%s' "${value:0:length}"
}

get_setting() {
  # get_setting FILE KEY -> value (empty when missing)
  [[ -f "$1" ]] || return 0
  sed -n "s/^$2=//p" "$1" | tail -n1
}

set_setting() {
  # set_setting FILE KEY VALUE (replaces an existing assignment or appends)
  local file="$1" key="$2" value="$3" tmp
  tmp=$(mktemp "${file}.XXXXXX")
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$file" >"$tmp"
  else
    { cat "$file" 2>/dev/null; printf '%s=%s\n' "$key" "$value"; } >"$tmp"
  fi
  chmod --reference="$file" "$tmp" 2>/dev/null || chmod 0600 "$tmp"
  mv -f "$tmp" "$file"
}

write_compose_file() {
  cat >"$COMPOSE_FILE" <<'YAML'
# Generated by myportal-docker.sh; rewritten on every upgrade. Put local
# changes in docker-compose.override.yml, which Compose merges automatically.
name: myportal

services:
  db:
    image: ${DB_IMAGE}
    restart: unless-stopped
    env_file: mariadb.env
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
    volumes:
      - db_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 30s

  app:
    image: ${MYPORTAL_IMAGE}
    restart: unless-stopped
    env_file: myportal.env
    environment:
      DB_HOST: db
      DB_PORT: "3306"
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "${HTTP_BIND}:${HTTP_PORT}:8000"
    volumes:
      - private_uploads:/app/private_uploads
      - uploads:/app/app/static/uploads
      - state:/app/var

volumes:
  db_data:
  private_uploads:
  uploads:
  state:
YAML
  chmod 0644 "$COMPOSE_FILE"
}

write_initial_configuration() {
  local port="$1" bind="$2" db_password root_password
  db_password=$(random_secret 32)
  root_password=$(random_secret 32)

  cat >"$DB_ENV" <<EOF
MARIADB_ROOT_PASSWORD=${root_password}
MARIADB_DATABASE=myportal
MARIADB_USER=myportal
MARIADB_PASSWORD=${db_password}
EOF

  cat >"$APP_ENV" <<EOF
# MyPortal configuration. Settings documented in .env.example of the release
# (https://github.com/${MYPORTAL_REPO}/blob/main/.env.example) may be added
# here. Apply changes with: myportal-docker restart
#
# Keep a secure copy of this file: losing TOTP_ENCRYPTION_KEY makes stored
# two-factor secrets and encrypted integration credentials unrecoverable.

# Set to https://your.host once TLS terminates in front of MyPortal, and then
# ENVIRONMENT=production (Secure-only cookies, so only over HTTPS).
PORTAL_URL=
ENVIRONMENT=development

SESSION_SECRET=$(random_secret 64)
TOTP_ENCRYPTION_KEY=$(random_secret 64)
SMTP2GO_WEBHOOK_SECRET=$(random_secret 43)
MCP_TOKEN=$(random_secret 43)

DB_USER=myportal
DB_PASSWORD=${db_password}
DB_NAME=myportal

# Comma-separated reverse proxy addresses whose X-Forwarded-* headers are
# trusted (for example the Docker network of Traefik or nginx).
TRUSTED_PROXIES=

# Uvicorn worker processes.
WEB_CONCURRENCY=2
EOF

  cat >"$PROJECT_ENV" <<EOF
# Managed by myportal-docker.sh.
MYPORTAL_VERSION=
MYPORTAL_IMAGE=
DB_IMAGE=${MYPORTAL_DB_IMAGE}
HTTP_PORT=${port}
HTTP_BIND=${bind}
EOF
  chmod 0600 "$DB_ENV" "$APP_ENV" "$PROJECT_ENV"
}

install_script_copy() {
  # Keep an installed copy for later upgrades. When the script was piped into
  # bash there is no file to copy, so fetch the release's copy instead.
  local tag="$1" source="${BASH_SOURCE[0]:-}"
  if [[ -f "$source" && "$(readlink -f "$source")" != "$INSTALLED_SCRIPT" ]]; then
    install -m 0755 "$source" "$INSTALLED_SCRIPT"
  elif [[ ! -f "$source" ]]; then
    curl -fsSL "${MYPORTAL_GITHUB_URL}/${MYPORTAL_REPO}/releases/download/${tag}/${SCRIPT_ASSET}" \
      -o "${INSTALLED_SCRIPT}.new" && install -m 0755 "${INSTALLED_SCRIPT}.new" "$INSTALLED_SCRIPT" \
      || warn "could not install ${INSTALLED_SCRIPT}; keep this script to run upgrades."
    rm -f "${INSTALLED_SCRIPT}.new"
  fi
}

# ---------------------------------------------------------------------------
# Health, backup and restore
# ---------------------------------------------------------------------------
wait_for_release() {
  # Wait until /readyz reports "ok" for the expected release.
  local tag="$1" port waited=0 body=""
  port=$(get_setting "$PROJECT_ENV" HTTP_PORT)
  info "Waiting for MyPortal ${tag} to become ready (migrations run on first start)…"
  while ((waited < MYPORTAL_HEALTH_TIMEOUT)); do
    body=$(curl -fsS --max-time 10 "http://127.0.0.1:${port}/readyz" 2>/dev/null || true)
    if [[ "$body" == *'"status":"ok"'* && "$body" == *"\"version\":\"${tag}\""* ]]; then
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  warn "MyPortal ${tag} did not become ready within ${MYPORTAL_HEALTH_TIMEOUT}s (last response: ${body:-none})."
  compose logs --tail 40 app >&2 || true
  return 1
}

backup_database() {
  # backup_database LABEL -> prints the backup path
  local label="$1" file
  install -d -m 0700 "$BACKUP_DIR"
  file="${BACKUP_DIR}/db-${label}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  info "Backing up the database to ${file}…"
  compose exec -T db sh -c \
    'exec mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction --routines --triggers --events --add-drop-database --databases "$MARIADB_DATABASE"' \
    | gzip >"$file"
  [[ -s "$file" ]] || die "the database backup is empty; aborting."
  chmod 0600 "$file"
  printf '%s' "$file"
}

prune_backups() {
  local keep="$MYPORTAL_BACKUPS_TO_KEEP"
  [[ -d "$BACKUP_DIR" ]] || return 0
  # Newest first; remove everything after the first $keep of each kind.
  local kind
  for kind in db files; do
    find "$BACKUP_DIR" -maxdepth 1 -name "${kind}-*" -printf '%T@ %p\n' | sort -rn \
      | awk -v k="$keep" 'NR > k {print $2}' | xargs -r rm -f
  done
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_install() {
  local version="" port="80" bind="0.0.0.0"
  while (($#)); do
    case "$1" in
      --version) version="${2:-}"; shift 2 ;;
      --port) port="${2:-}"; shift 2 ;;
      --bind) bind="${2:-}"; shift 2 ;;
      *) die "unknown option for install: $1" ;;
    esac
  done
  require_root
  [[ "$port" =~ ^[0-9]+$ ]] && ((port > 0 && port < 65536)) || die "--port must be a TCP port number."
  if [[ -f "$PROJECT_ENV" && -n "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" ]]; then
    die "MyPortal is already installed in ${MYPORTAL_DIR}. Use 'myportal-docker upgrade' instead."
  fi
  command -v curl >/dev/null 2>&1 || {
    command -v apt-get >/dev/null 2>&1 && apt-get update -qq && apt-get install -y -qq curl >/dev/null
  } || die "curl is required."

  ensure_docker
  if command -v ss >/dev/null 2>&1 && ss -Hltn "sport = :${port}" | grep -q .; then
    die "port ${port} is already in use. Choose another with --port."
  fi

  [[ -n "$version" ]] || version=$(latest_release_tag)
  valid_tag "$version" || die "invalid release tag: ${version}"
  info "Installing MyPortal ${version} into ${MYPORTAL_DIR}."

  install -d -m 0750 "$MYPORTAL_DIR"
  [[ -f "$APP_ENV" ]] || write_initial_configuration "$port" "$bind"
  set_setting "$PROJECT_ENV" HTTP_PORT "$port"
  set_setting "$PROJECT_ENV" HTTP_BIND "$bind"
  write_compose_file

  local image
  image=$(obtain_image "$version")
  set_setting "$PROJECT_ENV" MYPORTAL_IMAGE "$image"

  compose up -d
  if ! wait_for_release "$version"; then
    die "the installation did not become healthy. Inspect it with 'docker compose --project-directory ${MYPORTAL_DIR} logs app', then rerun install."
  fi
  # Recorded only once healthy, so a failed attempt can simply be rerun.
  set_setting "$PROJECT_ENV" MYPORTAL_VERSION "$version"
  install_script_copy "$version"

  local host
  host=$(hostname -f 2>/dev/null || hostname)
  cat <<EOF

MyPortal ${version} is running.
  Portal:        http://${host}$([[ "$port" == 80 ]] || printf ':%s' "$port")/
  Configuration: ${APP_ENV}
  Management:    myportal-docker help

Open the portal and register: the first account becomes the super
administrator. Upgrade with 'sudo myportal-docker upgrade', or enable daily
automatic upgrades with 'sudo myportal-docker auto-upgrade on'.

Before exposing the portal to the internet, terminate TLS in front of it, then
set PORTAL_URL=https://… and ENVIRONMENT=production in ${APP_ENV} and run
'sudo myportal-docker restart'.
EOF
}

require_installed() {
  [[ -f "$PROJECT_ENV" && -f "$COMPOSE_FILE" ]] \
    || die "MyPortal is not installed in ${MYPORTAL_DIR}. Run 'install' first."
}

release_script_url() {
  printf '%s' "${MYPORTAL_GITHUB_URL}/${MYPORTAL_REPO}/releases/download/$1/${SCRIPT_ASSET}"
}

release_published() {
  # The release workflow attaches this script only after the release's images
  # are published, so its presence means the release is ready to install.
  # A one-byte GET: the asset host rejects HEAD requests.
  curl -fsSL -r 0-0 -o /dev/null "$(release_script_url "$1")" 2>/dev/null
}

fetch_release_script() {
  # fetch_release_script TAG FILE: download the script published with TAG and
  # check that it is a valid bash script.
  curl -fsSL "$(release_script_url "$1")" -o "$2" 2>/dev/null \
    && head -n1 "$2" | grep -q '^#!' && bash -n "$2" 2>/dev/null
}

self_update() {
  # Re-run this command with the script published alongside the target
  # release, so compose changes that ship with a release are applied too.
  local tag="$1"; shift
  [[ -z "${MYPORTAL_SELF_UPDATED:-}" ]] || return 0
  local candidate
  candidate=$(mktemp)
  if fetch_release_script "$tag" "$candidate"; then
    if ! cmp -s "$candidate" "$INSTALLED_SCRIPT"; then
      install -m 0755 "$candidate" "$INSTALLED_SCRIPT"
      rm -f "$candidate"
      info "Updated ${INSTALLED_SCRIPT} to the version published with ${tag}."
      MYPORTAL_SELF_UPDATED=1 exec "$INSTALLED_SCRIPT" "$@"
    fi
  fi
  rm -f "$candidate"
}

cmd_upgrade() {
  local version="" explicit=false assume_yes=false
  local -a original_args=("$@")
  while (($#)); do
    case "$1" in
      --version) version="${2:-}"; explicit=true; shift 2 ;;
      --yes|-y) assume_yes=true; shift ;;
      *) die "unknown option for upgrade: $1" ;;
    esac
  done
  require_root
  require_installed
  ensure_docker

  local current
  current=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  [[ -n "$version" ]] || version=$(latest_release_tag)
  valid_tag "$version" || die "invalid release tag: ${version}"

  if [[ "$version" == "$current" ]]; then
    # An earlier upgrade may have run before this release's script was
    # attached; pick it up now so its commands are available.
    self_update "$version" upgrade "${original_args[@]}"
    info "MyPortal ${current} is already the latest release."
    return 0
  fi
  if [[ "$explicit" != true ]] && ! version_newer "$version" "$current"; then
    info "The installed release ${current} is newer than the latest published release ${version}; nothing to do."
    return 0
  fi
  if [[ "$explicit" != true && "$version" == "$(get_setting "$PROJECT_ENV" FAILED_VERSION)" ]]; then
    # Do not take the portal down every night for a release that already
    # failed its health check here.
    warn "release ${version} failed to start on this host before; skipping it. Retry with: myportal-docker upgrade --version ${version}"
    return 0
  fi
  if [[ "$explicit" != true ]] && ! release_published "$version"; then
    # Upgrading before the images are published would build the release
    # locally and leave this script out of date.
    info "Release ${version} is still being published; try again in a few minutes."
    return 0
  fi
  self_update "$version" upgrade "${original_args[@]}"

  if [[ "$assume_yes" != true && -t 0 ]]; then
    local answer
    read -r -p "Upgrade MyPortal from ${current} to ${version}? [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || die "upgrade cancelled."
  fi

  info "Upgrading MyPortal ${current} -> ${version}."
  local image previous_image backup
  image=$(obtain_image "$version")        # before any downtime
  previous_image=$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)
  write_compose_file
  compose up -d db
  backup=$(backup_database "before-${version}")

  set_setting "$PROJECT_ENV" MYPORTAL_IMAGE "$image"
  set_setting "$PROJECT_ENV" MYPORTAL_VERSION "$version"
  compose up -d
  if wait_for_release "$version"; then
    set_setting "$PROJECT_ENV" FAILED_VERSION ""
    prune_backups
    info "MyPortal ${version} is running. Database backup: ${backup}"
    return 0
  fi

  warn "rolling back to ${current}."
  set_setting "$PROJECT_ENV" FAILED_VERSION "$version"
  set_setting "$PROJECT_ENV" MYPORTAL_IMAGE "$previous_image"
  set_setting "$PROJECT_ENV" MYPORTAL_VERSION "$current"
  compose up -d
  if wait_for_release "$current"; then
    die "the upgrade to ${version} failed and ${current} was restored. The database was backed up before the upgrade to ${backup}; if ${current} misbehaves after the partial migration, restore it with 'myportal-docker restore-db ${backup}'."
  fi
  die "the upgrade to ${version} failed and ${current} did not recover. Restore the database with 'myportal-docker restore-db ${backup}'."
}

cmd_self_update() {
  require_root
  require_installed
  local tag candidate
  tag=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  candidate=$(mktemp)
  if ! fetch_release_script "$tag" "$candidate"; then
    rm -f "$candidate"
    die "could not download ${SCRIPT_ASSET} for release ${tag}. It is attached to a release a few minutes after publication; try again shortly."
  fi
  if cmp -s "$candidate" "$INSTALLED_SCRIPT"; then
    info "${INSTALLED_SCRIPT} is already the version published with ${tag}."
  else
    install -m 0755 "$candidate" "$INSTALLED_SCRIPT"
    info "Updated ${INSTALLED_SCRIPT} to the version published with ${tag}."
  fi
  rm -f "$candidate"
}

cmd_check() {
  require_installed
  local current latest
  current=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  latest=$(latest_release_tag)
  printf 'Installed release: %s\nLatest release:    %s\n' "$current" "$latest"
  if version_newer "$latest" "$current"; then
    printf 'An upgrade is available: sudo myportal-docker upgrade\n'
    return 10
  fi
  printf 'MyPortal is up to date.\n'
}

cmd_status() {
  require_installed
  printf 'Installed release: %s\nImage:             %s\n\n' \
    "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" "$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)"
  compose ps
  local port
  port=$(get_setting "$PROJECT_ENV" HTTP_PORT)
  printf '\nReadiness: %s\n' "$(curl -fsS --max-time 10 "http://127.0.0.1:${port}/readyz" 2>&1 || true)"
}

cmd_backup() {
  require_root
  require_installed
  local db files version
  version=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  db=$(backup_database "$version")
  files="${BACKUP_DIR}/files-${version}-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  info "Backing up uploaded files to ${files}…"
  compose exec -T app tar -czf - -C /app private_uploads app/static/uploads var >"$files"
  chmod 0600 "$files"
  prune_backups
  cat <<EOF
Backups written:
  ${db}
  ${files}
Also keep a copy of ${APP_ENV} (it holds the encryption keys).
EOF
}

cmd_restore_db() {
  local file="${1:-}"
  require_root
  require_installed
  [[ -f "$file" ]] || die "usage: myportal-docker restore-db BACKUP.sql.gz"
  info "Stopping MyPortal and restoring ${file}…"
  compose stop app
  compose up -d db
  local waited=0
  until compose exec -T db healthcheck.sh --connect >/dev/null 2>&1; do
    ((waited < 120)) || die "the database did not start."
    sleep 2; waited=$((waited + 2))
  done
  gunzip -c "$file" | compose exec -T db sh -c 'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
  compose up -d app
  wait_for_release "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" || die "MyPortal did not become ready after the restore."
  info "Database restored."
}

cmd_restart() {
  require_root
  require_installed
  compose up -d --force-recreate app
  wait_for_release "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" || die "MyPortal did not become ready."
}

cmd_setup() {
  require_root
  require_installed
  local image
  image=$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)
  [[ -n "$image" ]] || die "no MyPortal image is recorded in ${PROJECT_ENV}."
  docker run --rm "$image" test -f /app/scripts/onboarding_wizard.py >/dev/null 2>&1 \
    || die "the installed release has no onboarding wizard. Run 'myportal-docker upgrade' first."
  local -a tty=(-i)
  [[ -t 0 && -t 1 ]] && tty=(-it)
  # Runs as root so it can rewrite the root-owned myportal.env; the directory
  # is mounted (not the file) so the file can be replaced atomically.
  docker run --rm "${tty[@]}" --user 0:0 --entrypoint python \
    -v "${MYPORTAL_DIR}:/config" "$image" \
    /app/scripts/onboarding_wizard.py --docker --env-file /config/myportal.env "$@"
}

cmd_auto_upgrade() {
  require_root
  require_installed
  case "${1:-}" in
    on)
      [[ -x "$INSTALLED_SCRIPT" ]] || die "${INSTALLED_SCRIPT} is missing; reinstall it with 'bash myportal-docker.sh install' or copy this script there."
      local minute hour
      minute=$((RANDOM % 60)); hour=$((2 + RANDOM % 3))
      cat >"$AUTO_UPGRADE_CRON" <<EOF
# Installed by myportal-docker: apply new MyPortal releases daily.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${minute} ${hour} * * * root MYPORTAL_DIR=${MYPORTAL_DIR} ${INSTALLED_SCRIPT} upgrade --yes >> ${MYPORTAL_DIR}/auto-upgrade.log 2>&1
EOF
      chmod 0644 "$AUTO_UPGRADE_CRON"
      if ! command -v cron >/dev/null 2>&1 && ! command -v crond >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then
          DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cron >/dev/null 2>&1
        elif command -v dnf >/dev/null 2>&1; then
          dnf -y -q install cronie >/dev/null
        fi
      fi
      if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        systemctl enable --now cron >/dev/null 2>&1 || systemctl enable --now crond >/dev/null 2>&1 || true
      fi
      info "Automatic upgrades enabled (daily at $(printf '%02d:%02d' "$hour" "$minute"); log: ${MYPORTAL_DIR}/auto-upgrade.log)."
      ;;
    off)
      rm -f "$AUTO_UPGRADE_CRON"
      info "Automatic upgrades disabled."
      ;;
    *) die "usage: myportal-docker auto-upgrade on|off" ;;
  esac
}

# ---------------------------------------------------------------------------
# Super administrators
# ---------------------------------------------------------------------------
MIN_PASSWORD_LENGTH=12   # the portal's own password policy
MAX_PASSWORD_LENGTH=128

db_sql() {
  # Run the SQL read from stdin against the MyPortal database; prints
  # tab-separated rows without a header.
  compose exec -T db sh -c \
    'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" --batch --skip-column-names "$MARIADB_DATABASE"'
}

sql_string() {
  # Quote an arbitrary value for SQL without escaping concerns: it is passed
  # hex-encoded and compared with the users table's collation.
  printf "CONVERT(UNHEX('%s') USING utf8mb4) COLLATE utf8mb4_unicode_ci" \
    "$(printf '%s' "$1" | od -An -v -tx1 | tr -d ' \n')"
}

sql_string_or_null() {
  if [[ -n "$1" ]]; then sql_string "$1"; else printf 'NULL'; fi
}

find_user() {
  # find_user USERNAME -> "id<TAB>email<TAB>is_super_admin<TAB>is_active"
  db_sql <<SQL
SELECT id, email, is_super_admin, is_active FROM users WHERE email = $(sql_string "$1") LIMIT 1;
SQL
}

audit_cli_action() {
  # audit_cli_action ACTION USER_ID: record the change in the portal's audit log.
  db_sql >/dev/null <<SQL || warn "could not record the change in the audit log."
INSERT INTO audit_logs (action, entity_type, entity_id, metadata, created_at)
VALUES ('$1', 'user', $2, JSON_OBJECT('source', 'myportal-docker'), UTC_TIMESTAMP());
SQL
}

read_new_password() {
  # read_new_password GENERATE -> prints the password. Prompts on a terminal,
  # otherwise reads one line from standard input.
  local generate="$1" password confirm
  if [[ "$generate" == true ]]; then
    random_secret 20
    return 0
  fi
  if [[ -t 0 ]]; then
    read -r -s -p "New password: " password </dev/tty; printf '\n' >&2
    read -r -s -p "Repeat password: " confirm </dev/tty; printf '\n' >&2
    [[ "$password" == "$confirm" ]] || die "the passwords do not match."
  else
    IFS= read -r password || [[ -n "$password" ]] || die "no password on standard input (or use --generate-password)."
  fi
  ((${#password} >= MIN_PASSWORD_LENGTH)) || die "the password must be at least ${MIN_PASSWORD_LENGTH} characters."
  ((${#password} <= MAX_PASSWORD_LENGTH)) || die "the password must be at most ${MAX_PASSWORD_LENGTH} characters."
  printf '%s' "$password"
}

hash_password() {
  # Hash the password read from stdin with the portal's own password hashing,
  # so the stored format always matches the running release.
  local program='import sys; from app.security.passwords import hash_password; sys.stdout.write(hash_password(sys.stdin.read()))'
  local hash
  if compose exec -T app true >/dev/null 2>&1; then
    hash=$(compose exec -T app python -c "$program")
  else
    hash=$(compose run --rm --no-deps -T app python -c "$program" 2>/dev/null)
  fi
  # Only the portal's PBKDF2 format is accepted; it also makes the value safe
  # to place in SQL.
  [[ "$hash" =~ ^pbkdf2_sha256\$[0-9]+\$[A-Za-z0-9_-]+\$[A-Za-z0-9_-]+$ ]] \
    || die "could not hash the password with the MyPortal image."
  printf '%s' "$hash"
}

superadmin_usage() {
  die "usage: myportal-docker superadmin list
       myportal-docker superadmin grant USERNAME
       myportal-docker superadmin revoke USERNAME [--force]
       myportal-docker superadmin create USERNAME [--first-name NAME] [--last-name NAME] [--generate-password]
       myportal-docker superadmin reset-password USERNAME [--generate-password] [--reset-2fa]"
}

cmd_superadmin() {
  local action="${1:-}"
  (($#)) && shift
  local username="" force=false generate=false reset_2fa=false first_name="" last_name=""
  while (($#)); do
    case "$1" in
      --force) force=true; shift ;;
      --generate-password) generate=true; shift ;;
      --reset-2fa) reset_2fa=true; shift ;;
      --first-name) (($# >= 2)) || die "--first-name needs a value."; first_name="$2"; shift 2 ;;
      --last-name) (($# >= 2)) || die "--last-name needs a value."; last_name="$2"; shift 2 ;;
      -*) die "unknown option for superadmin: $1" ;;
      *) [[ -z "$username" ]] || die "superadmin takes a single USERNAME."; username="$1"; shift ;;
    esac
  done
  case "$action" in
    list) [[ -z "$username" ]] || superadmin_usage ;;
    grant|revoke|create|reset-password) [[ -n "$username" ]] || superadmin_usage ;;
    *) superadmin_usage ;;
  esac
  require_root
  require_installed
  compose up -d db >/dev/null 2>&1 || true
  local db_error="could not query the database. Is MyPortal running? Check with 'myportal-docker status'."

  if [[ "$action" == list ]]; then
    local rows
    rows=$(db_sql <<'SQL'
-- Blank names become "-" so the tab-separated columns stay aligned.
SELECT id, email, COALESCE(NULLIF(TRIM(CONCAT(COALESCE(first_name, ''), ' ', COALESCE(last_name, ''))), ''), '-'),
       IF(is_active = 1, 'yes', 'no')
FROM users WHERE is_super_admin = 1 ORDER BY LOWER(email), id;
SQL
    ) || die "$db_error"
    if [[ -z "$rows" ]]; then
      printf 'No users have super administrator rights.\n'
      return 0
    fi
    printf '%-6s  %-40s  %-30s  %s\n' ID USERNAME NAME ACTIVE
    local id email name active
    while IFS=$'\t' read -r id email name active; do
      printf '%-6s  %-40s  %-30s  %s\n' "$id" "$email" "$name" "$active"
    done <<<"$rows"
    return 0
  fi

  local record id email is_super is_active
  record=$(find_user "$username") || die "$db_error"

  if [[ "$action" == create ]]; then
    [[ -z "$record" ]] \
      || die "a user with username '${username}' already exists. Use 'superadmin grant' or 'superadmin reset-password' instead."
    [[ "$username" =~ ^[^[:space:]@]+@[^[:space:]@]+$ && ${#username} -le 255 ]] \
      || die "USERNAME must be the email address the user will sign in with."
    local password hash
    password=$(read_new_password "$generate")
    hash=$(printf '%s' "$password" | hash_password)
    # users.company_id is mandatory: use the first company, as the portal does
    # for the first registered account, creating one when none exists.
    id=$(db_sql <<SQL
SET @company = (SELECT MIN(id) FROM companies);
INSERT INTO companies (name) SELECT 'Default Company' FROM DUAL WHERE @company IS NULL;
SET @company = COALESCE(@company, LAST_INSERT_ID());
INSERT INTO users (email, password_hash, first_name, last_name, company_id, is_super_admin, is_active, email_verified_at)
VALUES ($(sql_string "$username"), '${hash}', $(sql_string_or_null "$first_name"), $(sql_string_or_null "$last_name"),
        @company, 1, 1, UTC_TIMESTAMP());
SELECT LAST_INSERT_ID();
SQL
    ) || die "could not create the user."
    audit_cli_action cli.superadmin.create "$id"
    info "Created super administrator ${username}."
    [[ "$generate" != true ]] || printf 'Password: %s\n' "$password"
    return 0
  fi

  [[ -n "$record" ]] || die "no user with username '${username}'. List users with super administrator rights with 'myportal-docker superadmin list'."
  IFS=$'\t' read -r id email is_super is_active <<<"$record"

  case "$action" in
    grant)
      if [[ "$is_super" == 1 ]]; then
        info "${email} already has super administrator rights."
        return 0
      fi
      db_sql <<<"UPDATE users SET is_super_admin = 1 WHERE id = ${id};" >/dev/null
      audit_cli_action cli.superadmin.grant "$id"
      info "Granted super administrator rights to ${email}."
      [[ "$is_active" == 1 ]] || warn "${email} is deactivated and cannot sign in until it is reactivated."
      ;;
    revoke)
      if [[ "$is_super" != 1 ]]; then
        info "${email} does not have super administrator rights."
        return 0
      fi
      local others
      others=$(db_sql <<<"SELECT COUNT(*) FROM users WHERE is_super_admin = 1 AND is_active = 1 AND id <> ${id};")
      if [[ "$others" == 0 && "$force" != true ]]; then
        die "${email} is the last active super administrator; revoking would leave nobody able to administer MyPortal. Grant another user first, or rerun with --force."
      fi
      db_sql <<<"UPDATE users SET is_super_admin = 0 WHERE id = ${id};" >/dev/null
      audit_cli_action cli.superadmin.revoke "$id"
      info "Revoked super administrator rights from ${email}."
      ;;
    reset-password)
      [[ "$is_super" == 1 ]] \
        || die "${email} is not a super administrator; this command only resets super administrator passwords. Reset other users' passwords in the portal."
      local password hash
      password=$(read_new_password "$generate")
      hash=$(printf '%s' "$password" | hash_password)
      # Signing the user out everywhere ends any session opened with the old
      # password.
      db_sql >/dev/null <<SQL
UPDATE users SET password_hash = '${hash}', force_password_change = 0 WHERE id = ${id};
UPDATE user_sessions SET is_active = 0 WHERE user_id = ${id};
SQL
      audit_cli_action cli.superadmin.reset_password "$id"
      info "Reset the password of ${email} and signed it out of all sessions."
      if [[ "$reset_2fa" == true ]]; then
        db_sql >/dev/null <<SQL
DELETE FROM user_totp_authenticators WHERE user_id = ${id};
DELETE FROM user_passkeys WHERE user_id = ${id};
SQL
        audit_cli_action cli.superadmin.reset_2fa "$id"
        info "Removed the authenticator apps and passkeys of ${email}; two-factor sign-in is set up again at the next sign-in."
      fi
      [[ "$is_active" == 1 ]] || warn "${email} is deactivated and cannot sign in until it is reactivated."
      [[ "$generate" != true ]] || printf 'Password: %s\n' "$password"
      ;;
  esac
}

cmd_user() {
  local action="${1:-}"
  (($#)) && shift
  [[ "$action" == verify && $# -eq 1 && "$1" != -* ]] || die "usage: myportal-docker user verify USERNAME"
  local username="$1"
  require_root
  require_installed
  compose up -d db >/dev/null 2>&1 || true

  local record id email is_active verified
  record=$(db_sql <<SQL
SELECT id, email, is_active, email_verified_at IS NOT NULL FROM users WHERE email = $(sql_string "$username") LIMIT 1;
SQL
  ) || die "could not query the database. Is MyPortal running? Check with 'myportal-docker status'."
  [[ -n "$record" ]] || die "no user with username '${username}'."
  IFS=$'\t' read -r id email is_active verified <<<"$record"

  if [[ "$verified" == 1 ]]; then
    info "${email} is already verified."
    # A verified but inactive account was deactivated on purpose; verifying
    # must not re-enable it.
    [[ "$is_active" == 1 ]] || warn "${email} is deactivated; reactivate it in the portal if it should sign in."
    return 0
  fi
  # The same changes the emailed verification link makes.
  db_sql >/dev/null <<SQL
UPDATE users SET is_active = 1, email_verified_at = UTC_TIMESTAMP() WHERE id = ${id};
UPDATE account_verification_tokens SET used = 1 WHERE user_id = ${id};
SQL
  audit_cli_action cli.user.verify "$id"
  info "Verified ${email}; the account can now sign in."
}

main() {
  local command="${1:-help}"
  (($#)) && shift
  case "$command" in
    install) cmd_install "$@" ;;
    upgrade|update) cmd_upgrade "$@" ;;
    check) cmd_check ;;
    self-update) cmd_self_update ;;
    status) cmd_status ;;
    logs)
      require_installed
      if (($#)); then compose logs "$@" app; else compose logs --follow app; fi
      ;;
    backup) cmd_backup ;;
    restore-db) cmd_restore_db "$@" ;;
    restart) cmd_restart ;;
    setup|onboard) cmd_setup "$@" ;;
    auto-upgrade) cmd_auto_upgrade "$@" ;;
    superadmin|super-admin) cmd_superadmin "$@" ;;
    user) cmd_user "$@" ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
