"""Backup register repository.

One ``backup_register`` row documents a backup a company relies on. Rows with
``backup_job_id`` add documentation to a status-tracked job from
``backup_jobs``; rows without it are manual entries that are not tracked.
``backup_apps`` is a shared list of backup applications that grows as users
enter new names, so earlier entries can be searched and reused.
"""
from __future__ import annotations

import json
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.core.database import db

MAX_NAME = 200
MAX_APP_NAME = 120
MAX_FREQUENCY = 120
MAX_TEXT = 4000
MAX_DESTINATIONS = 20
MAX_DESTINATION = 255
MAX_KEY_LINKS = 20
MAX_KEY_LABEL = 120
MAX_KEY_URL = 1000
MAX_CREDENTIALS = 20

FREQUENCY_SUGGESTIONS: tuple[str, ...] = (
    "Continuous",
    "Hourly",
    "Every 4 hours",
    "Daily",
    "Twice daily",
    "Weekly",
    "Monthly",
    "Quarterly",
    "Yearly",
    "Manual",
)

_SELECT = """
    SELECT r.id, r.company_id, r.backup_job_id, r.name, r.backup_app_id,
           a.name AS backup_app_name, r.frequency, r.source, r.destinations,
           r.encryption_keys, r.notes, r.created_by, r.created_at, r.updated_at
    FROM backup_register r
    LEFT JOIN backup_apps a ON a.id = r.backup_app_id
"""


# ---------------------------------------------------------------------------
# Input cleaning (pure helpers)
# ---------------------------------------------------------------------------


