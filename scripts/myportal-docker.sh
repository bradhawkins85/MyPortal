#!/usr/bin/env bash
# MyPortal Docker installer and upgrader.
#
# A single, self-contained script: download it and run it; no git clone needed.
#
#   curl -fsSLO https://github.com/bradhawkins85/MyPortal/releases/latest/download/myportal-docker.sh
#   sudo bash myportal-docker.sh install
#
# It installs Docker Engine and the Compose plugin when they are missing,
# writes a docker-compose.yml (MyPortal + MariaDB + Redis + Gitea for RMM
# scripts) with generated secrets, and
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
MYPORTAL_REDIS_IMAGE="${MYPORTAL_REDIS_IMAGE:-redis:7-alpine}"
# Gitea holds the RMM script library and is served at /gitea. Applied on every
# install and upgrade, so a release that raises it upgrades Gitea too.
MYPORTAL_GITEA_IMAGE="${MYPORTAL_GITEA_IMAGE:-gitea/gitea:1.24.6}"
# Optional: base image for local builds, and a CA bundle for networks that
# inspect TLS (passed to the build as a secret, never stored in the image).
MYPORTAL_BASE_IMAGE="${MYPORTAL_BASE_IMAGE:-}"
MYPORTAL_BUILD_CA_FILE="${MYPORTAL_BUILD_CA_FILE:-}"
# Optional network for local builds, e.g. "host" when the only proxy listens
# on localhost.
MYPORTAL_BUILD_NETWORK="${MYPORTAL_BUILD_NETWORK:-}"
MYPORTAL_HEALTH_TIMEOUT="${MYPORTAL_HEALTH_TIMEOUT:-600}"
MYPORTAL_BACKUPS_TO_KEEP="${MYPORTAL_BACKUPS_TO_KEEP:-10}"
# The nginx image that fronts the blue and green application containers. It
# needs nginx 1.27.3 or newer (dynamic "resolve" upstream servers).
MYPORTAL_PROXY_IMAGE="${MYPORTAL_PROXY_IMAGE:-nginx:1.28-alpine}"
# Seconds the previous slot keeps running after cutover, so requests it is
# still serving can finish.
MYPORTAL_DRAIN_SECONDS="${MYPORTAL_DRAIN_SECONDS:-10}"

INSTALLED_SCRIPT="/usr/local/bin/myportal-docker"
AUTO_UPGRADE_CRON="/etc/cron.d/myportal-docker-upgrade"
WEB_UPGRADE_CRON="/etc/cron.d/myportal-docker-requests"
# Where the portal queues upgrade requests, inside the app container's state
# volume. The host only reaches it with "docker exec" in the serving container.
CONTAINER_UPDATE_FLAG="/app/var/state/system_update.flag"
SCRIPT_ASSET="myportal-docker.sh"

COMPOSE_FILE="${MYPORTAL_DIR}/docker-compose.yml"
PROJECT_ENV="${MYPORTAL_DIR}/.env"            # compose variables (versions, port)
APP_ENV="${MYPORTAL_DIR}/myportal.env"        # application configuration
DB_ENV="${MYPORTAL_DIR}/mariadb.env"          # database container credentials
GITEA_ENV="${MYPORTAL_DIR}/gitea.env"         # Gitea secrets
GITEA_ADMIN_FILE="${MYPORTAL_DIR}/gitea-admin.txt"  # Gitea administrator sign-in
BACKUP_DIR="${MYPORTAL_DIR}/backups"
PROXY_DIR="${MYPORTAL_DIR}/proxy"             # nginx configuration (generated)
# Matches "name:" in the compose file; used to find containers by label.
COMPOSE_PROJECT="myportal"

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
        Upgrade to the latest GitHub release (or TAG) without downtime. Backs
        up the database, starts the release in the idle blue/green slot and
        switches the proxy to it only once it is healthy.
  rollback [--yes]     Switch back to the previous release, which is kept in
                       the idle slot after an upgrade.
  check                Report the installed and latest release. Exits 0 when
                       up to date and 10 when an upgrade is available.
  status               Show the containers and the running release.
  self-update          Reinstall this script from the installed release, to
                       get commands that release added.
  logs [ARGS...]       Follow the application logs (docker compose logs ARGS).
  backup               Back up the database and uploaded files.
  restore-db FILE      Restore a database backup (.sql.gz) made by this script.
  restart              Restart MyPortal without downtime (applies changes to
                       myportal.env).
  setup [--check | --list | --feature NAME]
        Run the onboarding wizard: choose which feature packs and modules are
        enabled and configure their settings in myportal.env. Re-run it at any
        time to review or correct settings; --check only reports missing or
        invalid values. Disabling a feature keeps its settings.
  auto-upgrade on|off  Check for and apply new releases daily.
  web-upgrades on|off|status
        Allow super administrators to start upgrades from the portal's System
        updates page (on by default). A root cron job checks for requests every
        minute and runs 'upgrade --yes'; it always upgrades to the latest
        published release and takes nothing else from the portal.
  process-requests     Apply a pending upgrade requested from the portal (run
                       by the web-upgrades cron job).
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
    install_compose_binary /usr/local/lib/docker/cli-plugins "$(uname -m)"
  fi
}

