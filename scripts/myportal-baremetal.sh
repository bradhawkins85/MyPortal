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
    *)
      echo "Error: unknown command '${1:-}'" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
