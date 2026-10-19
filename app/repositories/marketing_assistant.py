"""Storage for the Marketing Assistant (:mod:`app.services.marketing_assistant`).

Source websites listed for each marketing page, the assistant's runs (with
their verification report and a snapshot of the page before the run) and the
images it copied from the source websites.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_RUN_COLUMNS = (
    "id, page_id, mode, status, instructions, requested_by_user_id, model, started_at, finished_at, error, "
    "sources_read, sections_written, images_used, claims_verified, claims_rejected, report_json, restored_at"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return default
    return decoded if isinstance(decoded, type(default)) else default


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "page_id": int(row["page_id"]),
        "url": str(row.get("url") or ""),
        "label": str(row.get("label") or "").strip() or None,
        "last_fetched_at": row.get("last_fetched_at"),
        "last_status": row.get("last_status"),
        "last_error": row.get("last_error"),
        "created_at": row.get("created_at"),
    }


def _run_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "page_id": int(row["page_id"]),
        "mode": str(row.get("mode") or "generate"),
        "status": str(row.get("status") or ""),
        "instructions": row.get("instructions") or None,
        "requested_by_user_id": row.get("requested_by_user_id"),
        "model": row.get("model"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "error": row.get("error"),
        "sources_read": int(row.get("sources_read") or 0),
        "sections_written": int(row.get("sections_written") or 0),
        "images_used": int(row.get("images_used") or 0),
        "claims_verified": int(row.get("claims_verified") or 0),
        "claims_rejected": int(row.get("claims_rejected") or 0),
        "report": _decode(row.get("report_json"), {}),
        "restored_at": row.get("restored_at"),
    }


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


async def list_sources(page_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM marketing_assistant_sources WHERE page_id = %s ORDER BY id",
        (int(page_id),),
    )
    return [_source_row(row) for row in rows]


async def get_source(source_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM marketing_assistant_sources WHERE id = %s", (int(source_id),))
    return _source_row(row) if row else None


async def add_source(page_id: int, url: str, label: str | None = None) -> int:
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO marketing_assistant_sources (page_id, url, label, created_at) VALUES (%s, %s, %s, %s)",
        (int(page_id), url, label, _utcnow()),
    ))


async def delete_source(source_id: int) -> None:
    await db.execute("DELETE FROM marketing_assistant_sources WHERE id = %s", (int(source_id),))


async def record_source_fetch(source_id: int, *, status: str, error: str | None) -> None:
    await db.execute(
        "UPDATE marketing_assistant_sources SET last_fetched_at = %s, last_status = %s, last_error = %s "
        "WHERE id = %s",
        (_utcnow(), status[:16], (error or None) and error[:512], int(source_id)),
    )


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


async def create_run(
    *, page_id: int, mode: str, instructions: str | None, requested_by_user_id: int | None
) -> int:
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO marketing_assistant_runs (page_id, mode, status, instructions, requested_by_user_id, "
        "started_at) VALUES (%s, %s, 'running', %s, %s, %s)",
        (int(page_id), mode, instructions, requested_by_user_id, _utcnow()),
    ))


async def finish_run(
    run_id: int,
    *,
    status: str,
    model: str | None,
    error: str | None,
    sources_read: int = 0,
    sections_written: int = 0,
    images_used: int = 0,
    claims_verified: int = 0,
    claims_rejected: int = 0,
    report: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        "UPDATE marketing_assistant_runs SET status = %s, finished_at = %s, model = %s, error = %s, "
        "sources_read = %s, sections_written = %s, images_used = %s, claims_verified = %s, "
        "claims_rejected = %s, report_json = %s, snapshot_json = %s WHERE id = %s",
        (
            status,
            _utcnow(),
            (model or None) and model[:255],
            (error or None) and error[:512],
            int(sources_read),
            int(sections_written),
            int(images_used),
            int(claims_verified),
            int(claims_rejected),
            json.dumps(report or {}, default=str),
            json.dumps(snapshot, default=str) if snapshot is not None else None,
            int(run_id),
        ),
    )


async def list_runs(page_id: int, limit: int = 10) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        f"SELECT {_RUN_COLUMNS} FROM marketing_assistant_runs WHERE page_id = %s "
        "ORDER BY started_at DESC, id DESC LIMIT %s",
        (int(page_id), max(1, int(limit))),
    )
    return [run for run in (_run_row(row) for row in rows) if run]


async def get_run(run_id: int) -> dict[str, Any] | None:
    return _run_row(await db.fetch_one(
        f"SELECT {_RUN_COLUMNS} FROM marketing_assistant_runs WHERE id = %s", (int(run_id),)
    ))


async def get_run_snapshot(run_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT snapshot_json FROM marketing_assistant_runs WHERE id = %s", (int(run_id),))
    if not row:
        return None
    snapshot = _decode(row.get("snapshot_json"), {})
    return snapshot or None


async def has_running_run(page_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM marketing_assistant_runs WHERE page_id = %s AND status = 'running' LIMIT 1",
        (int(page_id),),
    )
    return bool(row)


async def fail_stale_runs(older_than: datetime) -> None:
    """Close runs left 'running' by a process that stopped mid-run."""

    await db.execute(
        "UPDATE marketing_assistant_runs SET status = 'failed', finished_at = %s, "
        "error = 'The run was interrupted before it finished.' WHERE status = 'running' AND started_at < %s",
        (_utcnow(), older_than),
    )


async def mark_restored(run_id: int) -> None:
    await db.execute(
        "UPDATE marketing_assistant_runs SET restored_at = %s WHERE id = %s", (_utcnow(), int(run_id))
    )


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


async def find_image(page_id: int, sha256: str) -> int | None:
    row = await db.fetch_one(
        "SELECT id FROM marketing_assistant_images WHERE page_id = %s AND sha256 = %s LIMIT 1",
        (int(page_id), sha256),
    )
    return int(row["id"]) if row else None


async def store_image(
    *,
    page_id: int,
    sha256: str,
    source_page_url: str,
    image_url: str,
    content_type: str,
    data: bytes,
) -> int:
    existing = await find_image(page_id, sha256)
    if existing:
        return existing
    return int(await db.execute_returning_lastrowid(
        "INSERT INTO marketing_assistant_images (page_id, sha256, source_page_url, image_url, content_type, "
        "byte_size, data, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (int(page_id), sha256, source_page_url[:2048], image_url[:2048], content_type, len(data), data,
         _utcnow()),
    ))


async def get_image(image_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT i.id, i.page_id, i.content_type, i.data, i.source_page_url, i.image_url, p.is_published "
        "FROM marketing_assistant_images AS i JOIN marketing_pages AS p ON p.id = i.page_id WHERE i.id = %s",
        (int(image_id),),
    )
    if not row:
        return None
    data = row.get("data")
    return {
        "id": int(row["id"]),
        "page_id": int(row["page_id"]),
        "content_type": str(row.get("content_type") or "application/octet-stream"),
        "data": bytes(data) if data is not None else b"",
        "source_page_url": row.get("source_page_url"),
        "image_url": row.get("image_url"),
        "page_published": bool(int(row.get("is_published") or 0)),
    }


async def image_in_use(image_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM marketing_page_sections WHERE image_id = %s LIMIT 1", (int(image_id),)
    )
    return bool(row)
