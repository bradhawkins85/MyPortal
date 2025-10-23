from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import change_log as change_log_repo

_CHANGE_MD_PATTERN = re.compile(
    r"^\s*-\s*(?P<date>\d{4}-\d{2}-\d{2})\s*,\s*(?P<time>\d{2}:\d{2})\s*(?P<tz>[A-Za-z]+)\s*,\s*(?P<type>[^,]+)\s*,\s*(?P<summary>.+)$"
)


@dataclass
class ChangeLogEntry:
    guid: str | None
    occurred_at: datetime
    change_type: str
    summary: str
    source: str
    file_path: Path | None = None

    @property
    def occurred_at_utc(self) -> datetime:
        value = self.occurred_at
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @property
    def content_hash(self) -> str:
        base = "|".join(
            [
                self.occurred_at_utc.isoformat(timespec="minutes"),
                self.change_type.lower(),
                self.summary.strip(),
            ]
        )
        return sha256(base.encode("utf-8")).hexdigest()

    def to_json(self) -> dict[str, str]:
        occurred = self.occurred_at_utc.replace(tzinfo=timezone.utc)
        return {
            "guid": self.guid,
            "occurred_at": _format_datetime(occurred),
            "change_type": self.change_type,
            "summary": self.summary,
            "content_hash": self.content_hash,
        }


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _format_datetime(value: datetime) -> str:
    as_utc = value.astimezone(timezone.utc)
    text = as_utc.isoformat(timespec="minutes")
    return text.replace("+00:00", "Z")


def _parse_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    candidate = text.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_change_type(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    normalised = cleaned.lower()
    if normalised in {"feature", "fix", "change"}:
        return normalised.capitalize()
    return cleaned


def _load_change_file(path: Path) -> ChangeLogEntry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log_error("Unable to parse change log JSON", file=str(path), error=str(exc))
        return None

    occurred_at = _parse_datetime(data.get("occurred_at"))
    change_type = _normalise_change_type(data.get("change_type"))
    summary = data.get("summary")

    if occurred_at is None or change_type is None or not isinstance(summary, str):
        log_error("Invalid change log entry", file=str(path))
        return None

    guid = data.get("guid")
    if isinstance(guid, str):
        guid = guid.strip() or None

    entry = ChangeLogEntry(
        guid=guid,
        occurred_at=occurred_at,
        change_type=change_type,
        summary=summary.strip(),
        source=str(path),
        file_path=path,
    )
    return entry


def _parse_changes_md(path: Path) -> list[ChangeLogEntry]:
    entries: list[ChangeLogEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _CHANGE_MD_PATTERN.match(line)
        if not match:
            continue
        date_part = match.group("date")
        time_part = match.group("time")
        tz_part = (match.group("tz") or "").strip().upper()
        if tz_part and tz_part != "UTC":
            log_error("Unsupported timezone in changes.md entry", timezone=tz_part, line=line.strip())
            continue
        change_type = _normalise_change_type(match.group("type"))
        summary = match.group("summary").strip()
        if change_type is None or not summary:
            continue
        try:
            occurred_at = datetime.fromisoformat(f"{date_part}T{time_part}:00")
        except ValueError:
            continue
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        entries.append(
            ChangeLogEntry(
                guid=None,
                occurred_at=occurred_at,
                change_type=change_type,
                summary=summary,
                source=str(path),
            )
        )
    return entries


def _write_change_file(entry: ChangeLogEntry, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = entry.to_json()
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


async def _persist_entries(entries: Sequence[ChangeLogEntry], *, changes_dir: Path, repository=change_log_repo) -> int:
    stored = 0
    for entry in entries:
        existing = await repository.get_change_by_hash(entry.content_hash)
        if not existing and entry.guid:
            existing = await repository.get_change_by_guid(entry.guid)

        if existing:
            entry.guid = existing.get("guid") or entry.guid
        if not entry.guid:
            entry.guid = str(uuid.uuid4())

        target_path = changes_dir / f"{entry.guid}.json"
        entry.file_path = target_path
        entry.source = str(target_path)

        desired = entry.to_json()
        should_write = True
        if target_path.exists():
            try:
                current = json.loads(target_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                should_write = True
            else:
                should_write = current != desired
        if should_write:
            _write_change_file(entry, target_path)

        await repository.upsert_change(
            guid=entry.guid,
            occurred_at_utc=entry.occurred_at_utc,
            change_type=entry.change_type,
            summary=entry.summary,
            source_file=str(Path("changes") / target_path.name),
            content_hash=entry.content_hash,
        )
        stored += 1
    return stored


async def sync_change_log_sources(*, base_path: Path | None = None, repository=change_log_repo) -> None:
    root = base_path or _project_root()
    changes_dir = root / "changes"
    changes_dir.mkdir(parents=True, exist_ok=True)

    if not db.is_connected() and repository is change_log_repo:
    requires_database = repository is change_log_repo
    if requires_database and not db.is_connected():
        log_info("Skipping change log synchronisation because the database is not connected")
        return

    entries: list[ChangeLogEntry] = []
    for path in sorted(changes_dir.glob("*.json")):
        entry = _load_change_file(path)
        if entry:
            entries.append(entry)

    changes_md = root / "changes.md"
    if changes_md.exists():
        entries.extend(_parse_changes_md(changes_md))

    if not entries:
        return

    stored = await _persist_entries(entries, changes_dir=changes_dir, repository=repository)
    log_info("Change log entries synchronised", total=len(entries), stored=stored)
