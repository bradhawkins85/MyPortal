"""Durable, administrator-visible history for system update executions."""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SHARED_ROOT = Path("/opt/myportal/shared")


def _default_shared_root(project_root: Path, default_shared: Path) -> str | None:
    """Locate the shared state tree when MYPORTAL_SHARED_ROOT is not set.

    The application runs from a release below /opt/myportal, but the update
    coordinator imports this module from the control checkout, which older
    installations cloned into /opt/myportal itself. Both must resolve to the
    same history directory, so an existing shared state tree wins over the
    checkout's own var/ directory.
    """
    if str(project_root).startswith(f"{default_shared.parent}/"):
        return str(default_shared)
    if (default_shared / "state").is_dir():
        return str(default_shared)
    return None


_shared_root = os.getenv("MYPORTAL_SHARED_ROOT") or _default_shared_root(
    _PROJECT_ROOT, _DEFAULT_SHARED_ROOT
)
_default_history_dir = (
    Path(_shared_root) / "state/system-updates"
    if _shared_root else _PROJECT_ROOT / "var/state/system-updates"
)


def _resolve_history_dir(default: Path) -> Path:
    # .env.example ships this key blank; treat blank as "use the default"
    # rather than Path(""), which resolves to the (read-only) working directory.
    return Path(os.getenv("MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR") or default)


_HISTORY_DIR = _resolve_history_dir(_default_history_dir)
_MAX_OUTPUT = 32_000
_SECRET_RE = re.compile(
    r"(?i)(authorization|password|passwd|token|secret|api[_-]?key|private[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_VALID_STATUSES = {"pending", "running", "succeeded", "failed"}
_ACTIVE_STATUSES = {"pending", "running"}
_TERMINAL_STATUSES = {"succeeded", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitise_output(value: str | None) -> str:
    """Redact common credential assignments and bound administrator output."""
    cleaned = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value or "")
    if len(cleaned) > _MAX_OUTPUT:
        # Keep the end of the log: it holds the current step and the result.
        cleaned = "…" + cleaned[-(_MAX_OUTPUT - 1):]
    return cleaned


def _record_name(update_id: str) -> str:
    try:
        return f"{uuid.UUID(update_id)}.json"
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Invalid system update identifier") from exc


# The history directory lives in the service-writable shared state tree, and
# the bare-metal coordinator may report into it with elevated privileges.
# Every access therefore goes through a directory descriptor opened without
# following symlinks, and each record is opened with O_NOFOLLOW, so a planted
# symlink or hard link can never redirect a read, write, chmod or chown.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR_FD_SUPPORTED = os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd


def _open_history_dir(*, create: bool) -> int | None:
    if create:
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    if not _DIR_FD_SUPPORTED:
        return None
    return os.open(_HISTORY_DIR, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW)


def _read_record(name: str, dir_fd: int | None) -> Any:
    if dir_fd is None:
        return json.loads((_HISTORY_DIR / name).read_text(encoding="utf-8"))
    fd = os.open(name, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), dir_fd=dir_fd)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        # Only a private regular file is a record: never a FIFO, device, or a
        # hard link to a file elsewhere on the filesystem.
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Refusing to read a non-regular system update record")
        return json.load(handle)


def _write(record: dict[str, Any]) -> dict[str, Any]:
    name = _record_name(str(record["id"]))
    dir_fd = _open_history_dir(create=True)
    temporary = f".system-update-{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(
            temporary if dir_fd is not None else str(_HISTORY_DIR / temporary),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600,
            **({"dir_fd": dir_fd} if dir_fd is not None else {}),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                # Change the file we created through its descriptor, never by
                # path: the path may have been swapped for a symlink meanwhile.
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), 0o600)
                _match_history_dir_owner(handle.fileno(), dir_fd)
                os.fsync(handle.fileno())
            if dir_fd is not None:
                os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            else:
                os.replace(_HISTORY_DIR / temporary, _HISTORY_DIR / name)
        finally:
            try:
                if dir_fd is not None:
                    os.unlink(temporary, dir_fd=dir_fd)
                else:
                    os.unlink(_HISTORY_DIR / temporary)
            except FileNotFoundError:
                # Temporary file may already have been atomically moved by os.replace().
                pass
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    return record


def _match_history_dir_owner(fd: int, dir_fd: int | None) -> None:
    """Give records written by a root caller to the history directory's owner.

    The bare-metal coordinator reports as the service account, but a root
    caller (for example a manual run) must not leave a root-owned 0600 record
    the application cannot read. Only the open descriptor is changed.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or dir_fd is None:
        return
    try:
        owner = os.fstat(dir_fd)
        os.fchown(fd, owner.st_uid, owner.st_gid)
    except OSError:
        pass


def create_pending(
    *, requested_at: str, target_revision: str, source: str,
    mode: str = "rolling", output: str = "Rolling blue/green update queued.",
    from_revision: str | None = None,
) -> dict[str, Any]:
    record = {
        "id": str(uuid.uuid4()), "status": "pending", "mode": mode,
        "source": source, "target_revision": target_revision,
        "from_revision": from_revision or None,
        "started_at": requested_at, "completed_at": None, "updated_at": _now(),
        "output": output, "error": None,
    }
    return _write(record)


def update(update_id: str, *, status: str, output: str | None = None,
           error: str | None = None, completed: bool = False) -> dict[str, Any]:
    if status not in _VALID_STATUSES:
        raise ValueError("Invalid system update status")
    record = get(update_id)
    if record.get("status") in _TERMINAL_STATUSES and status in _ACTIVE_STATUSES:
        # A late progress report must never reopen a finished update.
        return record
    record.update({"status": status, "updated_at": _now()})
    if output is not None:
        record["output"] = sanitise_output(output)
    if error is not None:
        record["error"] = sanitise_output(error)
    if completed:
        record["completed_at"] = _now()
    return _write(record)


def attach_changes(update_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Store the releases or commits an update applied, leaving its status alone."""
    record = get(update_id)
    record["changes"] = changes
    if changes.get("from_revision") and not record.get("from_revision"):
        record["from_revision"] = changes["from_revision"]
    return _write(record)


def get(update_id: str) -> dict[str, Any]:
    name = _record_name(update_id)
    try:
        dir_fd = _open_history_dir(create=False)
    except FileNotFoundError as exc:
        raise KeyError(update_id) from exc
    try:
        record = _read_record(name, dir_fd)
    except FileNotFoundError as exc:
        raise KeyError(update_id) from exc
    except (OSError, ValueError) as exc:
        # A symlink (ELOOP), FIFO, hard link, or corrupt JSON is not a record.
        raise KeyError(update_id) from exc
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    if not isinstance(record, dict):
        raise KeyError(update_id)
    return record


def list_updates(*, limit: int = 200) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        names = sorted(path.name for path in _HISTORY_DIR.glob("*.json"))
        dir_fd = _open_history_dir(create=False)
    except OSError:
        return []
    try:
        for name in names:
            try:
                record = _read_record(name, dir_fd)
                if isinstance(record, dict) and record.get("status") in _VALID_STATUSES:
                    records.append(record)
            except (OSError, ValueError, TypeError):
                continue
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    records.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return records[:limit]


def find_active() -> dict[str, Any] | None:
    """Return the newest update that is still queued or running, if any."""
    for record in list_updates():
        if record.get("status") in _ACTIVE_STATUSES:
            return record
    return None
