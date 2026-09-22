"""Durable, administrator-visible history for system update executions."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_shared_root = os.getenv("MYPORTAL_SHARED_ROOT")
if not _shared_root and str(_PROJECT_ROOT).startswith("/opt/myportal/"):
    _shared_root = "/opt/myportal/shared"
_default_history_dir = (
    Path(_shared_root) / "state/system-updates"
    if _shared_root else _PROJECT_ROOT / "var/state/system-updates"
)
_HISTORY_DIR = Path(os.getenv("MYPORTAL_SYSTEM_UPDATE_HISTORY_DIR", _default_history_dir))
_MAX_OUTPUT = 32_000
_SECRET_RE = re.compile(
    r"(?i)(authorization|password|passwd|token|secret|api[_-]?key|private[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_VALID_STATUSES = {"pending", "running", "succeeded", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitise_output(value: str | None) -> str:
    """Redact common credential assignments and bound administrator output."""
    cleaned = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value or "")
    if len(cleaned) > _MAX_OUTPUT:
        cleaned = cleaned[: _MAX_OUTPUT - 1] + "…"
    return cleaned


def _path(update_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9-]{36}", update_id):
        raise ValueError("Invalid system update identifier")
    return _HISTORY_DIR / f"{update_id}.json"


def _write(record: dict[str, Any]) -> dict[str, Any]:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".system-update-", dir=_HISTORY_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, _path(str(record["id"])))
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return record


def create_pending(*, requested_at: str, target_revision: str, source: str) -> dict[str, Any]:
    record = {
        "id": str(uuid.uuid4()), "status": "pending", "mode": "rolling",
        "source": source, "target_revision": target_revision,
        "started_at": requested_at, "completed_at": None, "updated_at": _now(),
        "output": "Rolling blue/green update queued.", "error": None,
    }
    return _write(record)


def update(update_id: str, *, status: str, output: str | None = None,
           error: str | None = None, completed: bool = False) -> dict[str, Any]:
    if status not in _VALID_STATUSES:
        raise ValueError("Invalid system update status")
    record = get(update_id)
    record.update({"status": status, "updated_at": _now()})
    if output is not None:
        record["output"] = sanitise_output(output)
    if error is not None:
        record["error"] = sanitise_output(error)
    if completed:
        record["completed_at"] = _now()
    return _write(record)


def get(update_id: str) -> dict[str, Any]:
    try:
        record = json.loads(_path(update_id).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KeyError(update_id) from exc
    if not isinstance(record, dict):
        raise KeyError(update_id)
    return record


def list_updates(*, limit: int = 200) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        paths = list(_HISTORY_DIR.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(record, dict) and record.get("status") in _VALID_STATUSES:
                records.append(record)
        except (OSError, ValueError, TypeError):
            continue
    records.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return records[:limit]
