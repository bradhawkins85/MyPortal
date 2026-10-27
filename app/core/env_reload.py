"""Apply environment file changes to running workers without a restart.

systemd loads ``/etc/myportal.env`` (and ``/etc/myportal.<slot>.env``) into
each worker's environment once, at start.  This module watches those files and,
when they change, applies the changed keys to ``os.environ`` and to the cached
``Settings`` object in place.  Module-level ``settings = get_settings()``
references hold that same object, so they see the new values too.

Every worker (both blue/green slots, every Uvicorn worker) watches on its own,
so nothing has to restart and no request is dropped.  A change is applied only
after the file has been unchanged for one poll interval, so a save in progress
is never half-applied, and only when the whole file still validates; a file
that does not validate is ignored and the running values stay in force.

Some settings are read once at startup (database pools, secrets, middleware,
logging, scheduler intervals).  Changes to those are not applied; they are
logged and reported as needing a restart.  Each worker records what it applied
in ``var/state/env-reload/<slot>-<pid>.json`` (key names only, never values),
which ``myportal-baremetal reload`` reads to confirm the change reached every
worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import log_debug, log_error, log_info, log_warning

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLL_SECONDS = 5.0

# Read once at startup: changing them needs a service restart.  Names are the
# environment keys, compared case-insensitively like pydantic-settings does.
RESTART_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        # Process and identity
        "APP_INSTANCE_ID", "ENVIRONMENT", "MYPORTAL_BIND_HOST", "PORT",
        "WEB_CONCURRENCY", "UVICORN_TIMEOUT_KEEP_ALIVE",
        # Secrets and encryption keys
        "SESSION_SECRET", "SECRET_KEY", "TOTP_ENCRYPTION_KEY", "VAULT_KEYS",
        "VAULT_ACTIVE_KEY_ID",
        # Connection pools
        "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
        "DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE", "DB_LOCK_POOL_MAX_SIZE",
        "DB_POOL_WAIT_TIMEOUT", "DB_CONNECT_TIMEOUT", "DB_QUERY_TIMEOUT",
        "EVENT_LOG_DB_HOST", "EVENT_LOG_DB_PORT", "EVENT_LOG_DB_USER",
        "EVENT_LOG_DB_PASSWORD", "EVENT_LOG_DB_NAME",
        "STATS_DB_HOST", "STATS_DB_PORT", "STATS_DB_USER", "STATS_DB_PASSWORD",
        "STATS_DB_NAME",
        "REDIS_URL", "REDIS_SOCKET_TIMEOUT", "REDIS_CONNECT_TIMEOUT",
        # Middleware and routes built when app.main is imported
        "ALLOWED_ORIGINS", "GENERAL_RATE_LIMIT", "GENERAL_RATE_LIMIT_WINDOW_SECONDS",
        "IP_WHITELIST", "IP_WHITELIST_ENABLED", "IP_WHITELIST_ADMIN_ONLY",
        "MCP_ENABLED", "SWAGGER_UI_URL",
        # Feature packs, plugins and background workers started at startup
        "FEATURE_PACKS", "DISABLED_FEATURE_PACKS", "DISABLED_MODULES",
        "FEATURE_PACK_WATCH", "PLUGIN_DIRS", "MATRIX_ENABLED",
        "ENABLE_BACKGROUND_RELATIONSHIPS", "RAG_RELATIONSHIP_WORKERS",
        # Scheduler timezone and intervals fixed when jobs are registered
        "CRON_TIMEZONE", "WEBSITE_CHECK_POLL_SECONDS", "M365_SPAM_PURGE_POLL_SECONDS",
        # Logging sinks configured at startup
        "LOG_LEVEL", "VERBOSE_LOGGING", "APP_LOG_PATH", "FAIL2BAN_LOG_PATH",
        "ERROR_LOG_PATH", "LOG_ROTATION", "LOG_RETENTION", "LOG_COMPRESSION",
    }
)


def _field_env_names(name: str) -> set[str]:
    alias = Settings.model_fields[name].validation_alias
    if alias is None:
        return {name.upper()}
    if isinstance(alias, str):
        return {alias.upper()}
    return {str(choice).upper() for choice in getattr(alias, "choices", ())}


# Settings fields whose value must stay as loaded at startup.
RESTART_REQUIRED_FIELDS: frozenset[str] = frozenset(
    name for name in Settings.model_fields if _field_env_names(name) & RESTART_REQUIRED_KEYS
)


def env_file_paths() -> list[Path]:
    """Return the environment files the service was started from, base first.

    ``MYPORTAL_ENV_FILE`` overrides the base file; otherwise it is the release's
    ``.env``, which production links to ``/etc/myportal.env``.  The per-slot
    file mirrors systemd's ``EnvironmentFile=-/etc/myportal.%i.env``.
    """
    configured = os.getenv("MYPORTAL_ENV_FILE") or str(_PROJECT_ROOT / ".env")
    base = Path(configured).resolve()
    paths = [base]
    slot = get_settings().app_instance_id.strip().lower()
    if slot in {"blue", "green"}:
        paths.append(base.with_name(f"{base.stem}.{slot}{base.suffix}"))
    return paths


def status_dir() -> Path:
    return _PROJECT_ROOT / "var" / "state" / "env-reload"


@dataclass
class _Snapshot:
    values: dict[str, str]
    digests: dict[str, str | None]

    @property
    def fingerprint(self) -> str:
        return "|".join(f"{path}={digest}" for path, digest in sorted(self.digests.items()))


def read_env_files(paths: list[Path]) -> _Snapshot | None:
    """Read ``paths`` (later files win), or ``None`` when the base is absent.

    Raises ``OSError`` when a file exists but cannot be read, so a permission
    problem never looks like every key being removed.
    """
    if not paths or not paths[0].is_file():
        return None
    values: dict[str, str] = {}
    digests: dict[str, str | None] = {}
    for path in paths:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            digests[str(path)] = None
            continue
        digests[str(path)] = hashlib.sha256(raw).hexdigest()
        parsed = dotenv_values(stream=StringIO(raw.decode("utf-8")), interpolate=False)
        values.update({key: value for key, value in parsed.items() if value is not None})
    return _Snapshot(values, digests)


def _safe_errors(exc: ValidationError) -> list[str]:
    # The default message echoes input values, which may be secrets.
    return [
        ".".join(str(part) for part in error.get("loc", ())) + ": " + str(error.get("msg", ""))
        for error in exc.errors(include_input=False, include_url=False)
    ]


@dataclass
class ReloadResult:
    applied: list[str] = field(default_factory=list)
    restart_required: list[str] = field(default_factory=list)
    error: str | None = None


class EnvReloader:
    """Watch the environment files and apply changes to this worker."""

    def __init__(
        self,
        *,
        paths: list[Path] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        settings_getter: Callable[[], Settings] = get_settings,
        state_dir: Path | None = None,
    ) -> None:
        self._paths = paths
        self._poll_seconds = poll_seconds
        self._settings_getter = settings_getter
        self._state_dir = state_dir
        self._snapshot: _Snapshot | None = None
        self._startup: dict[str, str] = {}
        self._candidate: str | None = None
        self._rejected: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_result = ReloadResult()

    @property
    def paths(self) -> list[Path]:
        if self._paths is None:
            self._paths = env_file_paths()
        return self._paths

    def prime(self) -> bool:
        """Record the files as loaded at startup; ``False`` disables watching."""
        try:
            self._snapshot = read_env_files(self.paths)
        except (OSError, UnicodeError) as exc:
            log_warning("Environment file cannot be read; live reload is off", error=str(exc))
            return False
        if self._snapshot is None:
            log_debug("No environment file to watch; live reload is off", path=str(self.paths[0]))
            return False
        self._startup = dict(self._snapshot.values)
        self._write_status()
        return True

    def poll(self) -> ReloadResult | None:
        """Apply a change once the files have been stable for one poll."""
        if self._snapshot is None:
            return None
        try:
            current = read_env_files(self.paths)
        except (OSError, UnicodeError) as exc:
            log_warning("Environment file cannot be read; keeping current settings", error=str(exc))
            return None
        if current is None or current.fingerprint == self._snapshot.fingerprint:
            self._candidate = None
            return None
        if current.fingerprint == self._rejected:
            return None
        if current.fingerprint != self._candidate:
            # Wait one interval so a file still being written is not applied.
            self._candidate = current.fingerprint
            return None
        self._candidate = None
        return self.apply(current)

    def apply(self, current: _Snapshot) -> ReloadResult:
        previous = self._snapshot.values if self._snapshot else {}
        changed = sorted(
            key for key in set(previous) | set(current.values) if previous.get(key) != current.values.get(key)
        )
        live = [key for key in changed if key.upper() not in RESTART_REQUIRED_KEYS]
        restart = [key for key in changed if key.upper() in RESTART_REQUIRED_KEYS]
        # Reported until a restart loads them, unless the file is changed back.
        pending = sorted(
            key
            for key in set(self._startup) | set(current.values)
            if key.upper() in RESTART_REQUIRED_KEYS and self._startup.get(key) != current.values.get(key)
        )

        saved = {key: os.environ.get(key) for key in live}
        for key in live:
            if key in current.values:
                os.environ[key] = current.values[key]
            elif os.environ.get(key) == previous.get(key):
                # Removed from the file: drop it unless something else set it.
                os.environ.pop(key, None)

        try:
            candidate = Settings()
        except Exception as exc:  # noqa: BLE001 - any failure keeps the running settings
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            # Messages of other errors may quote a value, so report only the type.
            errors = _safe_errors(exc) if isinstance(exc, ValidationError) else [type(exc).__name__]
            log_error("Environment file change rejected; keeping current settings", errors=errors)
            self._rejected = current.fingerprint
            self._last_result = ReloadResult(error="; ".join(errors))
            self._write_status(current)
            return self._last_result

        settings = self._settings_getter()
        for name in Settings.model_fields:
            if name in RESTART_REQUIRED_FIELDS:
                continue
            value = getattr(candidate, name)
            if getattr(settings, name) != value:
                setattr(settings, name, value)

        self._snapshot = current
        self._rejected = None
        self._last_result = ReloadResult(applied=live, restart_required=pending)
        if live:
            log_info("Environment file change applied without a restart", keys=live)
        if restart:
            log_warning("Environment file change needs a service restart to take effect", keys=restart)
        self._write_status()
        return self._last_result

    def _write_status(self, seen: _Snapshot | None = None) -> None:
        """Record what this worker has loaded; key names only, never values."""
        directory = self._state_dir or status_dir()
        slot = self._settings_getter().app_instance_id.strip().lower() or "app"
        files = seen or self._snapshot
        payload = {
            "pid": os.getpid(),
            "slot": slot,
            "release": str(_PROJECT_ROOT),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": files.digests if files else {},
            "applied": self._last_result.applied,
            "restart_required": self._last_result.restart_required,
            "error": self._last_result.error,
        }
        target = directory / f"{slot}-{os.getpid()}.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            staging = target.with_suffix(".tmp")
            staging.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            os.replace(staging, target)
        except OSError as exc:
            log_debug("Could not record environment reload status", error=str(exc))

    def _remove_status(self) -> None:
        slot = self._settings_getter().app_instance_id.strip().lower() or "app"
        with suppress(OSError):
            ((self._state_dir or status_dir()) / f"{slot}-{os.getpid()}.json").unlink()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                self.poll()
            except Exception as exc:  # pragma: no cover - never stop watching
                log_error("Environment reload check failed", error=str(exc))

    def start(self) -> None:
        if self._task is None and self.prime():
            self._task = asyncio.create_task(self._run(), name="env-reload")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._remove_status()


def poll_seconds_from_env() -> float:
    """``MYPORTAL_ENV_RELOAD_SECONDS`` sets the interval; 0 turns reload off."""
    try:
        return max(float(os.getenv("MYPORTAL_ENV_RELOAD_SECONDS", DEFAULT_POLL_SECONDS)), 0.0)
    except ValueError:
        return DEFAULT_POLL_SECONDS
