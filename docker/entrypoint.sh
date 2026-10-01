#!/usr/bin/env bash
# Container entrypoint for the MyPortal image.
#
#   serve (default)  wait for the database, apply migrations, start Uvicorn
#   migrate          wait for the database and apply migrations only
#   <command>        run any other command (for example "python manage.py ...")
set -euo pipefail
cd /app

VERSION=$(tr -d '\r\n' </app/version.txt 2>/dev/null || echo development)

wait_for_database() {
  if [[ -z "${DB_HOST:-}" ]]; then
    echo "[entrypoint] DB_HOST is not set; refusing to start without a database." >&2
    exit 1
  fi
  local timeout="${MYPORTAL_DB_WAIT_SECONDS:-180}"
  echo "[entrypoint] Waiting up to ${timeout}s for MariaDB at ${DB_HOST}:${DB_PORT:-3306}…" >&2
  python - "$timeout" <<'PY'
import os, sys, time
import pymysql

deadline = time.monotonic() + int(sys.argv[1])
last = None
while time.monotonic() < deadline:
    try:
        pymysql.connect(
            host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT") or 3306),
            user=os.environ.get("DB_USER", ""), password=os.environ.get("DB_PASSWORD", ""),
            database=os.environ.get("DB_NAME") or None, connect_timeout=5,
        ).close()
        sys.exit(0)
    except pymysql.MySQLError as exc:
        last = exc
        time.sleep(2)
print(f"[entrypoint] Database is not reachable: {last}", file=sys.stderr)
sys.exit(1)
PY
}

run_migrations() {
  echo "[entrypoint] Applying database migrations for release ${VERSION}…" >&2
  python manage.py migrate --target-release "$VERSION"
}

case "${1:-serve}" in
  serve)
    wait_for_database
    if [[ "${MYPORTAL_MIGRATE_ON_START:-true}" != "false" ]]; then
      run_migrations
    fi
    args=(--host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-2}")
    if [[ -n "${TRUSTED_PROXIES:-}" ]]; then
      # Keep Uvicorn's client address handling aligned with MyPortal's
      # TRUSTED_PROXIES boundary when a reverse proxy sits in front.
      args+=(--proxy-headers --forwarded-allow-ips "$TRUSTED_PROXIES")
    fi
    echo "[entrypoint] Starting MyPortal ${VERSION}." >&2
    exec python -m uvicorn app.main:app "${args[@]}"
    ;;
  migrate)
    wait_for_database
    run_migrations
    ;;
  *)
    exec "$@"
    ;;
esac