def _text(value: Any, limit: int, label: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{label} must be {limit} characters or fewer")
    return text


def parse_destinations(raw: Any) -> list[str]:
    """One destination per line; blank lines and duplicates are dropped."""
    destinations: list[str] = []
    for line in str(raw or "").splitlines():
        value = line.strip()
        if not value or value in destinations:
            continue
        if len(value) > MAX_DESTINATION:
            raise ValueError(f"Each destination must be {MAX_DESTINATION} characters or fewer")
        destinations.append(value)
    if len(destinations) > MAX_DESTINATIONS:
        raise ValueError(f"Enter no more than {MAX_DESTINATIONS} destinations")
    return destinations


def _safe_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError("Encryption key links must be http:// or https:// URLs")
    if len(value) > MAX_KEY_URL:
        raise ValueError(f"Encryption key links must be {MAX_KEY_URL} characters or fewer")
    return value


def parse_key_links(raw: Any) -> list[dict[str, str]]:
    """Parse ``Label | URL`` (or bare URL) lines into encryption key links.

    Only http(s) URLs are accepted so a stored link can never run script when
    it is rendered as an anchor.
    """
    links: list[dict[str, str]] = []
    for line in str(raw or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if "|" in value:
            label, _, url = value.rpartition("|")
            label, url = label.strip(), url.strip()
        else:
            label, url = "", value
        url = _safe_url(url)
        label = label or url
        if len(label) > MAX_KEY_LABEL:
            raise ValueError(f"Encryption key labels must be {MAX_KEY_LABEL} characters or fewer")
        links.append({"label": label, "url": url})
    if len(links) > MAX_KEY_LINKS:
        raise ValueError(f"Enter no more than {MAX_KEY_LINKS} encryption key links")
    return links


def parse_ids(values: Iterable[Any]) -> list[int]:
    ids: list[int] = []
    for value in values:
        try:
            item = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in ids:
            ids.append(item)
    if len(ids) > MAX_CREDENTIALS:
        raise ValueError(f"Link no more than {MAX_CREDENTIALS} vault credentials")
    return ids


def clean_entry(values: dict[str, Any], *, require_name: bool = True) -> dict[str, Any]:
    """Validate submitted register fields; raises ``ValueError`` on bad input."""
    name = _text(values.get("name"), MAX_NAME, "Name")
    if require_name and not name:
        raise ValueError("Name is required")
    app_id = parse_ids([values.get("backup_app_id")] if values.get("backup_app_id") else [])
    return {
        "name": name,
        "backup_app_id": app_id[0] if app_id else None,
        "new_backup_app": _text(values.get("new_backup_app"), MAX_APP_NAME, "Backup app"),
        "frequency": _text(values.get("frequency"), MAX_FREQUENCY, "Frequency"),
        "source": _text(values.get("source"), MAX_TEXT, "Source"),
        "destinations": parse_destinations(values.get("destinations")),
        "credential_ids": parse_ids(values.get("credential_ids") or []),
        "key_links": parse_key_links(values.get("key_links")),
        "notes": _text(values.get("notes"), MAX_TEXT, "Notes"),
    }


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    entry = dict(row)
    for key in ("id", "company_id", "backup_job_id", "backup_app_id"):
        if entry.get(key) is not None:
            entry[key] = int(entry[key])
    destinations = _loads(entry.get("destinations"), [])
    entry["destinations"] = [str(item) for item in destinations if item] if isinstance(destinations, list) else []
    keys = _loads(entry.get("encryption_keys"), {})
    if not isinstance(keys, dict):
        keys = {}
    entry["credential_ids"] = parse_ids(keys.get("credential_ids") or [])
    entry["key_links"] = [
        {"label": str(link.get("label") or link.get("url")), "url": str(link["url"])}
        for link in keys.get("links") or []
        if isinstance(link, dict) and str(link.get("url") or "").lower().startswith(("http://", "https://"))
    ]
    return entry


def _encode_keys(cleaned: dict[str, Any]) -> str:
    return json.dumps({"credential_ids": cleaned["credential_ids"], "links": cleaned["key_links"]})


# ---------------------------------------------------------------------------
# Backup applications
# ---------------------------------------------------------------------------


async def list_apps() -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT id, name FROM backup_apps ORDER BY name")
    return [{"id": int(row["id"]), "name": row["name"]} for row in rows]


async def resolve_app(cleaned: dict[str, Any], *, created_by: int | None = None) -> int | None:
    """Return the app id for the submission, creating a typed-in app if new."""
    new_name = cleaned.get("new_backup_app")
    if new_name:
        existing = await db.fetch_one("SELECT id FROM backup_apps WHERE name = %s", (new_name,))
        if existing:
            return int(existing["id"])
        return int(await db.execute_returning_lastrowid(
            "INSERT INTO backup_apps (name, created_by) VALUES (%s, %s)", (new_name, created_by)))
    app_id = cleaned.get("backup_app_id")
    if app_id is None:
        return None
    row = await db.fetch_one("SELECT id FROM backup_apps WHERE id = %s", (app_id,))
    if not row:
        raise ValueError("Choose a backup app from the list")
    return int(row["id"])


# ---------------------------------------------------------------------------
# Register entries
# ---------------------------------------------------------------------------


async def list_entries(company_id: int, *, job_ids: Iterable[int] = ()) -> list[dict[str, Any]]:
    """Manual entries for the company plus the details of its tracked jobs.

    Tracked details are matched by job as well as company so they follow a job
    that Backup history has moved to another company.
    """
    ids = [int(job_id) for job_id in job_ids]
    where = "r.company_id = %s"
    params: list[Any] = [int(company_id)]
    if ids:
        placeholders = ", ".join(["%s"] * len(ids))
        where = f"(r.company_id = %s AND r.backup_job_id IS NULL) OR r.backup_job_id IN ({placeholders})"
        params.extend(ids)
    rows = await db.fetch_all(
        _SELECT + f" WHERE {where} ORDER BY r.name, r.id",  # nosec B608 - placeholders only
        tuple(params),
    )
    return [entry for entry in (_normalise(row) for row in rows) if entry]


async def get_entry(company_id: int, entry_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        _SELECT + " WHERE r.company_id = %s AND r.id = %s", (int(company_id), int(entry_id)))
    return _normalise(row)


async def get_entry_for_job(job_id: int) -> dict[str, Any] | None:
    """Details for a tracked job; callers must check the job belongs to their company."""
    row = await db.fetch_one(_SELECT + " WHERE r.backup_job_id = %s", (int(job_id),))
    return _normalise(row)


async def create_entry(
    company_id: int,
    cleaned: dict[str, Any],
    *,
    backup_app_id: int | None,
    backup_job_id: int | None = None,
    created_by: int | None = None,
) -> int:
    return int(await db.execute_returning_lastrowid(
        """
        INSERT INTO backup_register
            (company_id, backup_job_id, name, backup_app_id, frequency, source,
             destinations, encryption_keys, notes, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            int(company_id), backup_job_id, cleaned["name"] or "", backup_app_id,
            cleaned["frequency"], cleaned["source"], json.dumps(cleaned["destinations"]),
            _encode_keys(cleaned), cleaned["notes"], created_by,
        ),
    ))


async def update_entry(
    company_id: int, entry_id: int, cleaned: dict[str, Any], *, backup_app_id: int | None
) -> None:
    """Update a manual entry owned by the company."""
    await db.execute(
        """
        UPDATE backup_register
        SET name = %s, backup_app_id = %s, frequency = %s, source = %s,
            destinations = %s, encryption_keys = %s, notes = %s
        WHERE company_id = %s AND id = %s
        """,
        (
            cleaned["name"] or "", backup_app_id, cleaned["frequency"], cleaned["source"],
            json.dumps(cleaned["destinations"]), _encode_keys(cleaned), cleaned["notes"],
            int(company_id), int(entry_id),
        ),
    )


async def update_job_entry(
    company_id: int, entry_id: int, cleaned: dict[str, Any], *, backup_app_id: int | None
) -> None:
    """Update a tracked job's details, re-homing them to the job's current company."""
    await db.execute(
        """
        UPDATE backup_register
        SET company_id = %s, name = %s, backup_app_id = %s, frequency = %s, source = %s,
            destinations = %s, encryption_keys = %s, notes = %s
        WHERE id = %s AND backup_job_id IS NOT NULL
        """,
        (
            int(company_id), cleaned["name"] or "", backup_app_id, cleaned["frequency"],
            cleaned["source"], json.dumps(cleaned["destinations"]), _encode_keys(cleaned),
            cleaned["notes"], int(entry_id),
        ),
    )


async def delete_entry(company_id: int, entry_id: int) -> None:
    await db.execute(
        "DELETE FROM backup_register WHERE company_id = %s AND id = %s",
        (int(company_id), int(entry_id)),
    )
