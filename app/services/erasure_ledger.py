"""Erasure ledger: keeps account anonymisation in force across backup restores.

Anonymisation (:mod:`app.services.anonymisation`) overwrites a person's
details in the live database, but every database backup taken before then
still holds them, and restoring one would bring the person back. The ledger
closes that gap:

* Each completed anonymisation is appended to a small file kept outside the
  database (``PRIVACY_ERASURE_LEDGER_PATH``), so restoring a database backup
  never rolls it back.
* :func:`reconcile` runs when MyPortal starts (before it serves requests),
  every few minutes, and when a ledger account's session is used. It re-runs
  the anonymisation for any account a restore brought back, and copies into
  the file any completed request it is missing (anonymisations made before the
  ledger existed, or a write that failed).

An entry holds the user id, the request id, the time and the one-way email
hash the request already keeps; no names or addresses. A restored database
can hand a later sign-up an erased account's id, so an account is only
re-anonymised when its email hash matches. One that doesn't is listed for a
super admin to decide (re-apply, or clear it as a different person), and the
users id counter is moved past every erased id so ids aren't handed out again.

Lines are hash-chained, so an edited or truncated ledger is reported. Entries
are never dropped because of a broken chain: a damaged ledger still erases.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from loguru import logger

from app.core.config import get_settings
from app.repositories import anonymisation as anonymisation_repo

try:  # pragma: no cover - fcntl is always present on the Linux hosts we deploy to
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

LEDGER_VERSION = 1
KIND_ERASED = "erased"
KIND_CLEARED = "cleared"
_KINDS = (KIND_ERASED, KIND_CLEARED)
_GENESIS = "0" * 64
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _PROJECT_ROOT / "var" / "data" / "privacy" / "erasure-ledger.jsonl"
# How long the in-memory index trusts the file before checking it again.
_INDEX_TTL_SECONDS = 5.0
RECONCILE_INTERVAL_SECONDS = 300


def ledger_path() -> Path:
    configured = (get_settings().privacy_erasure_ledger_path or "").strip()
    return Path(configured) if configured else _DEFAULT_PATH


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    kind: str
    user_id: int
    email_hash: str | None
    request_id: int | None
    recorded_at: str
    prev: str
    hash: str


@dataclass
class LedgerRead:
    entries: list[LedgerEntry] = field(default_factory=list)
    # Line numbers and reasons only, never entry contents.
    problems: list[str] = field(default_factory=list)


@dataclass
class _Marks:
    erased: set[str | None] = field(default_factory=set)
    cleared: set[str | None] = field(default_factory=set)
    erased_at: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_fields(fields: Mapping[str, Any]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fields(
    *, seq: int, kind: str, user_id: int, email_hash: str | None, request_id: int | None, recorded_at: str, prev: str
) -> dict[str, Any]:
    return {
        "v": LEDGER_VERSION,
        "seq": seq,
        "kind": kind,
        "user_id": user_id,
        "email_hash": email_hash,
        "request_id": request_id,
        "recorded_at": recorded_at,
        "prev": prev,
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _parse(text: str) -> LedgerRead:
    read = LedgerRead()
    prev = _GENESIS
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            kind = str(data["kind"])
            if kind not in _KINDS:
                raise ValueError("unknown kind")
            email_hash = data.get("email_hash")
            entry = LedgerEntry(
                seq=int(data["seq"]),
                kind=kind,
                user_id=int(data["user_id"]),
                email_hash=str(email_hash) if email_hash else None,
                request_id=_optional_int(data.get("request_id")),
                recorded_at=str(data.get("recorded_at") or ""),
                prev=str(data.get("prev") or ""),
                hash=str(data.get("hash") or ""),
            )
        except (ValueError, TypeError, KeyError):
            # A torn final line from a crash mid-write lands here too.
            read.problems.append("line " + str(number) + ": unreadable entry skipped")
            continue
        expected = _hash_fields(
            _fields(
                seq=entry.seq,
                kind=entry.kind,
                user_id=entry.user_id,
                email_hash=entry.email_hash,
                request_id=entry.request_id,
                recorded_at=entry.recorded_at,
                prev=entry.prev,
            )
        )
        if entry.hash != expected:
            read.problems.append("line " + str(number) + ": entry was changed after it was written")
        elif entry.prev != prev:
            read.problems.append("line " + str(number) + ": chain broken (an entry before it was removed or changed)")
        prev = entry.hash
        read.entries.append(entry)
    return read


def read_ledger(path: Path | None = None) -> LedgerRead:
    target = path or ledger_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LedgerRead()
    return _parse(text)


def _key(kind: str, user_id: int, email_hash: str | None) -> tuple[str, int, str | None]:
    return kind, int(user_id), email_hash or None


def _append(new: Sequence[Mapping[str, Any]], path: Path | None = None) -> int:
    """Append entries not already in the ledger. Returns how many were written.

    The file is locked for the read-check-write, so concurrent workers can't
    interleave lines or chain two entries to the same predecessor.
    """
    if not new:
        return 0
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            text = handle.read()
            current = _parse(text)
            present = {_key(e.kind, e.user_id, e.email_hash) for e in current.entries}
            prev = current.entries[-1].hash if current.entries else _GENESIS
            seq = current.entries[-1].seq if current.entries else 0
            lines: list[str] = []
            for item in new:
                kind = str(item.get("kind") or KIND_ERASED)
                key = _key(kind, int(item["user_id"]), item.get("email_hash"))
                if key in present:
                    continue
                present.add(key)
                seq += 1
                fields = _fields(
                    seq=seq,
                    kind=kind,
                    user_id=key[1],
                    email_hash=key[2],
                    request_id=_optional_int(item.get("request_id")),
                    recorded_at=str(item.get("recorded_at") or _now_iso()),
                    prev=prev,
                )
                fields["hash"] = prev = _hash_fields(fields)
                lines.append(json.dumps(fields, sort_keys=True, separators=(",", ":")))
            if lines:
                # A torn last line would otherwise swallow the first new entry.
                lead = "\n" if text and not text.endswith("\n") else ""
                handle.write(lead + "\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    _invalidate_index()
    return len(lines)


def record_erasure(user_id: int, *, email_hash: str | None, request_id: int | None) -> bool:
    """Record a completed anonymisation. Returns False when it was already recorded."""
    return _append([{"kind": KIND_ERASED, "user_id": user_id, "email_hash": email_hash, "request_id": request_id}]) > 0


def record_cleared(user_id: int, *, email_hash: str | None) -> bool:
    """Record that the account now holding *user_id* is a different person."""
    return _append([{"kind": KIND_CLEARED, "user_id": user_id, "email_hash": email_hash}]) > 0


def import_entries(source: Path) -> int:
    """Merge another copy of the ledger (from off-site storage) into this one."""
    read = read_ledger(source)
    return _append(
        [
            {
                "kind": entry.kind,
                "user_id": entry.user_id,
                "email_hash": entry.email_hash,
                "request_id": entry.request_id,
                "recorded_at": entry.recorded_at,
            }
            for entry in read.entries
        ]
    )


def _build_index(entries: Iterable[LedgerEntry]) -> dict[int, _Marks]:
    index: dict[int, _Marks] = {}
    for entry in entries:
        marks = index.setdefault(entry.user_id, _Marks())
        if entry.kind == KIND_ERASED:
            marks.erased.add(entry.email_hash)
            marks.erased_at = entry.recorded_at or marks.erased_at
        else:
            marks.cleared.add(entry.email_hash)
    return {user_id: marks for user_id, marks in index.items() if marks.erased}


# In-memory index for the per-request session check: (checked_at, file stamp, index).
_index_cache: tuple[float, tuple[int, int] | None, dict[int, _Marks]] | None = None


def _invalidate_index() -> None:
    global _index_cache
    _index_cache = None


def _index() -> dict[int, _Marks]:
    global _index_cache
    now = time.monotonic()
    if _index_cache is not None and now - _index_cache[0] < _INDEX_TTL_SECONDS:
        return _index_cache[2]
    try:
        stat = ledger_path().stat()
        stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    if _index_cache is not None and _index_cache[1] == stamp:
        _index_cache = (now, stamp, _index_cache[2])
        return _index_cache[2]
    index = _build_index(read_ledger().entries) if stamp else {}
    _index_cache = (now, stamp, index)
    return index


# ---------------------------------------------------------------------------
# Reconciling the ledger with the database
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    reason: str
    checked_at: str
    entries: int = 0
    backfilled: int = 0
    reapplied: int = 0
    failed: int = 0
    needs_review: int = 0
    ids_reserved: bool = False
    problems: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "entries": self.entries,
            "backfilled": self.backfilled,
            "reapplied": self.reapplied,
            "failed": self.failed,
            "needs_review": self.needs_review,
            "ids_reserved": self.ids_reserved,
            "problems": len(self.problems),
        }


_last_result: ReconcileResult | None = None
_background: set[asyncio.Task[Any]] = set()


def _compare(
    index: Mapping[int, _Marks], users: Mapping[int, Mapping[str, Any]]
) -> tuple[list[int], list[dict[str, Any]]]:
    """Split ledger accounts into ones to re-anonymise and ones to review.

    Accounts that are missing or already anonymised need nothing.
    """
    reapply: list[int] = []
    review: list[dict[str, Any]] = []
    for user_id in sorted(index):
        row = users.get(user_id)
        if row is None:
            continue
        email = str(row.get("email") or "").strip()
        if anonymisation_repo.is_anonymised_email(email):
            continue
        marks = index[user_id]
        current = anonymisation_repo.email_hash(email)
        if current in marks.erased:
            reapply.append(user_id)
        elif current not in marks.cleared:
            review.append(
                {
                    "user_id": user_id,
                    "email": email,
                    "name": " ".join(
                        part for part in (row.get("first_name"), row.get("last_name")) if part
                    ),
                    "erased_at": marks.erased_at,
                }
            )
    return reapply, review


async def _backfill(entries: Sequence[LedgerEntry]) -> int:
    present = {_key(e.kind, e.user_id, e.email_hash) for e in entries}
    missing = [
        {
            "kind": KIND_ERASED,
            "user_id": int(row["user_id"]),
            "email_hash": row.get("original_email_hash"),
            "request_id": row.get("id"),
            "recorded_at": _iso(row.get("completed_at")),
        }
        for row in await anonymisation_repo.list_completed_requests()
        if _key(KIND_ERASED, int(row["user_id"]), row.get("original_email_hash")) not in present
    ]
    return await asyncio.to_thread(_append, missing)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value) if value else None


async def reconcile(*, reason: str) -> ReconcileResult:
    """Re-apply every recorded anonymisation the database no longer reflects."""
    global _last_result
    from app.services import anonymisation as anonymisation_service

    result = ReconcileResult(reason=reason, checked_at=_now_iso())
    async with anonymisation_repo.ledger_lock():
        read = await asyncio.to_thread(read_ledger)
        result.backfilled = await _backfill(read.entries)
        if result.backfilled:
            read = await asyncio.to_thread(read_ledger)
        result.problems = list(read.problems)
        index = _build_index(read.entries)
        result.entries = len(index)
        if index:
            users = await anonymisation_repo.get_users_by_ids(list(index))
            reapply, review = _compare(index, users)
            result.needs_review = len(review)
            for user_id in reapply:
                try:
                    await anonymisation_service.reapply_erasure(user_id, reason=reason)
                    result.reapplied += 1
                except Exception as exc:
                    result.failed += 1
                    logger.error("Erasure re-apply failed for user {}: {}", user_id, type(exc).__name__)
            try:
                result.ids_reserved = await anonymisation_repo.reserve_user_ids(max(index))
            except Exception as exc:
                logger.warning("Could not move the users id counter past erased ids: {}", type(exc).__name__)
    _last_result = result
    if result.reapplied or result.failed or result.needs_review or result.problems or result.backfilled:
        logger.warning("Erasure ledger reconcile: {}", result.summary())
    return result


def request_reconcile(reason: str) -> None:
    """Start a reconcile in the background unless one is already running."""
    if any(not task.done() for task in _background):
        return
    task = asyncio.create_task(_reconcile_quietly(reason))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _reconcile_quietly(reason: str) -> None:
    try:
        await reconcile(reason=reason)
    except Exception as exc:
        logger.error("Erasure ledger reconcile failed ({}): {}", reason, type(exc).__name__)


async def blocks_session(user_id: int | None) -> bool:
    """True when *user_id* is an erased account that a restore brought back.

    Called for every session load, so it costs a dictionary lookup unless the
    id is in the ledger.
    """
    if user_id is None:
        return False
    try:
        marks = _index().get(int(user_id))
        if marks is None:
            return False
        users = await anonymisation_repo.get_users_by_ids([int(user_id)])
    except Exception as exc:
        # The scheduled check still re-applies; don't fail every page load.
        logger.warning("Erasure ledger session check failed: {}", type(exc).__name__)
        return False
    row = users.get(int(user_id))
    if row is None:
        return False
    email = str(row.get("email") or "")
    if anonymisation_repo.is_anonymised_email(email):
        return True
    if anonymisation_repo.email_hash(email) not in marks.erased:
        return False
    request_reconcile("session")
    return True


# ---------------------------------------------------------------------------
# Super admin view and decisions
# ---------------------------------------------------------------------------


async def status() -> dict[str, Any]:
    """Ledger health and the accounts waiting for a super admin decision."""
    path = ledger_path()
    read = await asyncio.to_thread(read_ledger)
    index = _build_index(read.entries)
    review: list[dict[str, Any]] = []
    pending = 0
    if index:
        reapply, review = _compare(index, await anonymisation_repo.get_users_by_ids(list(index)))
        pending = len(reapply)
    directory = path if path.exists() else path.parent
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    completed = await anonymisation_repo.list_completed_requests()
    recorded = {(e.user_id, e.email_hash) for e in read.entries if e.kind == KIND_ERASED}
    return {
        "path": str(path),
        "exists": path.exists(),
        "writable": os.access(directory, os.W_OK),
        "entries": len(index),
        "problems": read.problems,
        "pending": pending,
        "review": review,
        "unrecorded": sum(
            1 for row in completed if (int(row["user_id"]), row.get("original_email_hash")) not in recorded
        ),
        "last_result": _last_result,
    }


async def _review_row(user_id: int) -> dict[str, Any]:
    read = await asyncio.to_thread(read_ledger)
    index = _build_index(read.entries)
    if int(user_id) not in index:
        raise LookupError("This account isn't in the erasure ledger.")
    _, review = _compare(index, await anonymisation_repo.get_users_by_ids([int(user_id)]))
    if not review:
        raise LookupError("This account doesn't need a decision.")
    return review[0]


async def confirm_and_reapply(user_id: int, *, actor_user_id: int, request: Any = None) -> dict[str, int]:
    """Super admin: the restored account is the erased person; anonymise it again.

    The account's current email hash is recorded too, so restoring the same
    backup again re-applies without asking.
    """
    from app.services import anonymisation as anonymisation_service

    row = await _review_row(user_id)
    await asyncio.to_thread(
        record_erasure, int(user_id), email_hash=anonymisation_repo.email_hash(row["email"]), request_id=None
    )
    return await anonymisation_service.reapply_erasure(
        int(user_id), reason="confirmed", actor_user_id=actor_user_id, request=request
    )


async def clear(user_id: int, *, actor_user_id: int, request: Any = None) -> None:
    """Super admin: the account now holding this id is a different person."""
    from app.services import audit as audit_service

    row = await _review_row(user_id)
    await asyncio.to_thread(record_cleared, int(user_id), email_hash=anonymisation_repo.email_hash(row["email"]))
    try:
        await audit_service.log_action(
            action="account_anonymisation.ledger_clear",
            user_id=actor_user_id,
            entity_type="user",
            entity_id=int(user_id),
            new_value={"user_id": int(user_id)},
            request=request,
        )
    except Exception as exc:
        logger.warning("Erasure ledger audit write failed: {}", type(exc).__name__)