# Download the Compose v2 plugin binary from Docker's latest GitHub release and
# install it only if it matches the .sha256 file published with that release.
install_compose_binary() {
  local plugin_dir="$1" arch="$2" releases="https://github.com/docker/compose/releases"
  local latest tag asset workdir expected actual
  # Resolve "latest" once so the binary and its checksum come from one release.
  latest=$(curl -fsSL -o /dev/null -w '%{url_effective}' "${releases}/latest") \
    || die "Could not resolve the latest Docker Compose release."
  tag=${latest##*/}
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Unexpected Docker Compose release tag: ${tag}"
  [[ "$arch" =~ ^[A-Za-z0-9_]+$ ]] || die "Unsupported architecture: ${arch}"
  asset="docker-compose-linux-${arch}"
  workdir=$(mktemp -d)
  if ! curl -fsSL "${releases}/download/${tag}/${asset}" -o "${workdir}/${asset}" \
    || ! curl -fsSL "${releases}/download/${tag}/${asset}.sha256" -o "${workdir}/${asset}.sha256"; then
    rm -rf "$workdir"
    die "Could not download Docker Compose ${tag} (${asset})."
  fi
  expected=$(awk 'NR == 1 { print tolower($1) }' "${workdir}/${asset}.sha256")
  actual=$(sha256sum "${workdir}/${asset}" | awk '{ print $1 }')
  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$expected" != "$actual" ]]; then
    rm -rf "$workdir"
    die "Checksum verification failed for Docker Compose ${tag} (${asset}); not installing it."
  fi
  install -d -m 0755 "$plugin_dir"
  install -m 0755 "${workdir}/${asset}" "${plugin_dir}/docker-compose"
  rm -rf "$workdir"
  info "Installed Docker Compose ${tag} (sha256 ${actual})."
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

redis_connection_url() {
  awk '
    /^[[:space:]]*(export[[:space:]]+)?REDIS_URL[[:space:]]*=/ {
      sub(/^[[:space:]]*(export[[:space:]]+)?REDIS_URL[[:space:]]*=[[:space:]]*/, "")
      sub(/[[:space:]]+#.*/, "")
      gsub(/^[[:space:]\042\047]+|[[:space:]\042\047]+$/, "")
      value=$0
    }
    END {print value}
  ' "$APP_ENV"
}

ensure_redis_settings() {
  # Read only REDIS_URL; never source an application env file as shell code.
  local url
  url=$(redis_connection_url) || return 1
  if [[ -z "$url" ]]; then
    local tmp
    tmp=$(mktemp "${APP_ENV}.XXXXXX") || return 1
    awk '!/^[[:space:]]*(export[[:space:]]+)?REDIS_URL[[:space:]]*=/' "$APP_ENV" >"$tmp"
    printf '\nREDIS_URL=redis://redis:6379/0\n' >>"$tmp"
    chmod --reference="$APP_ENV" "$tmp"
    mv -f "$tmp" "$APP_ENV" || return 1
  fi
  [[ -n "$(get_setting "$PROJECT_ENV" REDIS_IMAGE)" ]] \
    || set_setting "$PROJECT_ENV" REDIS_IMAGE "$MYPORTAL_REDIS_IMAGE"
}

ensure_local_redis() {
  # Explicitly targeting this profiled service leaves external Redis setups
  # alone. --no-recreate preserves an existing container and its configuration.
  local url
  url=$(redis_connection_url) || return 1
  case "$url" in
    redis://redis:*|redis://redis/*|redis://redis)
      local container pull_policy=missing
      container=$(compose ps -a -q redis) || return 1
      [[ -z "$container" ]] || pull_policy=never
      compose up -d --no-recreate --pull "$pull_policy" --wait redis >&2
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Gitea (RMM script library)
#
# A Gitea container holds the scripts technicians run from MyPortal's Scripts
# page; the proxy serves it at /gitea. The first deploy creates an
# administrator, a private "rmm-scripts" repository and a repository token, and
# writes the GITEA_* settings into myportal.env. Installations with
# GITEA_PROVISION=false, or whose GITEA_BASE_URL names another server, are
# left alone.
# ---------------------------------------------------------------------------
GITEA_INTERNAL_URL="http://gitea:3000"
GITEA_ADMIN_USER="myportal"
GITEA_ADMIN_EMAIL="scripts@myportal.localhost"
GITEA_REPOSITORY="rmm-scripts"

gitea_managed() {
  local provision base_url
  provision=$(get_setting "$APP_ENV" GITEA_PROVISION | tr -d "\"'" | tr '[:upper:]' '[:lower:]')
  case "$provision" in false|0|no|off) return 1 ;; esac
  base_url=$(get_setting "$APP_ENV" GITEA_BASE_URL | tr -d "\"'")
  [[ -z "$base_url" || "${base_url%/}" == "$GITEA_INTERNAL_URL" ]]
}

gitea_needs_setup() {
  # True while a managed Gitea has not been connected to MyPortal yet.
  gitea_managed && [[ -z "$(get_setting "$APP_ENV" GITEA_API_TOKEN)" ]]
}

gitea_root_url() {
  # Gitea's own address: the portal's public address plus /gitea/.
  local portal port
  portal=$(get_setting "$APP_ENV" PORTAL_URL | tr -d "\"'")
  if [[ "$portal" =~ ^https?://[^/]+ ]]; then
    printf '%s/gitea/' "${BASH_REMATCH[0]}"
    return
  fi
  port=$(get_setting "$PROJECT_ENV" HTTP_PORT)
  printf 'http://%s%s/gitea/' "$(hostname -f 2>/dev/null || hostname)" "$([[ -z "$port" || "$port" == 80 ]] || printf ':%s' "$port")"
}

jwt_secret() {
  # 32 random bytes, unpadded base64url, as Gitea's [oauth2] JWT_SECRET expects.
  head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'
}

ensure_gitea_settings() {
  # The compose file names gitea.env and these variables whether or not the
  # service runs, so they always exist. Secrets are generated once.
  if [[ ! -s "$GITEA_ENV" ]]; then
    ( umask 077
      printf 'GITEA__security__SECRET_KEY=%s\nGITEA__security__INTERNAL_TOKEN=%s\nGITEA__oauth2__JWT_SECRET=%s\n' \
        "$(random_secret 64)" "$(random_secret 64)" "$(jwt_secret)" >"$GITEA_ENV" ) || return 1
  fi
  chmod 0600 "$GITEA_ENV"
  set_setting "$PROJECT_ENV" GITEA_IMAGE "$MYPORTAL_GITEA_IMAGE" || return 1
  set_setting "$PROJECT_ENV" GITEA_ROOT_URL "$(gitea_root_url)"
}

gitea_cli() {
  compose exec -T -u git gitea gitea "$@"
}

gitea_api() {
  # gitea_api METHOD PATH [JSON]: prints the HTTP status. Credentials arrive
  # on stdin as a curl config, so they never appear in process arguments.
  local method="$1" path="$2" body="${3:-}"
  local -a args=(-sS -o /dev/null -w '%{http_code}' -X "$method" -K -)
  [[ -z "$body" ]] || args+=(-H 'Content-Type: application/json' --data "$body")
  compose exec -T gitea curl "${args[@]}" "http://127.0.0.1:3000/api/v1${path}" || printf '000'
}

gitea_auth() {
  # Curl config lines: a token header, or the administrator's password.
  if [[ -n "${1:-}" ]]; then
    printf 'header = "Authorization: token %s"\n' "$1"
  else
    local password
    password=$(sed -n 's/^password=//p' "$GITEA_ADMIN_FILE" 2>/dev/null | head -n1)
    password=${password//\\/\\\\}
    printf 'user = "%s:%s"\n' "$GITEA_ADMIN_USER" "${password//\"/\\\"}"
  fi
}

ensure_gitea_admin() {
  local output password
  # Captured first: "grep -q" exiting early would fail the pipeline (pipefail).
  output=$(gitea_cli admin user list --admin 2>/dev/null || true)
  if awk 'NR > 1 {print $2}' <<<"$output" | grep -x "$GITEA_ADMIN_USER" >/dev/null; then
    return 0
  fi
  info "Creating the Gitea administrator '${GITEA_ADMIN_USER}'…"
  output=$(gitea_cli admin user create --admin --username "$GITEA_ADMIN_USER" --email "$GITEA_ADMIN_EMAIL" \
    --random-password --must-change-password=false) || return 1
  password=$(printf '%s\n' "$output" | sed -n "s/.*generated random password is '\(.*\)'.*/\1/p" | head -n1)
  [[ -n "$password" ]] || { warn "Gitea did not report the administrator's password."; return 1; }
  ( umask 077; printf 'url=%s\nusername=%s\npassword=%s\n' "$(gitea_root_url)" "$GITEA_ADMIN_USER" "$password" >"$GITEA_ADMIN_FILE" )
  chmod 0600 "$GITEA_ADMIN_FILE"
}

ensure_gitea_repository() {
  local token="$1" status
  status=$(gitea_auth "$token" | gitea_api GET "/repos/${GITEA_ADMIN_USER}/${GITEA_REPOSITORY}")
  [[ "$status" != 200 ]] || return 0
  info "Creating the Gitea repository ${GITEA_ADMIN_USER}/${GITEA_REPOSITORY}…"
  status=$(gitea_auth | gitea_api POST /user/repos \
    "{\"name\":\"${GITEA_REPOSITORY}\",\"description\":\"Scripts MyPortal runs on devices\",\"private\":true,\"auto_init\":true,\"default_branch\":\"main\",\"readme\":\"Default\"}")
  [[ "$status" == 201 || "$status" == 409 ]] || {
    warn "could not create ${GITEA_ADMIN_USER}/${GITEA_REPOSITORY} in Gitea (HTTP ${status}); create it, then sync scripts."
    return 1
  }
}

connect_gitea() {
  # Gives MyPortal a token (it reads scripts and creates the script folders)
  # and the repository, once.
  local token key value
  token=$(get_setting "$APP_ENV" GITEA_API_TOKEN)
  if [[ -z "$token" ]]; then
    ensure_gitea_admin || return 1
    token=$(gitea_cli admin user generate-access-token --username "$GITEA_ADMIN_USER" \
      --token-name "myportal-$(date -u +%Y%m%d%H%M%S)" --scopes write:repository | grep -oE '[0-9a-f]{40}' | tail -n1)
    [[ -n "$token" ]] || { warn "could not create a Gitea token for MyPortal."; return 1; }
    ensure_gitea_repository "$token" || true
    set_setting "$APP_ENV" GITEA_API_TOKEN "$token" || return 1
  fi
  for key in GITEA_BASE_URL GITEA_PUBLIC_URL GITEA_SCRIPTS_REPOSITORY GITEA_SCRIPTS_BRANCH; do
    case "$key" in
      GITEA_BASE_URL) value="$GITEA_INTERNAL_URL" ;;
      GITEA_PUBLIC_URL) value="/gitea" ;;
      GITEA_SCRIPTS_REPOSITORY) value="${GITEA_ADMIN_USER}/${GITEA_REPOSITORY}" ;;
      GITEA_SCRIPTS_BRANCH) value="main" ;;
    esac
    [[ -n "$(get_setting "$APP_ENV" "$key")" ]] || set_setting "$APP_ENV" "$key" "$value" || return 1
  done
}

ensure_local_gitea() {
  # Starts (or upgrades) the managed Gitea and connects MyPortal to it. A
  # Gitea problem never blocks a MyPortal deploy; the next one retries.
  gitea_managed || return 0
  info "Starting Gitea for the script library…"
  if ! compose up -d --pull missing --wait gitea >&2; then
    warn "Gitea did not start; MyPortal runs without its script library. Inspect it with 'myportal-docker logs gitea'."
    return 0
  fi
  connect_gitea || warn "Gitea is running but MyPortal is not connected to it yet; the next upgrade or restart retries."
}

write_compose_file() {
  cat >"$COMPOSE_FILE" <<'YAML'
# Generated by myportal-docker.sh; rewritten on every upgrade. Put local
# changes in docker-compose.override.yml, which Compose merges automatically.
name: myportal

services:
  redis:
    image: ${REDIS_IMAGE}
    profiles: [local-redis]
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 30
    # Accessible only on the Compose network; no published host port.

  # Gitea for the RMM script library, served by the proxy at /gitea. Started
  # by name, so installations that use another Gitea server never run it.
  gitea:
    image: ${GITEA_IMAGE}
    profiles: [local-gitea]
    restart: unless-stopped
    env_file: gitea.env
    environment:
      USER_UID: "1000"
      USER_GID: "1000"
      GITEA__database__DB_TYPE: sqlite3
      GITEA__server__HTTP_PORT: "3000"
      GITEA__server__ROOT_URL: ${GITEA_ROOT_URL}
      GITEA__server__PUBLIC_URL_DETECTION: auto
      GITEA__server__DISABLE_SSH: "true"
      GITEA__server__START_SSH_SERVER: "false"
      GITEA__server__LFS_START_SERVER: "false"
      GITEA__server__OFFLINE_MODE: "true"
      GITEA__security__INSTALL_LOCK: "true"
      GITEA__service__DISABLE_REGISTRATION: "true"
      GITEA__service__REQUIRE_SIGNIN_VIEW: "true"
      GITEA__service__DEFAULT_KEEP_EMAIL_PRIVATE: "true"
      # MyPortal sign-in: the proxy sends who is signed in to MyPortal. Only
      # Compose containers reach Gitea, so the private ranges are trusted.
      GITEA__service__ENABLE_REVERSE_PROXY_AUTHENTICATION: "true"
      GITEA__service__ENABLE_REVERSE_PROXY_AUTO_REGISTRATION: "true"
      GITEA__service__ENABLE_REVERSE_PROXY_EMAIL: "true"
      GITEA__service__ENABLE_REVERSE_PROXY_FULL_NAME: "true"
      GITEA__security__REVERSE_PROXY_AUTHENTICATION_USER: X-WEBAUTH-USER
      GITEA__security__REVERSE_PROXY_AUTHENTICATION_EMAIL: X-WEBAUTH-EMAIL
      GITEA__security__REVERSE_PROXY_AUTHENTICATION_FULL_NAME: X-WEBAUTH-FULLNAME
      GITEA__security__REVERSE_PROXY_TRUSTED_PROXIES: 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
      GITEA__repository__DEFAULT_BRANCH: main
      GITEA__repository__DEFAULT_PRIVATE: private
      GITEA__openid__ENABLE_OPENID_SIGNIN: "false"
      GITEA__openid__ENABLE_OPENID_SIGNUP: "false"
      GITEA__mailer__ENABLED: "false"
    volumes:
      - gitea_data:/data
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:3000/api/healthz"]
      interval: 5s
      timeout: 3s
      retries: 60
    # Accessible only on the Compose network and through the proxy.

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

  # Blue/green application slots. One serves through the proxy; the other is
  # stopped and keeps the previous release for 'myportal-docker rollback'.
  # An upgrade starts the new release in the idle slot and switches the proxy
  # only once it is healthy. Start slots by name: 'docker compose up -d'
  # without service names would start both.
  app_blue:
    image: ${BLUE_IMAGE}
    restart: unless-stopped
    env_file: myportal.env
    environment:
      DB_HOST: db
      DB_PORT: "3306"
      PORT: "8000"
      APP_INSTANCE_ID: blue
      # Only the proxy can reach the slots, so trust its network.
      TRUSTED_PROXIES: ${APP_TRUSTED_PROXIES}
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - private_uploads:/app/private_uploads
      - uploads:/app/app/static/uploads
      - state:/app/var

  app_green:
    image: ${GREEN_IMAGE}
    restart: unless-stopped
    env_file: myportal.env
    environment:
      DB_HOST: db
      DB_PORT: "3306"
      PORT: "8000"
      APP_INSTANCE_ID: green
      TRUSTED_PROXIES: ${APP_TRUSTED_PROXIES}
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - private_uploads:/app/private_uploads
      - uploads:/app/app/static/uploads
      - state:/app/var

  proxy:
    image: ${PROXY_IMAGE}
    restart: unless-stopped
    ports:
      - "${HTTP_BIND}:${HTTP_PORT}:8080"
    volumes:
      # A directory, not single files, so atomically replaced files are seen.
      - ./proxy:/etc/nginx/conf.d:ro

volumes:
  redis_data:
  gitea_data:
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
REDIS_URL=redis://redis:6379/0

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
# Blue/green slots and the proxy
#
# Two application services, app_blue and app_green, share the database and
# volumes. An nginx proxy publishes the HTTP port and forwards to the active
# slot, named in proxy/active-slot.inc. A release is started in the idle slot
# while the active one keeps serving, and the proxy is switched (a graceful
# nginx reload) only once the new release reports ready. The previous slot is
# then stopped but kept, so 'rollback' can switch back to it.
#
# ACTIVE_SLOT in .env is empty on installations made before blue/green: they
# still run a single "app" service that publishes the port itself, and are
# converted by their next upgrade or restart.
# ---------------------------------------------------------------------------
active_slot() {
  get_setting "$PROJECT_ENV" ACTIVE_SLOT
}

other_slot() {
  if [[ "$1" == blue ]]; then printf green; else printf blue; fi
}

slot_key() {
  # slot_key SLOT NAME -> the .env key holding NAME for SLOT, e.g. BLUE_IMAGE.
  printf '%s_%s' "$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')" "$2"
}

app_service() {
  # The compose service serving the portal.
  local slot
  slot=$(active_slot)
  if [[ -n "$slot" ]]; then printf 'app_%s' "$slot"; else printf app; fi
}

service_container() {
  # service_container SERVICE -> ID of its running container (empty if none).
  # Found by label rather than through compose, so it also finds the
  # pre-blue/green "app" container after the compose file was rewritten.
  docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=$1" 2>/dev/null | head -n1
}

app_exec() {
  # app_exec COMMAND...: run COMMAND in the serving application container,
  # with standard input attached.
  local container
  container=$(service_container "$(app_service)")
  [[ -n "$container" ]] || return 1
  docker exec -i -e PYTHONPATH=/app "$container" "$@"
}

ensure_slot_settings() {
  # Fill in the .env keys the compose file needs. Both slots always name an
  # image, because Compose rejects a service without one.
  local image
  image=$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)
  [[ -n "$(get_setting "$PROJECT_ENV" PROXY_IMAGE)" ]] || set_setting "$PROJECT_ENV" PROXY_IMAGE "$MYPORTAL_PROXY_IMAGE"
  [[ -n "$(get_setting "$PROJECT_ENV" BLUE_IMAGE)" ]] || set_setting "$PROJECT_ENV" BLUE_IMAGE "$image"
  [[ -n "$(get_setting "$PROJECT_ENV" GREEN_IMAGE)" ]] || set_setting "$PROJECT_ENV" GREEN_IMAGE "$image"
  grep -q '^APP_TRUSTED_PROXIES=' "$PROJECT_ENV" || set_setting "$PROJECT_ENV" APP_TRUSTED_PROXIES ""
}

refresh_app_trusted_proxies() {
  # The slots are reachable only on the project network, through the proxy,
  # so they trust that network's forwarding headers. The network exists once
  # the database has been started.
  local subnets
  subnets=$(docker network inspect "${COMPOSE_PROJECT}_default" \
    --format '{{range .IPAM.Config}}{{.Subnet}},{{end}}' 2>/dev/null || true)
  subnets="${subnets%,}"
  [[ -n "$subnets" ]] || die "could not determine the subnet of the ${COMPOSE_PROJECT}_default Docker network."
  set_setting "$PROJECT_ENV" APP_TRUSTED_PROXIES "$subnets"
}

write_active_slot() {
  # Atomically point the proxy's upstream at SLOT; nginx re-reads it on reload.
  local tmp
  tmp=$(mktemp "${PROXY_DIR}/.active-slot.XXXXXX")
  printf '# Managed by myportal-docker.\nserver app_%s:8000 resolve;\n' "$1" >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "${PROXY_DIR}/active-slot.inc"
}

write_proxy_config() {
  # Generate the nginx configuration. TRUSTED_PROXIES from myportal.env now
  # applies here: requests from those addresses may name the client and the
  # scheme; everything else is described by the proxy itself.
  install -d -m 0755 "$PROXY_DIR"
  local entry realip="" geo="" tmp
  local -a entries=()
  IFS=',' read -r -a entries <<<"$(get_setting "$APP_ENV" TRUSTED_PROXIES | tr -d "\"' \t\r")"
  for entry in "${entries[@]}"; do
    [[ -n "$entry" ]] || continue
    if [[ "$entry" =~ ^[0-9A-Fa-f.:]+(/[0-9]{1,3})?$ ]]; then
      realip+="    set_real_ip_from ${entry};"$'\n'
      geo+="    ${entry} 1;"$'\n'
    else
      warn "ignoring TRUSTED_PROXIES entry '${entry}' in ${APP_ENV}: not an IP address or network."
    fi
  done

  tmp=$(mktemp "${PROXY_DIR}/.myportal.XXXXXX")
  {
    cat <<'NGINX'
# Generated by myportal-docker; rewritten on every upgrade and restart.
# Forwards to the active blue/green slot named in active-slot.inc.

# Docker's embedded DNS: slots are resolved when they start, so the proxy
# runs even while a slot is being replaced.
resolver 127.0.0.11 valid=5s ipv6=off;

upstream myportal_app {
    zone myportal_app 64k;
    include /etc/nginx/conf.d/active-slot.inc;
    keepalive 16;
}

map $http_upgrade $myportal_connection {
    default upgrade;
    ""      "";
}

# Whether the request came from a reverse proxy listed in TRUSTED_PROXIES.
geo $realip_remote_addr $myportal_trusted_proxy {
    default 0;
NGINX
    printf '%s' "$geo"
    cat <<'NGINX'
}

map "$myportal_trusted_proxy:$http_x_forwarded_proto" $myportal_forwarded_proto {
    default   $scheme;
    "1:https" https;
    "1:http"  http;
}

# Gitea keeps its own persistent session, so a stale one would keep a
# technician signed in after they sign out of MyPortal or lose the Script
# editing permission. When MyPortal has no identity to forward
# ($myportal_gitea_user is empty), drop the request's cookies so Gitea shows
# its sign-in page instead of reusing the session; signed-in requests keep
# their cookies, so Gitea's CSRF protection still works. The map is evaluated
# when the cookie is forwarded (content phase), after auth_request has set
# $myportal_gitea_user (access phase).
map $myportal_gitea_user $myportal_gitea_forward_cookie {
    default    $http_cookie;
    ""         "";
}

server {
    listen 8080;
    server_tokens off;

    # Uploads and responses stream through unchanged, as when MyPortal was
    # reached directly; the application enforces its own limits.
    client_max_body_size 0;
    proxy_request_buffering off;
    proxy_buffering off;

    real_ip_header X-Forwarded-For;
    real_ip_recursive on;
NGINX
    printf '%s' "$realip"
    cat <<'NGINX'

    location / {
        proxy_pass http://myportal_app;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $myportal_connection;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $myportal_forwarded_proto;
        proxy_connect_timeout 10s;
        # Long enough for websockets and slow reports.
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        error_page 502 504 =503 /myportal-unavailable.html;
    }

    # Gitea, where RMM scripts are edited. Resolved per request, so the proxy
    # runs whether or not this installation runs Gitea.
    location = /gitea {
        absolute_redirect off;
        return 301 /gitea/;
    }

    # MyPortal sign-in for Gitea: the proxy asks MyPortal who is signed in
    # and passes the answer to Gitea, which trusts these headers only from
    # the Compose network. They are always set here, so a browser cannot
    # supply its own.
    location = /_myportal/gitea-identity {
        internal;
        proxy_method GET;
        proxy_pass http://myportal_app/api/rmm/gitea/identity;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $myportal_forwarded_proto;
    }

    # Gitea's static files are public and need no sign-in lookup.
    location ^~ /gitea/assets/ {
        set $myportal_gitea gitea:3000;
        rewrite ^/gitea(/.*)$ $1 break;
        proxy_pass http://$myportal_gitea;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Connection "";
        proxy_set_header X-WEBAUTH-USER "";
        proxy_set_header X-WEBAUTH-EMAIL "";
        proxy_set_header X-WEBAUTH-FULLNAME "";
    }

    location ^~ /gitea/ {
        auth_request /_myportal/gitea-identity;
        auth_request_set $myportal_gitea_user $upstream_http_x_myportal_gitea_user;
        auth_request_set $myportal_gitea_email $upstream_http_x_myportal_gitea_email;
        auth_request_set $myportal_gitea_name $upstream_http_x_myportal_gitea_name;
        set $myportal_gitea gitea:3000;
        rewrite ^/gitea(/.*)$ $1 break;
        proxy_pass http://$myportal_gitea;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Connection "";
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $myportal_forwarded_proto;
        proxy_set_header X-WEBAUTH-USER $myportal_gitea_user;
        proxy_set_header X-WEBAUTH-EMAIL $myportal_gitea_email;
        proxy_set_header X-WEBAUTH-FULLNAME $myportal_gitea_name;
        # Forward the browser's cookies to Gitea only while MyPortal forwarded
        # an identity; otherwise strip them so a stale Gitea session can't
        # keep the technician signed in after MyPortal sign-out (map above).
        proxy_set_header Cookie $myportal_gitea_forward_cookie;
        proxy_connect_timeout 10s;
        proxy_read_timeout 300s;
    }

    location = /myportal-unavailable.html {
        internal;
        root /etc/nginx/conf.d;
        add_header Retry-After 15 always;
        add_header Cache-Control "no-store" always;
    }
}
NGINX
  } >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "${PROXY_DIR}/myportal.conf"

  cat >"${PROXY_DIR}/myportal-unavailable.html" <<'HTML'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>MyPortal is restarting</title>
<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#1f2937}</style>
</head>
<body>
<h1>MyPortal is restarting</h1>
<p>The portal is temporarily unavailable. This page reloads automatically.</p>
</body>
</html>
HTML
  chmod 0644 "${PROXY_DIR}/myportal-unavailable.html"
  [[ -f "${PROXY_DIR}/active-slot.inc" ]] || write_active_slot "${1:-blue}"
}

proxy_instance() {
  # Identifies the running proxy container and when it started (empty if none).
  local container
  container=$(service_container proxy)
  [[ -z "$container" ]] || docker inspect -f '{{.Id}} {{.State.StartedAt}}' "$container" 2>/dev/null
}

switch_proxy() {
  # switch_proxy SLOT: send new requests to SLOT. Requests in flight finish on
  # the previous slot (nginx reloads gracefully). When nginx rejects the
  # change, the previous slot stays active and this returns non-zero.
  local slot="$1" include="${PROXY_DIR}/active-slot.inc" previous="" container before
  [[ ! -f "$include" ]] || previous=$(sed -n 's/^server app_\([a-z]*\):.*/\1/p' "$include")
  write_active_slot "$slot"
  before=$(proxy_instance)
  # Creates the proxy, or recreates it when its compose definition changed.
  # A stopped proxy is always replaced: one left over from a failed attempt
  # can have lost its network, and could then never resolve the slots.
  local -a recreate=()
  [[ -n "$before" ]] || recreate=(--force-recreate)
  if compose up -d --no-deps "${recreate[@]}" proxy >&2; then
    container=$(service_container proxy)
    if [[ -n "$container" && "$(proxy_instance)" != "$before" ]]; then
      # Just started, so it read the new slot at startup. Signalling nginx
      # while it starts makes it exit ("Address in use"), so don't reload;
      # wait_for_proxy confirms it serves.
      return 0
    fi
    if [[ -n "$container" ]] && docker exec "$container" nginx -t >/dev/null 2>&1 \
      && docker exec "$container" nginx -s reload >/dev/null 2>&1; then
      return 0
    fi
    [[ -z "$container" ]] || docker exec "$container" nginx -t >&2 || true
  fi
  warn "could not switch the proxy to the ${slot} slot."
  if [[ -n "$previous" ]]; then
    write_active_slot "$previous"
    container=$(service_container proxy)
    [[ -z "$container" ]] || docker exec "$container" nginx -s reload >/dev/null 2>&1 || true
  fi
  return 1
}

stop_legacy_app() {
  # Pre-blue/green installations run one "app" container publishing the port.
  local container
  container=$(service_container app)
  [[ -z "$container" ]] || docker stop "$container" >/dev/null
}

remove_legacy_app() {
  docker ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
    --filter "label=com.docker.compose.service=app" | xargs -r docker rm -f >/dev/null
}

deploy_release() {
  # deploy_release IMAGE TAG: run TAG in the idle slot and switch the proxy to
  # it once it is ready. The serving slot is untouched until then; if TAG
  # does not become ready it keeps serving and this returns non-zero.
  local image="$1" tag="$2" active idle idle_image_key previous_idle_image
  active=$(active_slot)
  idle=$(other_slot "$active")
  idle_image_key=$(slot_key "$idle" IMAGE)
  previous_idle_image=$(get_setting "$PROJECT_ENV" "$idle_image_key")

  if [[ -z "$active" ]] && grep -Eqs '^[[:space:]]+app:[[:space:]]*$' "${MYPORTAL_DIR}/docker-compose.override.yml"; then
    warn "${MYPORTAL_DIR}/docker-compose.override.yml changes the 'app' service, which blue/green replaces with 'app_blue' and 'app_green' (and 'proxy', which now publishes the port). Move those changes to the new services, then rerun."
    return 1
  fi
  # Until the conversion succeeds, a pre-blue/green installation keeps its
  # compose file, so its commands keep working if this fails.
  [[ -n "$active" || ! -f "$COMPOSE_FILE" ]] || cp -p "$COMPOSE_FILE" "${COMPOSE_FILE}.single"
  # Callers test this function's status, which turns off "set -e" inside it.
  write_compose_file || return 1
  ensure_redis_settings || return 1
  ensure_gitea_settings || return 1
  ensure_slot_settings || return 1
  write_proxy_config "$idle" || return 1
  compose up -d db >&2 || return 1
  ensure_local_redis || return 1
  ensure_local_gitea
  refresh_app_trusted_proxies || return 1
  set_setting "$PROJECT_ENV" "$idle_image_key" "$image" || return 1
  # Recreated even when the image is unchanged, so 'restart' applies
  # myportal.env changes.
  info "Starting MyPortal ${tag} in the ${idle} slot${active:+ while the ${active} slot keeps serving}…"
  if ! compose up -d --no-deps --force-recreate "app_${idle}" >&2 || ! wait_for_service "app_${idle}" "$tag"; then
    compose stop "app_${idle}" >/dev/null 2>&1 || true
    set_setting "$PROJECT_ENV" "$idle_image_key" "$previous_idle_image"
    [[ ! -f "${COMPOSE_FILE}.single" ]] || mv -f "${COMPOSE_FILE}.single" "$COMPOSE_FILE"
    return 1
  fi

  if [[ -z "$active" && -n "$(service_container app)" ]]; then
    # Converting a pre-blue/green installation: the old container holds the
    # port the proxy needs, so this one cutover has a short interruption.
    info "Switching to the blue/green layout; the portal is unavailable for a few seconds…"
    stop_legacy_app
  fi
  if ! switch_proxy "$idle" || ! wait_for_proxy "$tag"; then
    if [[ -n "$active" ]]; then
      switch_proxy "$active" || true
    else
      compose rm -sf proxy >/dev/null 2>&1 || true
      compose stop "app_${idle}" >/dev/null 2>&1 || true
      if [[ -f "${COMPOSE_FILE}.single" ]]; then
        mv -f "${COMPOSE_FILE}.single" "$COMPOSE_FILE"
        compose up -d app >&2 || true
      fi
    fi
    compose stop "app_${idle}" >/dev/null 2>&1 || true
    set_setting "$PROJECT_ENV" "$idle_image_key" "$previous_idle_image"
    return 1
  fi

  set_setting "$PROJECT_ENV" ACTIVE_SLOT "$idle"
  set_setting "$PROJECT_ENV" "$(slot_key "$idle" VERSION)" "$tag"
  set_setting "$PROJECT_ENV" MYPORTAL_IMAGE "$image"
  set_setting "$PROJECT_ENV" MYPORTAL_VERSION "$tag"
  info "MyPortal ${tag} is now serving from the ${idle} slot."
  if [[ -n "$active" ]]; then
    info "Letting the ${active} slot finish its requests (${MYPORTAL_DRAIN_SECONDS}s), then stopping it…"
    sleep "$MYPORTAL_DRAIN_SECONDS"
    compose stop "app_${active}" >&2 || warn "could not stop the ${active} slot."
  else
    remove_legacy_app
    rm -f "${COMPOSE_FILE}.single"
    # The previous release was not kept in a slot, so there is nothing to
    # roll back to until the next upgrade.
    set_setting "$PROJECT_ENV" "$(slot_key "$(other_slot "$idle")" IMAGE)" "$image"
    set_setting "$PROJECT_ENV" "$(slot_key "$(other_slot "$idle")" VERSION)" ""
  fi
}

# ---------------------------------------------------------------------------
# Health, backup and restore
# ---------------------------------------------------------------------------
release_ready() {
  # release_ready BODY TAG: true when a /readyz body reports TAG as ready.
  [[ "$1" == *'"status":"ok"'* && "$1" == *"\"version\":\"$2\""* ]]
}

# Ignores HTTP_PROXY and similar settings: the request never leaves the
# container.
READYZ_PROGRAM='import urllib.error, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    response = opener.open("http://127.0.0.1:8000/readyz", timeout=8)
except urllib.error.HTTPError as exc:
    response = exc
print(response.read().decode("utf-8", "replace"))'

wait_for_service() {
  # wait_for_service SERVICE TAG: wait until the application in SERVICE
  # reports TAG as ready. The slots publish no port, so ask from inside.
  local service="$1" tag="$2" body="" container state start=$SECONDS report=$((SECONDS + 60))
  info "Waiting for MyPortal ${tag} to become ready in ${service} (migrations run on first start)…"
  # Timed by the clock: each check can itself take several seconds.
  while ((SECONDS - start < MYPORTAL_HEALTH_TIMEOUT)); do
    container=$(service_container "$service")
    body=""
    [[ -z "$container" ]] || body=$(docker exec "$container" python -c "$READYZ_PROGRAM" 2>/dev/null || true)
    release_ready "$body" "$tag" && return 0
    if ((SECONDS >= report)); then
      state=$(docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
        --filter "label=com.docker.compose.service=${service}" --format '{{.Status}}' 2>/dev/null | head -n1)
      info "Still waiting for ${service} after $((SECONDS - start))s (container: ${state:-missing}; /readyz: ${body:-no response})."
      report=$((SECONDS + 60))
    fi
    sleep 5
  done
  warn "MyPortal ${tag} did not become ready within ${MYPORTAL_HEALTH_TIMEOUT}s (last response: ${body:-none})."
  compose logs --tail 40 "$service" >&2 || true
  return 1
}

wait_for_proxy() {
  # wait_for_proxy TAG: confirm the proxy serves TAG.
  local tag="$1" body="" container start=$SECONDS
  while ((SECONDS - start < 60)); do
    container=$(service_container proxy)
    body=""
    [[ -z "$container" ]] \
      || body=$(docker exec "$container" wget -Y off -qO- -T 10 http://127.0.0.1:8080/readyz 2>/dev/null || true)
    release_ready "$body" "$tag" && return 0
    sleep 2
  done
  warn "the proxy does not serve MyPortal ${tag} (last response: ${body:-none})."
  compose logs --tail 20 proxy >&2 || true
  return 1
}

wait_for_release() {
  # Wait until the serving application reports TAG as ready.
  local tag="$1"
  wait_for_service "$(app_service)" "$tag" || return 1
  [[ -z "$(active_slot)" ]] || wait_for_proxy "$tag"
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
  for kind in db files gitea; do
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

  local image
  image=$(obtain_image "$version")
  set_setting "$PROJECT_ENV" MYPORTAL_IMAGE "$image"

  # Records the release only once healthy, so a failed attempt can be rerun.
  if ! deploy_release "$image" "$version"; then
    die "the installation did not become healthy. Inspect it with 'docker compose --project-directory ${MYPORTAL_DIR} logs app_blue', then rerun install."
  fi
  install_script_copy "$version"
  if [[ -x "$INSTALLED_SCRIPT" ]]; then
    install_web_upgrade_cron
    set_setting "$PROJECT_ENV" WEB_UPGRADES on
  fi

  local host
  host=$(hostname -f 2>/dev/null || hostname)
  local gitea_note
  gitea_note=$(gitea_install_note)
  [[ -z "$gitea_note" ]] || gitea_note+=$'\n\n'
  cat <<EOF

MyPortal ${version} is running.
  Portal:        http://${host}$([[ "$port" == 80 ]] || printf ':%s' "$port")/
  Configuration: ${APP_ENV}
  Management:    myportal-docker help

${gitea_note}Open the portal and register: the first account becomes the super
administrator. Upgrade from the portal's System updates page, with
'sudo myportal-docker upgrade', or enable daily automatic upgrades with
'sudo myportal-docker auto-upgrade on'.

Before exposing the portal to the internet, terminate TLS in front of it, then
set PORTAL_URL=https://… and ENVIRONMENT=production in ${APP_ENV} and run
'sudo myportal-docker restart'.
EOF
}

gitea_install_note() {
  # Where the script library is, for the post-install summary.
  [[ -f "$GITEA_ADMIN_FILE" ]] || return 0
  printf 'Scripts (Gitea): %s\n' "$(sed -n 's/^url=//p' "$GITEA_ADMIN_FILE")"
  printf '  Sign in as %s; the password is in %s.\n\n' "$GITEA_ADMIN_USER" "$GITEA_ADMIN_FILE"
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

take_deploy_lock() {
  # One upgrade, rollback or restart at a time, whether from the shell, the
  # nightly job or the portal. The descriptor and variable survive the
  # self-update exec.
  [[ -z "${MYPORTAL_UPGRADE_LOCKED:-}" ]] || return 0
  exec 9>"$(dirname "$PROJECT_ENV")/.upgrade.lock"
  flock -n 9 || die "another upgrade, rollback or restart is already running."
  export MYPORTAL_UPGRADE_LOCKED=1
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
  take_deploy_lock
  adopt_web_upgrades

  local current
  current=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  [[ -n "$version" ]] || version=$(latest_release_tag)
  valid_tag "$version" || die "invalid release tag: ${version}"

  if [[ "$version" == "$current" ]]; then
    # An earlier upgrade may have run before this release's script was
    # attached; pick it up now so its commands are available.
    self_update "$version" upgrade "${original_args[@]}"
    local redis_url
    redis_url=$(redis_connection_url) || die "could not read Redis configuration."
    if [[ -z "$redis_url" ]] || gitea_needs_setup; then
      deploy_release "$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)" "$current" \
        || die "could not apply local Redis and Gitea defaults to the installed release."
    fi
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
  local image backup
  image=$(obtain_image "$version")
  compose up -d db
  backup=$(backup_database "before-${version}")

  # The new release starts in the idle slot while ${current} keeps serving.
  if deploy_release "$image" "$version"; then
    set_setting "$PROJECT_ENV" FAILED_VERSION ""
    prune_backups
    info "MyPortal ${version} is running. Database backup: ${backup}"
    [[ -z "$(get_setting "$PROJECT_ENV" "$(slot_key "$(other_slot "$(active_slot)")" VERSION)")" ]] \
      || info "${current} is kept in the idle slot; 'myportal-docker rollback' switches back to it."
    return 0
  fi

  set_setting "$PROJECT_ENV" FAILED_VERSION "$version"
  if wait_for_release "$current"; then
    die "the upgrade to ${version} failed; ${current} is still serving. The database was backed up before the upgrade to ${backup}; if ${current} misbehaves after a partial migration, restore it with 'myportal-docker restore-db ${backup}'."
  fi
  die "the upgrade to ${version} failed and ${current} is not ready. Restore the database with 'myportal-docker restore-db ${backup}'."
}

cmd_rollback() {
  local assume_yes=false
  while (($#)); do
    case "$1" in
      --yes|-y) assume_yes=true; shift ;;
      *) die "unknown option for rollback: $1" ;;
    esac
  done
  require_root
  require_installed
  ensure_docker
  take_deploy_lock

  local active idle current version image
  active=$(active_slot)
  [[ -n "$active" ]] || die "no previous release is kept yet; one is kept from the next upgrade on."
  idle=$(other_slot "$active")
  current=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  version=$(get_setting "$PROJECT_ENV" "$(slot_key "$idle" VERSION)")
  image=$(get_setting "$PROJECT_ENV" "$(slot_key "$idle" IMAGE)")
  [[ -n "$version" && "$version" != "$current" ]] \
    || die "no previous release is kept (a restart or first upgrade replaces it). Restore an older release with 'myportal-docker upgrade --version TAG'."
  docker image inspect "$image" >/dev/null 2>&1 \
    || die "the image of ${version} (${image}) is no longer on this host. Use 'myportal-docker upgrade --version ${version}' instead."

  if [[ "$assume_yes" != true && -t 0 ]]; then
    local answer
    read -r -p "Switch MyPortal from ${current} back to ${version}? Changes ${current} made to the database stay. [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || die "rollback cancelled."
  fi
  info "Rolling MyPortal back from ${current} to ${version}."
  deploy_release "$image" "$version" || die "${version} did not become ready; ${current} is still serving."
  # Unattended and portal upgrades would otherwise reinstall it tonight.
  set_setting "$PROJECT_ENV" FAILED_VERSION "$current"
  info "MyPortal ${version} is serving again. Unattended upgrades skip ${current}; install it again with 'myportal-docker upgrade --version ${current}'."
  info "If ${version} misbehaves with the database as ${current} left it, restore the backup taken before that upgrade (in ${BACKUP_DIR}) with 'myportal-docker restore-db FILE'."
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
  printf 'Installed release: %s\nImage:             %s\n' \
    "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" "$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)"
  local active previous
  active=$(active_slot)
  if [[ -n "$active" ]]; then
    previous=$(get_setting "$PROJECT_ENV" "$(slot_key "$(other_slot "$active")" VERSION)")
    printf 'Serving slot:      %s\n' "$active"
    if [[ -n "$previous" && "$previous" != "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" ]]; then
      printf 'Previous release:  %s (%s slot, stopped; myportal-docker rollback)\n' "$previous" "$(other_slot "$active")"
    fi
  fi
  printf '\n'
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
  app_exec tar -czf - -C /app private_uploads app/static/uploads var >"$files"
  chmod 0600 "$files"
  local gitea=""
  if [[ -n "$(service_container gitea)" ]]; then
    gitea="${BACKUP_DIR}/gitea-${version}-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    info "Backing up the Gitea script library to ${gitea}…"
    compose exec -T gitea tar -czf - -C /data . >"$gitea"
    chmod 0600 "$gitea"
  fi
  prune_backups
  cat <<EOF
Backups written:
  ${db}
  ${files}${gitea:+
  ${gitea}}
Also keep a copy of ${APP_ENV} (it holds the encryption keys).
EOF
}

cmd_restore_db() {
  local file="${1:-}"
  require_root
  require_installed
  [[ -f "$file" ]] || die "usage: myportal-docker restore-db BACKUP.sql.gz"
  local service
  service=$(app_service)
  info "Stopping MyPortal and restoring ${file}…"
  compose stop "$service"
  compose up -d db
  local waited=0
  until compose exec -T db healthcheck.sh --connect >/dev/null 2>&1; do
    ((waited < 120)) || die "the database did not start."
    sleep 2; waited=$((waited + 2))
  done
  gunzip -c "$file" | compose exec -T db sh -c 'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
  compose up -d "$service"
  wait_for_release "$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)" || die "MyPortal did not become ready after the restore."
  info "Database restored."
}

cmd_restart() {
  # Start the installed release afresh in the idle slot and switch to it, so
  # configuration changes apply without downtime. This replaces the previous
  # release kept for rollback.
  require_root
  require_installed
  ensure_docker
  take_deploy_lock
  local current
  current=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  deploy_release "$(get_setting "$PROJECT_ENV" MYPORTAL_IMAGE)" "$current" \
    || die "MyPortal ${current} did not become ready with the current configuration; the running container was left serving. Check ${APP_ENV}."
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

ensure_cron_daemon() {
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
      ensure_cron_daemon
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
# Upgrades requested from the portal
#
# The portal never gets root or the Docker socket. Its only capability is to
# drop a request file in its own state volume. This root job reads that file
# through "docker exec" (so a symlink planted in the volume cannot
# redirect a host path), takes nothing from it but a UUID to report progress
# against, and runs the same 'upgrade --yes' as the nightly job: the latest
# published release, with a backup and automatic rollback.
# ---------------------------------------------------------------------------
install_web_upgrade_cron() {
  [[ -x "$INSTALLED_SCRIPT" ]] || die "${INSTALLED_SCRIPT} is missing; reinstall it with 'bash myportal-docker.sh install' or copy this script there."
  cat >"$WEB_UPGRADE_CRON" <<EOF
# Installed by myportal-docker: apply upgrades requested from the MyPortal
# System updates page. Disable with 'myportal-docker web-upgrades off'.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
* * * * * root MYPORTAL_DIR=${MYPORTAL_DIR} ${INSTALLED_SCRIPT} process-requests >> ${MYPORTAL_DIR}/web-upgrade.log 2>&1
EOF
  chmod 0644 "$WEB_UPGRADE_CRON"
  ensure_cron_daemon
}

adopt_web_upgrades() {
  # Installations that predate web-triggered upgrades turn them on at their
  # next upgrade, unless an administrator already chose 'web-upgrades off'.
  [[ -z "$(get_setting "$PROJECT_ENV" WEB_UPGRADES)" && -x "$INSTALLED_SCRIPT" ]] || return 0
  install_web_upgrade_cron
  set_setting "$PROJECT_ENV" WEB_UPGRADES on
  info "Upgrades can now be started from the portal's System updates page ('myportal-docker web-upgrades off' disables this)."
}

cmd_web_upgrades() {
  require_root
  require_installed
  case "${1:-status}" in
    on)
      install_web_upgrade_cron
      set_setting "$PROJECT_ENV" WEB_UPGRADES on
      info "Super administrators can start upgrades from the portal's System updates page (log: ${MYPORTAL_DIR}/web-upgrade.log)."
      ;;
    off)
      rm -f "$WEB_UPGRADE_CRON"
      set_setting "$PROJECT_ENV" WEB_UPGRADES off
      app_exec rm -f "$CONTAINER_UPDATE_FLAG" >/dev/null 2>&1 || true
      info "Upgrades from the portal are disabled."
      ;;
    status)
      if [[ "$(get_setting "$PROJECT_ENV" WEB_UPGRADES)" == on && -f "$WEB_UPGRADE_CRON" ]]; then
        printf 'Upgrades from the portal: enabled\n'
      else
        printf 'Upgrades from the portal: disabled\n'
      fi
      ;;
    *) die "usage: myportal-docker web-upgrades on|off|status" ;;
  esac
}

report_update() {
  # report_update FILE UPDATE_ID STATUS [--error MESSAGE]
  # Record progress in the portal's update history, with FILE as the output.
  # The helper runs inside the container as its service account, so the host
  # never writes into the volume. Should no application container be running,
  # a one-off container records the final result instead.
  local file="$1"; shift
  local -a helper=(/app/scripts/system_update_report.py "$@" --output-file -)
  app_exec python "${helper[@]}" <"$file" >/dev/null 2>&1 && return 0
  [[ "${REPORT_EXEC_ONLY:-}" != 1 ]] || return 0
  compose run --rm --no-deps -T -e PYTHONPATH=/app --entrypoint python "$(app_service)" "${helper[@]}" <"$file" >/dev/null 2>&1 \
    || warn "could not record the upgrade result in the portal."
}

cmd_process_requests() {
  require_root
  require_installed
  [[ "$(get_setting "$PROJECT_ENV" WEB_UPGRADES)" != off ]] || return 0
  exec 8>"${MYPORTAL_DIR}/.requests.lock"
  flock -n 8 || return 0

  local request update_id
  request=$(app_exec sh -c \
    'f="$1"; if [ -f "$f" ] && [ ! -L "$f" ]; then head -c 4096 "$f"; fi' sh "$CONTAINER_UPDATE_FLAG" \
    </dev/null 2>/dev/null) || return 0
  [[ -n "$request" ]] || return 0
  # Claim the request before acting on it, so a failure can never loop.
  app_exec rm -f "$CONTAINER_UPDATE_FLAG" </dev/null >/dev/null 2>&1 || true
  update_id=$(printf '%s\n' "$request" | sed -n 's/^update_id=//p' | head -n1 | tr -d '[:space:]')
  if [[ ! "$update_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    warn "ignoring an upgrade request without a valid identifier."
    return 0
  fi

  local log status=0 progress_pid before after script
  log=$(mktemp)
  before=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  info "Upgrade ${update_id} requested from the portal."
  printf 'Picked up by the Docker host at %s. Installed release: %s.\n' "$(date -u +%FT%TZ)" "$before" >"$log"
  report_update "$log" "$update_id" running
  (
    # Finish an in-flight report before exiting so it cannot land after the
    # final result; "sleep & wait" lets the signal interrupt the pause.
    trap 'exit 0' TERM
    while :; do
      sleep "${MYPORTAL_PROGRESS_INTERVAL:-10}" & wait $!
      REPORT_EXEC_ONLY=1 report_update "$log" "$update_id" running
    done
  ) &
  progress_pid=$!

  script="$INSTALLED_SCRIPT"
  [[ -x "$script" ]] || script=$(readlink -f "$0")
  MYPORTAL_DIR="$MYPORTAL_DIR" "$script" upgrade --yes >>"$log" 2>&1 || status=$?
  kill "$progress_pid" 2>/dev/null || true
  wait "$progress_pid" 2>/dev/null || true
  cat "$log"

  after=$(get_setting "$PROJECT_ENV" MYPORTAL_VERSION)
  if ((status != 0)); then
    report_update "$log" "$update_id" failed --error "myportal-docker upgrade exited with status ${status}."
  elif [[ "$after" == "$before" ]]; then
    # The portal only queues a request when a newer release exists, so an
    # unchanged release means the upgrade was skipped; the output says why.
    status=1
    report_update "$log" "$update_id" failed --error "No upgrade was applied; ${before} is still installed. See the output for the reason."
  else
    report_update "$log" "$update_id" succeeded
  fi
  rm -f "$log"
  return "$status"
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
  if app_exec true </dev/null >/dev/null 2>&1; then
    hash=$(app_exec python -c "$program")
  else
    hash=$(compose run --rm --no-deps -T "$(app_service)" python -c "$program" 2>/dev/null)
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
    rollback) cmd_rollback "$@" ;;
    check) cmd_check ;;
    self-update) cmd_self_update ;;
    status) cmd_status ;;
    logs)
      require_installed
      if (($#)); then compose logs "$@" "$(app_service)"; else compose logs --follow "$(app_service)"; fi
      ;;
    backup) cmd_backup ;;
    restore-db) cmd_restore_db "$@" ;;
    restart) cmd_restart ;;
    setup|onboard) cmd_setup "$@" ;;
    auto-upgrade) cmd_auto_upgrade "$@" ;;
    web-upgrades) cmd_web_upgrades "$@" ;;
    process-requests)
      # The upgrade may replace this script on disk while it runs; exit here
      # so bash never reads on into the new file's contents.
      cmd_process_requests
      exit 0
      ;;
    superadmin|super-admin) cmd_superadmin "$@" ;;
    user) cmd_user "$@" ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
  esac
}

main "$@"
