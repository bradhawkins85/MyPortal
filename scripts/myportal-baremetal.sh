#!/usr/bin/env bash
# MyPortal bare-metal environment wizard.
#
# A small wrapper around scripts/onboarding_wizard.py that exposes the same
# "setup" entry point style as myportal-docker.sh on Docker hosts.

set -Eeuo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
WIZARD="${PROJECT_ROOT}/scripts/onboarding_wizard.py"
ENV_FILE="${MYPORTAL_ENV_FILE:-/etc/myportal.env}"
RELOAD_STATE_DIR="${MYPORTAL_SHARED_ROOT:-/opt/myportal/shared}/state/env-reload"

usage() {
  cat <<'EOF'
Usage: myportal-baremetal <command> [options]

Commands:
  setup [--check | --list | --feature NAME]
        Run the onboarding wizard for the bare-metal environment file.
        Default file: /etc/myportal.env (override with MYPORTAL_ENV_FILE)
  check [--feature NAME] [--verbose]
        Verify the current environment file without prompting.
  list
        List every feature and whether it is enabled or needs configuration.
  reload [--timeout SECONDS]
        Confirm every running worker has loaded the current environment file.
        Workers apply edits on their own within about 10 seconds, without a
        restart; this reports settings that still need one. Default timeout 30.
  help
        Show this help.
EOF
}

require_wizard() {
  [[ -f "$WIZARD" ]] || {
    echo "Error: wizard not found at ${WIZARD}." >&2
    exit 1
  }
}

run_wizard() {
  require_wizard
  exec python3 "$WIZARD" --env-file "$ENV_FILE" "$@"
}

reload_status() {
  local timeout=30
  while (($#)); do
    case "$1" in
      --timeout) timeout="${2:?--timeout needs a value}"; shift 2 ;;
      *) echo "Error: unknown reload option '$1'" >&2; exit 2 ;;
    esac
  done
  python3 - "$RELOAD_STATE_DIR" "$timeout" "$ENV_FILE" <<'PY'
import hashlib, json, os, stat, sys, time
from pathlib import Path

state_dir, deadline = Path(sys.argv[1]), time.monotonic() + float(sys.argv[2])
env_file = Path(sys.argv[3]).resolve()
# Status files are written by the service account. Only ever hash the files a
# worker is expected to load, so they cannot point this root process elsewhere.
allowed = {str(env_file)} | {
    str(env_file.with_name(f"{env_file.stem}.{slot}{env_file.suffix}")) for slot in ("blue", "green")
}


def digest(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def read_status(path):
    # Never follow a symlink or block on a FIFO planted in the state directory.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("not a regular file")
        payload = json.loads(handle.read(1 << 16))
    if not isinstance(payload, dict):
        raise ValueError("not an object")
    return payload


def printable(value):
    return "".join(char if char.isprintable() else "?" for char in str(value))[:500]


def workers():
    found = []
    for status in sorted(state_dir.glob("*.json")):
        try:
            payload = read_status(status)
        except (OSError, ValueError):
            continue
        pid = payload.get("pid")
        if not isinstance(pid, int) or not Path(f"/proc/{pid}").exists():
            continue  # a worker that has since exited
        files = payload.get("files")
        if not isinstance(files, dict) or not files or not set(files) <= allowed:
            payload["state"] = "unrecognised"
        elif all(digest(path) == value for path, value in files.items()):
            payload["state"] = "rejected" if payload.get("error") else "current"
        else:
            payload["state"] = "pending"
        found.append(payload)
    return found


try:
    while True:
        found = workers()
        if found and all(worker["state"] != "pending" for worker in found):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
except PermissionError as exc:
    sys.exit(f"Error: {exc}. Run with sudo.")

if not found:
    sys.exit(f"No running MyPortal worker has reported to {state_dir}. Is the service running a release with live reload?")

restart = set()
for worker in found:
    line = f"{printable(worker.get('slot', '?')):>5} pid {worker['pid']}: {worker['state']}"
    if worker["state"] == "rejected":
        line += f" (file not applied: {printable(worker['error'])})"
    elif worker["state"] == "unrecognised":
        line += f" (does not watch {env_file})"
    print(line)
    if isinstance(worker.get("restart_required"), list):
        restart.update(printable(key) for key in worker["restart_required"])

if restart:
    print("\nRead only at startup, so not yet in effect: " + ", ".join(sorted(restart)))
    print("They apply at the next deployment (sudo myportal-upgrade) or when the slots restart.")
states = {worker["state"] for worker in found}
if states & {"rejected", "unrecognised"}:
    sys.exit(1)
if "pending" in states:
    sys.exit("Some workers have not loaded the current file yet.")
print("\nEvery worker is running with the current environment file.")
PY
}

main() {
  case "${1:-help}" in
    help|-h|--help)
      usage
      ;;
    setup)
      shift
      run_wizard "$@"
      ;;
    check)
      shift
      run_wizard --check "$@"
      ;;
    list)
      shift
      run_wizard --list "$@"
      ;;
    reload)
      shift
      reload_status "$@"
      ;;
    *)
      echo "Error: unknown command '${1:-}'" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
