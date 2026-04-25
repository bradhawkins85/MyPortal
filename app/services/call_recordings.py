from __future__ import annotations

import array
import csv
import json
import re
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx
from loguru import logger

from app.repositories import call_recordings as call_recordings_repo
from app.repositories import integration_modules as modules_repo
from app.services import webhook_monitor


_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}

# Phone system types whose recording layout differs from the generic
# audio-file/title based discovery. Keep in sync with
# ``app.services.modules.CALL_RECORDINGS_PHONE_SYSTEM_TYPES``.
PHONE_SYSTEM_GENERIC = "generic"
PHONE_SYSTEM_GRANDSTREAM_UCM = "grandstream-ucm"
PHONE_SYSTEM_3CX = "3cx"

# Grandstream UCM emits a hidden CSV index per month named e.g.
# ``.rd_files_netdisk_2026-04.csv`` which lives next to a folder called
# ``2026-04`` containing the actual recordings for that month.
_GRANDSTREAM_CSV_PATTERN = re.compile(
    r"^\.rd_files_netdisk_(?P<period>\d{4}-\d{2})\.csv$"
)
_GRANDSTREAM_CSV_GLOB = ".rd_files_netdisk_*.csv"


def _extract_phone_from_title(title: str | None) -> str | None:
    """Extract phone number from recording title."""
    if not title:
        return None
    
    # Pattern 1: Look for +country_code followed by digits (e.g., +61439531124)
    match = re.search(r'\+\d{10,15}', title)
    if match:
        return match.group(0)
    
    # Pattern 2: Look for standalone digits (10-15 digits) without +
    # This should match numbers like 61410553956 but avoid matching dates/times
    match = re.search(r'\b(\d{10,15})\b', title)
    if match:
        return match.group(1)
    
    return None


def _extract_datetime_from_title(title: str | None) -> datetime | None:
    """Extract date and time from recording title."""
    if not title:
        return None
    
    # Pattern: YYYY-MM-DD HH:MM at the end of the title
    match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?:\s|$)', title)
    if match:
        date_str = f"{match.group(1)} {match.group(2)}"
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
            # Assume UTC timezone
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    
    return None


def _read_audio_title(audio_path: Path) -> str | None:
    """Read the title tag from an audio file (MP3 ID3 TIT2 tag)."""
    try:
        # Only try to read ID3 tags from MP3 files
        if audio_path.suffix.lower() == ".mp3":
            from mutagen.mp3 import MP3
            from mutagen.id3 import ID3, TIT2
            
            try:
                audio = MP3(str(audio_path))
                if audio.tags and "TIT2" in audio.tags:
                    title = str(audio.tags["TIT2"])
                    return title.strip() if title else None
            except Exception as exc:
                logger.debug(f"Could not read ID3 tags from {audio_path}: {exc}")
                return None
    except Exception as exc:  # pragma: no cover
        logger.debug(f"Could not read audio metadata from {audio_path}: {exc}")
    
    return None


def _iter_audio_files(base_path: Path) -> list[Path]:
    """Return a sorted list of audio files beneath ``base_path``."""
    files: list[Path] = []
    for path in base_path.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        files.append(path)
    return sorted(files)


def _load_json_metadata(audio_path: Path, *, errors: list[str]) -> dict[str, Any]:
    """Attempt to load a metadata JSON file adjacent to ``audio_path``."""
    candidates = [
        audio_path.with_suffix(".json"),
        audio_path.with_name(f"{audio_path.stem}.json"),
        audio_path.with_name(f"{audio_path.stem}.metadata.json"),
    ]
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive logging
            message = f"Failed to parse metadata JSON {candidate}: {exc}"
            logger.warning(message)
            # Do not expose detailed exception information to the caller
            errors.append("Failed to parse metadata JSON file.")
            continue
        except OSError as exc:  # pragma: no cover - filesystem dependent
            message = f"Unable to read metadata file {candidate}: {exc}"
            logger.warning(message)
            # Do not expose detailed exception information to the caller
            errors.append("Unable to read metadata JSON file.")
            continue

        if isinstance(data, dict):
            metadata = data.get("metadata")
            if isinstance(metadata, dict):
                return metadata
            return data
    return {}


def _read_transcription(audio_path: Path, metadata: dict[str, Any]) -> str | None:
    """Retrieve transcription text from metadata or sidecar files."""
    transcription = metadata.get("transcription") or metadata.get("transcript")
    if isinstance(transcription, str) and transcription.strip():
        return transcription.strip()

    candidates = [
        audio_path.with_suffix(".txt"),
        audio_path.with_name(f"{audio_path.stem}.txt"),
        audio_path.with_name(f"{audio_path.stem}.transcription.txt"),
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8").strip()
                if text:
                    return text
            except OSError:  # pragma: no cover - filesystem dependent
                logger.warning("Unable to read transcription file", file=str(candidate))
                continue
    return None


def _coerce_datetime_value(value: Any) -> datetime | None:
    """Best-effort conversion of metadata values into ``datetime`` objects."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        normalised = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalised)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    return parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
    return None


def _coerce_duration(value: Any) -> int | None:
    """Convert metadata duration values into total seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if ":" in text:
            parts = text.split(":")
            try:
                seconds = 0
                for part in parts:
                    seconds = seconds * 60 + int(part)
                return seconds
            except ValueError:
                return None
        try:
            return int(float(text))
        except ValueError:
            return None
    return None


def _first_non_empty(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value not in (None, ""):
            return value
    return None


def _normalize_phone_system_type(phone_system_type: str | None) -> str:
    """Coerce a phone system type into one of the supported values."""
    candidate = (phone_system_type or "").strip().lower()
    if candidate in {
        PHONE_SYSTEM_GENERIC,
        PHONE_SYSTEM_GRANDSTREAM_UCM,
        PHONE_SYSTEM_3CX,
    }:
        return candidate
    return PHONE_SYSTEM_GENERIC


def _coerce_grandstream_phone(value: Any) -> str | None:
    """Pick a usable phone number from a Grandstream CSV row value."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Some rows use sentinel values such as ``s`` or ``unknown``; skip those.
    if text.lower() in {"s", "unknown", "anonymous", "n/a"}:
        return None
    return text


def _grandstream_pick_phone(row: Mapping[str, Any]) -> str | None:
    """Determine the most useful phone number from a Grandstream CSV row.

    The CSV has both ``caller_num`` and ``callee_num``. Prefer the externally
    visible number (``new_caller_num`` if present), then the caller number,
    then the callee number, then the ``other_num`` field.
    """
    for key in ("new_caller_num", "caller_num", "callee_num", "other_num"):
        candidate = _coerce_grandstream_phone(row.get(key))
        if candidate:
            return candidate
    return None


def _grandstream_parse_create_time(value: Any) -> datetime | None:
    """Parse the ``create_time`` column from a Grandstream CSV row."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # ``create_time`` is typically a unix timestamp (seconds). Fall back to the
    # generic datetime coercion if it is not numeric.
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    return _coerce_datetime_value(text)


def _grandstream_parse_duration(value: Any) -> int | None:
    """Parse the ``duration`` column from a Grandstream CSV row."""
    return _coerce_duration(value)


def _find_grandstream_csv_files(base_path: Path) -> list[Path]:
    """Return Grandstream UCM CSV index files under ``base_path``.

    The index files are hidden (their names start with a dot) so a normal
    ``rglob('*.csv')`` would still return them, but we filter explicitly to
    avoid picking up unrelated CSV files in the same tree.
    """
    matches: list[Path] = []
    for path in base_path.rglob(_GRANDSTREAM_CSV_GLOB):
        if not path.is_file():
            continue
        if not _GRANDSTREAM_CSV_PATTERN.match(path.name):
            continue
        matches.append(path)
    return sorted(matches)


def _resolve_grandstream_audio_path(
    csv_path: Path,
    period: str,
    file_name: str,
    *,
    base_path: Path,
) -> Path | None:
    """Resolve an audio file referenced by a Grandstream CSV row.

    The CSV name encodes a ``YYYY-MM`` period that matches the name of a
    folder containing the recordings for that month. We try, in order:

    1. ``<csv_dir>/<period>/<file_name>`` (the documented layout)
    2. ``<csv_dir>/<file_name>`` (CSV stored alongside the audio files)
    3. A recursive search beneath ``base_path`` for a file with the same name.
    """
    candidate_name = Path(file_name).name  # guard against path traversal in CSV
    candidates = [
        csv_path.parent / period / candidate_name,
        csv_path.parent / candidate_name,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except (OSError, ValueError):
            continue
        if resolved.is_file() and _is_within(resolved, base_path):
            return resolved

    # Fall back to a recursive search for the file name inside base_path.
    for path in base_path.rglob(candidate_name):
        if path.is_file():
            return path
    return None


def _is_within(path: Path, base: Path) -> bool:
    """Return ``True`` when ``path`` is the same as or below ``base``."""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _iter_grandstream_csv_rows(csv_path: Path) -> Iterable[dict[str, Any]]:
    """Yield decoded rows from a Grandstream UCM index CSV.

    The reader uses ``utf-8-sig`` to transparently strip the BOM that
    Grandstream firmware sometimes emits.
    """
    try:
        handle = csv_path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:  # pragma: no cover - filesystem dependent
        logger.warning(f"Unable to open Grandstream CSV {csv_path}: {exc}")
        return
    with handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            yield {
                (key or "").strip(): value
                for key, value in row.items()
                if key is not None
            }


async def _sync_grandstream_ucm(
    base_path: Path,
    *,
    force: bool,
) -> dict[str, Any]:
    """Synchronise recordings using Grandstream UCM CSV index files."""
    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    csv_files = _find_grandstream_csv_files(base_path)
    if not csv_files:
        return {
            "status": "ok",
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [
                "No Grandstream UCM CSV index files (.rd_files_netdisk_YYYY-MM.csv) "
                "were found beneath the configured recordings path."
            ],
        }

    for csv_path in csv_files:
        match = _GRANDSTREAM_CSV_PATTERN.match(csv_path.name)
        if not match:  # pragma: no cover - filtered above
            continue
        period = match.group("period")

        try:
            rows = list(_iter_grandstream_csv_rows(csv_path))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "Failed to read Grandstream CSV index", csv_path=str(csv_path)
            )
            errors.append(f"Failed to read Grandstream CSV {csv_path.name}.")
            continue

        for row in rows:
            file_name = str(row.get("file_name") or "").strip()
            if not file_name:
                continue

            audio_file = _resolve_grandstream_audio_path(
                csv_path, period, file_name, base_path=base_path
            )
            if audio_file is None:
                skipped += 1
                continue

            phone_number = _grandstream_pick_phone(row)
            call_date = _grandstream_parse_create_time(row.get("create_time"))
            if call_date is None:
                # Fall back to file_date (YYYY-MM-DD), then file mtime.
                call_date = _coerce_datetime_value(row.get("file_date"))
            if call_date is None:
                try:
                    call_date = datetime.fromtimestamp(
                        audio_file.stat().st_mtime, tz=timezone.utc
                    )
                except OSError:
                    call_date = datetime.now(tz=timezone.utc)

            duration = _grandstream_parse_duration(row.get("duration"))

            existing = await call_recordings_repo.get_call_recording_by_file_path(
                str(audio_file)
            )
            if existing:
                updates: dict[str, Any] = {}
                if force:
                    if audio_file.name != existing.get("file_name"):
                        updates["file_name"] = audio_file.name
                    if phone_number and phone_number != existing.get("phone_number"):
                        updates["phone_number"] = phone_number
                    if call_date and call_date != existing.get("call_date"):
                        updates["call_date"] = call_date
                    if (
                        duration is not None
                        and duration != existing.get("duration_seconds")
                    ):
                        updates["duration_seconds"] = duration
                    if phone_number:
                        staff_id = await call_recordings_repo.lookup_staff_by_phone(
                            phone_number
                        )
                        if staff_id and staff_id != existing.get("caller_staff_id"):
                            updates["caller_staff_id"] = staff_id
                    if updates:
                        await call_recordings_repo.force_update_call_recording(
                            existing["id"], **updates
                        )
                        updated += 1
                    else:
                        skipped += 1
                else:
                    skipped += 1
                continue

            try:
                await call_recordings_repo.create_call_recording(
                    file_path=str(audio_file),
                    file_name=audio_file.name,
                    phone_number=phone_number,
                    call_date=call_date,
                    duration_seconds=duration,
                    transcription=None,
                    transcription_status="pending",
                )
                created += 1
            except Exception as exc:  # pragma: no cover - database dependent
                logger.error(
                    f"Failed to persist Grandstream call recording {audio_file}: {exc}"
                )
                errors.append(
                    f"Failed to persist call recording {audio_file.name}."
                )

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


async def sync_recordings_from_filesystem(
    recordings_path: str,
    *,
    phone_system_type: str | None = None,
) -> dict[str, Any]:
    """Discover recordings on disk and persist them to the database."""
    # Validate and resolve the path
    try:
        base_path = Path(recordings_path).expanduser().resolve()
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid recordings path: {recordings_path}")
    
    if not base_path.exists() or not base_path.is_dir():
        raise FileNotFoundError(f"Recordings path does not exist: {recordings_path}")

    resolved_phone_system = _normalize_phone_system_type(phone_system_type)
    if resolved_phone_system == PHONE_SYSTEM_GRANDSTREAM_UCM:
        return await _sync_grandstream_ucm(base_path, force=False)
    # ``3cx`` currently shares the generic discovery flow.

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for audio_file in _iter_audio_files(base_path):
        # Try to extract phone number and date from audio file title (MP3 ID3 tag)
        audio_title = _read_audio_title(audio_file)
        phone_from_title = _extract_phone_from_title(audio_title) if audio_title else None
        date_from_title = _extract_datetime_from_title(audio_title) if audio_title else None
        
        metadata = _load_json_metadata(audio_file, errors=errors)
        transcription = _read_transcription(audio_file, metadata)
        transcription_status = (
            _first_non_empty(metadata, "transcription_status", "transcriptionStatus")
            or ("completed" if transcription else metadata.get("status"))
        )
        if not transcription_status:
            transcription_status = "completed" if transcription else "pending"

        # Use date from title if available, otherwise use metadata or file mtime
        call_date = date_from_title
        if call_date is None:
            call_date_value = _first_non_empty(
                metadata,
                "call_date",
                "callDate",
                "started_at",
                "start_time",
                "startTime",
                "timestamp",
                "created_at",
                "createdAt",
            )
            call_date = _coerce_datetime_value(call_date_value)
        if call_date is None:
            call_date = datetime.fromtimestamp(audio_file.stat().st_mtime, tz=timezone.utc)

        duration = _coerce_duration(
            _first_non_empty(
                metadata,
                "duration_seconds",
                "duration",
                "durationSeconds",
                "length",
                "length_seconds",
            )
        )

        # Use phone number from title if available, otherwise use metadata
        phone_number = phone_from_title
        if not phone_number:
            # Fallback to metadata fields (legacy support)
            phone_number = _first_non_empty(
                metadata,
                "phone_number",
                "phoneNumber",
                "caller_number",
                "callerNumber",
                "callee_number",
                "calleeNumber",
                "from",
                "from_number",
                "fromNumber",
                "to",
                "to_number",
                "toNumber",
                "caller",
                "callee",
            )

        existing = await call_recordings_repo.get_call_recording_by_file_path(str(audio_file))
        if existing:
            updates: dict[str, Any] = {}
            if transcription and transcription != existing.get("transcription"):
                updates["transcription"] = transcription
                updates["transcription_status"] = transcription_status or "completed"

            if updates:
                await call_recordings_repo.update_call_recording(existing["id"], **updates)
                updated += 1
            else:
                skipped += 1
            continue

        try:
            await call_recordings_repo.create_call_recording(
                file_path=str(audio_file),
                file_name=audio_file.name,
                phone_number=str(phone_number) if phone_number else None,
                call_date=call_date,
                duration_seconds=duration,
                transcription=transcription,
                transcription_status=transcription_status,
            )
            created += 1
        except Exception as exc:  # pragma: no cover - database dependent
            message = f"Failed to persist call recording {audio_file}: {exc}"
            logger.error(message)
            # Record a sanitized error message for the caller without exception details
            errors.append(f"Failed to persist call recording {audio_file.name}.")

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


async def force_sync_recordings_from_filesystem(
    recordings_path: str,
    *,
    phone_system_type: str | None = None,
) -> dict[str, Any]:
    """
    Force sync recordings from filesystem, reloading all details while preserving ticket linkages and transcriptions.
    
    This is similar to sync_recordings_from_filesystem but updates ALL metadata from the filesystem
    for existing recordings, except for:
    - linked_ticket_id (preserved)
    - transcription (only updated if found in filesystem)
    - labour-related fields (preserved)
    """
    # Validate and resolve the path
    try:
        base_path = Path(recordings_path).expanduser().resolve()
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid recordings path: {recordings_path}")
    
    if not base_path.exists() or not base_path.is_dir():
        raise FileNotFoundError(f"Recordings path does not exist: {recordings_path}")

    resolved_phone_system = _normalize_phone_system_type(phone_system_type)
    if resolved_phone_system == PHONE_SYSTEM_GRANDSTREAM_UCM:
        return await _sync_grandstream_ucm(base_path, force=True)
    # ``3cx`` currently shares the generic discovery flow.

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for audio_file in _iter_audio_files(base_path):
        # Try to extract phone number and date from audio file title (MP3 ID3 tag)
        audio_title = _read_audio_title(audio_file)
        phone_from_title = _extract_phone_from_title(audio_title) if audio_title else None
        date_from_title = _extract_datetime_from_title(audio_title) if audio_title else None
        
        metadata = _load_json_metadata(audio_file, errors=errors)
        transcription = _read_transcription(audio_file, metadata)
        transcription_status = (
            _first_non_empty(metadata, "transcription_status", "transcriptionStatus")
            or ("completed" if transcription else metadata.get("status"))
        )
        if not transcription_status:
            transcription_status = "completed" if transcription else "pending"

        # Use date from title if available, otherwise use metadata or file mtime
        call_date = date_from_title
        if call_date is None:
            call_date_value = _first_non_empty(
                metadata,
                "call_date",
                "callDate",
                "started_at",
                "start_time",
                "startTime",
                "timestamp",
                "created_at",
                "createdAt",
            )
            call_date = _coerce_datetime_value(call_date_value)
        if call_date is None:
            call_date = datetime.fromtimestamp(audio_file.stat().st_mtime, tz=timezone.utc)

        duration = _coerce_duration(
            _first_non_empty(
                metadata,
                "duration_seconds",
                "duration",
                "durationSeconds",
                "length",
                "length_seconds",
            )
        )

        # Use phone number from title if available, otherwise use metadata
        phone_number = phone_from_title
        if not phone_number:
            # Fallback to metadata fields (legacy support)
            phone_number = _first_non_empty(
                metadata,
                "phone_number",
                "phoneNumber",
                "caller_number",
                "callerNumber",
                "callee_number",
                "calleeNumber",
                "from",
                "from_number",
                "fromNumber",
                "to",
                "to_number",
                "toNumber",
                "caller",
                "callee",
            )

        existing = await call_recordings_repo.get_call_recording_by_file_path(str(audio_file))
        if existing:
            # Force update all metadata from filesystem, but preserve ticket linkages and transcriptions
            updates: dict[str, Any] = {}
            
            # Always update file name in case it changed
            if audio_file.name != existing.get("file_name"):
                updates["file_name"] = audio_file.name
            
            # Update phone number if found in filesystem
            if phone_number and phone_number != existing.get("phone_number"):
                updates["phone_number"] = str(phone_number)
            
            # Update call date if found in filesystem
            if call_date and call_date != existing.get("call_date"):
                updates["call_date"] = call_date
            
            # Update duration if found in filesystem
            if duration is not None and duration != existing.get("duration_seconds"):
                updates["duration_seconds"] = duration
            
            # Update transcription only if found in filesystem and different
            if transcription and transcription != existing.get("transcription"):
                updates["transcription"] = transcription
                updates["transcription_status"] = transcription_status or "completed"
            
            # Lookup staff by phone number and update if changed
            if phone_number:
                staff_id = await call_recordings_repo.lookup_staff_by_phone(phone_number)
                if staff_id and staff_id != existing.get("caller_staff_id"):
                    updates["caller_staff_id"] = staff_id

            if updates:
                await call_recordings_repo.force_update_call_recording(existing["id"], **updates)
                updated += 1
            else:
                skipped += 1
            continue

        # Create new recording if not exists
        try:
            await call_recordings_repo.create_call_recording(
                file_path=str(audio_file),
                file_name=audio_file.name,
                phone_number=str(phone_number) if phone_number else None,
                call_date=call_date,
                duration_seconds=duration,
                transcription=transcription,
                transcription_status=transcription_status,
            )
            created += 1
        except Exception as exc:  # pragma: no cover - database dependent
            log_message = f"Failed to persist call recording {audio_file}: {exc}"
            logger.error(log_message)
            errors.append(f"Failed to persist call recording {audio_file}")

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Stereo channel helpers
# ---------------------------------------------------------------------------

# Sample-width → array type-code for little-endian PCM WAV.
_WAV_TYPECODES: dict[int, str] = {1: "b", 2: "h", 4: "l"}


def _split_stereo_wav(file_path: Path) -> tuple[Path, Path] | None:
    """Split a stereo WAV file into two temporary mono channel files.

    For Grandstream UCM recordings the **right** channel carries the **caller**
    and the **left** channel carries the **callee**.

    Returns ``(callee_path, caller_path)`` on success, or ``None`` when the
    file is not a 2-channel PCM WAV (e.g. mono, non-WAV, or unsupported
    sample width).  The caller is responsible for deleting both files.
    """
    try:
        with wave.open(str(file_path), "rb") as wav:
            if wav.getnchannels() != 2:
                return None
            sample_width = wav.getsampwidth()
            frame_rate = wav.getframerate()
            n_frames = wav.getnframes()
            raw_data = wav.readframes(n_frames)
    except (wave.Error, EOFError, OSError):
        return None

    typecode = _WAV_TYPECODES.get(sample_width)
    if typecode is None:
        return None

    samples: array.array = array.array(typecode, raw_data)
    # Stereo samples are interleaved: [L0, R0, L1, R1, …]
    callee_samples = samples[0::2]   # left  channel = callee
    caller_samples = samples[1::2]   # right channel = caller

    stem = file_path.stem
    callee_path = file_path.parent / f"{stem}_callee_ch.wav"
    caller_path = file_path.parent / f"{stem}_caller_ch.wav"

    try:
        for path, channel in ((callee_path, callee_samples), (caller_path, caller_samples)):
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(sample_width)
                out.setframerate(frame_rate)
                out.writeframes(channel.tobytes())
    except (wave.Error, OSError):
        callee_path.unlink(missing_ok=True)
        caller_path.unlink(missing_ok=True)
        return None

    return callee_path, caller_path


def _fmt_time(seconds: float) -> str:
    """Format a timestamp in seconds as ``MM:SS``."""
    secs = max(0, int(seconds))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _parse_whisperx_response(response: httpx.Response) -> tuple[str, list[dict[str, Any]]]:
    """Parse a WhisperX ``/asr`` response.

    Returns ``(text, segments)`` where *segments* is a (possibly empty) list
    of dicts with at least ``start`` and ``text`` keys.
    """
    try:
        result = response.json()
        text = (result.get("text") or "").strip()
        segments = result.get("segments") or []
        return text, [s for s in segments if isinstance(s, dict)]
    except ValueError:
        return response.text.strip(), []


def _build_stereo_transcription(
    caller_text: str,
    caller_segments: list[dict[str, Any]],
    callee_text: str,
    callee_segments: list[dict[str, Any]],
) -> str:
    """Combine caller and callee channel results into a labelled transcription.

    When both channels supply timing segments the output is a single
    chronologically-ordered conversation.  Otherwise two labelled sections
    are returned.
    """
    if caller_segments or callee_segments:
        tagged: list[dict[str, Any]] = []
        for seg in caller_segments:
            tagged.append({"start": seg.get("start", 0.0), "label": "Caller", "text": (seg.get("text") or "").strip()})
        for seg in callee_segments:
            tagged.append({"start": seg.get("start", 0.0), "label": "Callee", "text": (seg.get("text") or "").strip()})
        tagged = [s for s in tagged if s["text"]]
        tagged.sort(key=lambda s: s["start"])
        lines = [f"[{_fmt_time(s['start'])}] **{s['label']}:** {s['text']}" for s in tagged]
        return "\n".join(lines)

    parts: list[str] = []
    if caller_text:
        parts.append(f"**Caller:**\n{caller_text}")
    if callee_text:
        parts.append(f"**Callee:**\n{callee_text}")
    return "\n\n".join(parts)


async def queue_pending_transcriptions() -> dict[str, Any]:
    """
    Queue all pending recordings for transcription.
    
    This marks recordings with status 'pending' as 'queued' to prevent
    duplicate transcription attempts when the scheduled task runs repeatedly.
    
    Returns:
        Dictionary with count of recordings queued
    """
    # Get all recordings that are pending (not yet queued, processing, completed, or failed)
    recordings = await call_recordings_repo.list_call_recordings(
        transcription_status="pending",
        limit=1000,  # Process in batches
    )
    
    queued_count = 0
    for recording in recordings:
        # Mark as queued to prevent re-processing
        await call_recordings_repo.update_call_recording(
            recording["id"],
            transcription_status="queued",
        )
        queued_count += 1
    
    logger.info(f"Queued {queued_count} recordings for transcription")
    
    return {
        "status": "ok",
        "queued": queued_count,
    }


async def process_queued_transcriptions() -> dict[str, Any]:
    """
    Process queued transcriptions one at a time.
    
    This function:
    1. Finds the next queued recording
    2. Attempts to transcribe it
    3. Updates the status based on success or failure
    4. Returns immediately after processing one recording
    
    Failed recordings are marked as 'failed' and will NOT be retried
    automatically. They can be retried by manually updating their status
    back to 'pending' or 'queued'.
    
    Returns:
        Dictionary with processing results
    """
    # Get the next queued recording (oldest first)
    queued_recordings = await call_recordings_repo.list_call_recordings(
        transcription_status="queued",
        limit=1,
    )
    
    if not queued_recordings:
        logger.debug("No queued recordings to process")
        return {
            "status": "ok",
            "processed": 0,
            "details": "No recordings to process",
        }
    
    recording = queued_recordings[0]
    recording_id = recording["id"]
    
    try:
        # Transcribe the recording (this updates status to processing, then completed or failed)
        await transcribe_recording(recording_id, force=False)
        
        logger.info(f"Successfully transcribed recording {recording_id}")
        
        return {
            "status": "ok",
            "processed": 1,
            "recording_id": recording_id,
        }
    except Exception as exc:
        # The transcribe_recording function already marks as failed,
        # but log the error here for monitoring
        logger.error(f"Failed to transcribe recording {recording_id}: {exc}")
        
        return {
            "status": "error",
            "processed": 0,
            "recording_id": recording_id,
            "error": str(exc),
        }


async def transcribe_recording(recording_id: int, *, force: bool = False) -> dict[str, Any]:
    """
    Transcribe a call recording using WhisperX service.

    Args:
        recording_id: ID of the recording to transcribe
        force: If True, re-transcribe even if already done

    Returns:
        Updated recording dict with transcription
    """
    recording = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not recording:
        raise ValueError(f"Recording {recording_id} not found")

    # Check if already transcribed
    if not force and recording.get("transcription") and recording.get("transcription_status") == "completed":
        logger.info(f"Recording {recording_id} already transcribed, skipping")
        return recording
    
    # Skip if already processing (prevents concurrent processing)
    if not force and recording.get("transcription_status") == "processing":
        logger.info(f"Recording {recording_id} already being processed, skipping")
        return recording

    # Get WhisperX module settings
    module = await modules_repo.get_module("whisperx")
    if not module or not module.get("enabled"):
        logger.warning("WhisperX module not enabled")
        await call_recordings_repo.update_call_recording(
            recording_id,
            transcription_status="failed",
        )
        raise ValueError("WhisperX module not enabled")

    settings = module.get("settings", {})
    base_url = settings.get("base_url")
    api_key = settings.get("api_key")

    if not base_url:
        logger.error("WhisperX base URL not configured")
        await call_recordings_repo.update_call_recording(
            recording_id,
            transcription_status="failed",
        )
        raise ValueError("WhisperX base URL not configured")

    # Update status to processing
    await call_recordings_repo.update_call_recording(
        recording_id,
        transcription_status="processing",
    )

    # Prepare webhook event
    file_path = recording["file_path"]
    target_url = f"{base_url.rstrip('/')}/asr"
    
    # Log the transcription attempt
    logger.info(
        "Starting transcription for recording {}: file={}, url={}",
        recording_id,
        recording["file_name"],
        target_url,
    )

    # Create webhook event for tracking
    webhook_event = None
    try:
        webhook_event = await webhook_monitor.create_manual_event(
            name=f"whisperx_transcription_{recording_id}",
            target_url=target_url,
            payload={
                "recording_id": recording_id,
                "file_name": recording["file_name"],
                "file_path": file_path,
                "language": settings.get("language"),
            },
            headers={"Content-Type": "multipart/form-data"},
            max_attempts=1,
        )
        webhook_event_id = webhook_event.get("id") if webhook_event else None
        logger.debug(f"Created webhook event {webhook_event_id} for transcription tracking")
    except Exception as exc:
        logger.warning(f"Failed to create webhook event for transcription tracking: {exc}")
        webhook_event_id = None

    try:
        # Call WhisperX API
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Log request details (redact authorization header)
        safe_headers = {k: ("***REDACTED***" if k.lower() == "authorization" else v) for k, v in headers.items()}
        logger.debug(
            "WhisperX API request: url={}, headers={}, language={}",
            target_url,
            safe_headers,
            settings.get("language"),
        )

        # Attempt stereo channel split (Grandstream UCM: right=caller, left=callee)
        stereo_split = settings.get("stereo_split", False)
        stereo_channel_paths: tuple[Path, Path] | None = None
        if stereo_split:
            stereo_channel_paths = _split_stereo_wav(Path(file_path))
            if stereo_channel_paths is None:
                logger.info(
                    "Stereo split requested but recording {} is not a 2-channel WAV; "
                    "transcribing as a single channel",
                    recording_id,
                )

        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                data: dict[str, str] = {}
                if settings.get("language"):
                    data["language"] = settings["language"]

                if stereo_channel_paths:
                    # --- stereo: transcribe each channel separately then merge ---
                    callee_path, caller_path = stereo_channel_paths
                    try:
                        logger.debug(
                            "Sending caller channel to WhisperX for recording {}: size={} bytes",
                            recording_id,
                            caller_path.stat().st_size,
                        )
                        with open(caller_path, "rb") as cf:
                            resp_caller = await client.post(
                                target_url,
                                files={"audio_file": (recording["file_name"], cf, "audio/wav")},
                                data=data or None,
                                headers=headers,
                            )
                        resp_caller.raise_for_status()
                        caller_text, caller_segs = _parse_whisperx_response(resp_caller)

                        logger.debug(
                            "Sending callee channel to WhisperX for recording {}: size={} bytes",
                            recording_id,
                            callee_path.stat().st_size,
                        )
                        with open(callee_path, "rb") as cf:
                            resp_callee = await client.post(
                                target_url,
                                files={"audio_file": (recording["file_name"], cf, "audio/wav")},
                                data=data or None,
                                headers=headers,
                            )
                        resp_callee.raise_for_status()
                        callee_text, callee_segs = _parse_whisperx_response(resp_callee)
                    finally:
                        callee_path.unlink(missing_ok=True)
                        caller_path.unlink(missing_ok=True)

                    transcription: str | None = _build_stereo_transcription(
                        caller_text, caller_segs, callee_text, callee_segs
                    )
                    result: dict | None = {"text": transcription}
                    response = resp_callee  # use last response for webhook logging

                    logger.info(
                        "WhisperX stereo transcription completed for recording {}: length={}",
                        recording_id,
                        len(transcription),
                    )

                else:
                    # --- mono: single channel (original behaviour) ---
                    with open(file_path, "rb") as audio_file:
                        files = {"audio_file": (recording["file_name"], audio_file, "audio/wav")}

                        logger.debug(f"Sending audio file to WhisperX: size={Path(file_path).stat().st_size} bytes")

                        response = await client.post(
                            target_url,
                            files=files,
                            data=data if data else None,
                            headers=headers,
                        )

                        # Log response details
                        logger.info(
                            "WhisperX API response: status={}, content_length={}",
                            response.status_code,
                            len(response.content),
                        )

                        response.raise_for_status()

                        # Try to parse as JSON first
                        transcription = None
                        result = None
                        try:
                            result = response.json()
                            logger.debug(f"WhisperX response body (JSON): {result}")
                            transcription = result.get("text", "")
                        except ValueError as exc:
                            # Not JSON - check if it's plain text
                            content_type = response.headers.get("content-type", "").lower()
                            if "text/plain" in content_type or not content_type.startswith("application/json"):
                                # Accept plain text response
                                transcription = response.text.strip()
                                logger.info(
                                    "WhisperX returned plain text response for recording {}: length={}",
                                    recording_id,
                                    len(transcription),
                                )
                            else:
                                # Unexpected content type
                                error_message = f"Invalid response format (content-type: {content_type}): {response.text[:500]}"
                                logger.error(
                                    "Invalid response format while transcribing recording {}: {}",
                                    recording_id,
                                    response.text[:500],
                                )

                                # Record webhook failure
                                if webhook_event_id:
                                    try:
                                        await webhook_monitor.record_manual_failure(
                                            webhook_event_id,
                                            attempt_number=1,
                                            status="failed",
                                            error_message=error_message,
                                            response_status=response.status_code,
                                            response_body=response.text[:4000],
                                            request_headers=safe_headers,
                                            request_body={"file_name": recording["file_name"]},
                                            response_headers=dict(response.headers),
                                        )
                                    except Exception as webhook_exc:
                                        logger.warning(f"Failed to record webhook failure: {webhook_exc}")

                                await call_recordings_repo.update_call_recording(
                                    recording_id,
                                    transcription_status="failed",
                                )
                                raise ValueError("Invalid response from transcription service") from exc

            except FileNotFoundError:
                error_message = f"Audio file not found: {file_path}"
                logger.error(error_message)

                # Record webhook failure
                if webhook_event_id:
                    try:
                        await webhook_monitor.record_manual_failure(
                            webhook_event_id,
                            attempt_number=1,
                            status="error",
                            error_message=error_message,
                            response_status=None,
                            response_body=None,
                            request_headers=safe_headers,
                            request_body={"file_path": file_path},
                        )
                    except Exception as webhook_exc:
                        logger.warning(f"Failed to record webhook failure: {webhook_exc}")

                await call_recordings_repo.update_call_recording(
                    recording_id,
                    transcription_status="failed",
                )
                raise ValueError(error_message)

            # Transcription is already set from either JSON or plain text above
            if not transcription:
                error_message = "Empty transcription received from WhisperX"
                logger.error(error_message)
                await call_recordings_repo.update_call_recording(
                    recording_id,
                    transcription_status="failed",
                )
                raise ValueError(error_message)

            logger.info(
                "Transcription completed for recording {}: length={}",
                recording_id,
                len(transcription),
            )

            # Record webhook success
            if webhook_event_id:
                try:
                    # Use result for JSON responses, or response.text for plain text
                    response_body_for_log = json.dumps(result)[:4000] if result else response.text[:4000]
                    await webhook_monitor.record_manual_success(
                        webhook_event_id,
                        attempt_number=1,
                        response_status=response.status_code,
                        response_body=response_body_for_log,
                        request_headers=safe_headers,
                        request_body={"file_name": recording["file_name"]},
                        response_headers=dict(response.headers),
                    )
                except Exception as webhook_exc:
                    logger.warning(f"Failed to record webhook success: {webhook_exc}")

            # Update recording with transcription
            updated = await call_recordings_repo.update_call_recording(
                recording_id,
                transcription=transcription,
                transcription_status="completed",
            )

            logger.info(f"Successfully transcribed recording {recording_id}")
            return updated

    except ValueError as e:
        # ValueError is raised for known errors (file not found, invalid JSON)
        # These have already been logged and webhook recorded, so just re-raise
        raise
    except httpx.HTTPError as e:
        error_message = f"HTTP error during transcription: {str(e)}"
        logger.error(f"Failed to transcribe recording {recording_id}: {error_message}")
        
        # Extract response details if available
        response_status = None
        response_body = None
        response_headers = None
        if hasattr(e, "response") and e.response is not None:
            response_status = e.response.status_code
            response_body = e.response.text[:4000]
            response_headers = dict(e.response.headers)
            logger.error(
                "WhisperX error response: status={}, body={}",
                response_status,
                response_body[:500],
            )
        
        # Record webhook failure
        if webhook_event_id:
            try:
                await webhook_monitor.record_manual_failure(
                    webhook_event_id,
                    attempt_number=1,
                    status="failed",
                    error_message=error_message,
                    response_status=response_status,
                    response_body=response_body,
                    request_headers=safe_headers,
                    request_body={"file_name": recording["file_name"]},
                    response_headers=response_headers,
                )
            except Exception as webhook_exc:
                logger.warning(f"Failed to record webhook failure: {webhook_exc}")
        
        await call_recordings_repo.update_call_recording(
            recording_id,
            transcription_status="failed",
        )
        raise ValueError(f"Failed to transcribe recording: {e}")
        
    except Exception as e:
        error_message = f"Unexpected error: {str(e)}"
        logger.error(f"Unexpected error transcribing recording {recording_id}: {error_message}")
        
        # Record webhook failure
        if webhook_event_id:
            try:
                await webhook_monitor.record_manual_failure(
                    webhook_event_id,
                    attempt_number=1,
                    status="error",
                    error_message=error_message,
                    response_status=None,
                    response_body=None,
                    request_headers=safe_headers,
                    request_body={"file_name": recording["file_name"]},
                )
            except Exception as webhook_exc:
                logger.warning(f"Failed to record webhook failure: {webhook_exc}")
        
        await call_recordings_repo.update_call_recording(
            recording_id,
            transcription_status="failed",
        )
        raise


async def summarize_transcription(transcription: str) -> str:
    """
    Summarize a call transcription using Ollama.

    Args:
        transcription: The full transcription text

    Returns:
        A summary of the transcription suitable for a ticket description
    """
    if not transcription or not transcription.strip():
        return "No transcription available to summarize."

    # Get Ollama module settings
    module = await modules_repo.get_module("ollama")
    if not module or not module.get("enabled"):
        logger.warning("Ollama module not enabled for summarization")
        return transcription[:500] + ("..." if len(transcription) > 500 else "")

    settings = module.get("settings", {})
    base_url = settings.get("base_url")
    model = settings.get("model", "llama3")

    if not base_url:
        logger.warning("Ollama base URL not configured")
        return transcription[:500] + ("..." if len(transcription) > 500 else "")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            prompt = f"""Summarize the following call transcription into a concise ticket description.
Focus on the main issue, request, or topic discussed. Keep it under 200 words.

Transcription:
{transcription}

Summary:"""

            response = await client.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()
            result = response.json()

            summary = result.get("response", "").strip()
            return summary if summary else transcription[:500] + ("..." if len(transcription) > 500 else "")

    except Exception as e:
        logger.error(f"Failed to summarize transcription: {e}")
        # Fall back to truncated transcription
        return transcription[:500] + ("..." if len(transcription) > 500 else "")


async def create_ticket_from_recording(
    recording_id: int,
    *,
    company_id: int,
    user_id: int,
) -> dict[str, Any]:
    """
    Create a ticket from a call recording with summarized transcription.

    Args:
        recording_id: ID of the call recording
        company_id: Company ID for the ticket
        user_id: User ID creating the ticket

    Returns:
        Created ticket dict
    """
    from app.repositories import tickets as tickets_repo

    recording = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not recording:
        raise ValueError(f"Recording {recording_id} not found")

    transcription = recording.get("transcription", "")
    if not transcription:
        raise ValueError("Recording has no transcription. Please transcribe it first.")

    # Generate summary for ticket subject and description
    summary = await summarize_transcription(transcription)

    # Create subject from summary (first line or first 100 chars)
    subject_lines = summary.split("\n")
    subject = subject_lines[0][:100] if subject_lines else "Call Recording"

    # Determine staff names and phone number
    staff_name = "Unknown"
    if recording.get("caller_first_name") and recording.get("caller_last_name"):
        staff_name = f"{recording['caller_first_name']} {recording['caller_last_name']}"
    elif recording.get("callee_first_name") and recording.get("callee_last_name"):
        staff_name = f"{recording['callee_first_name']} {recording['callee_last_name']}"
    elif recording.get("phone_number"):
        staff_name = recording["phone_number"]

    # Build full description with summary and link to transcript
    call_date = recording.get("call_date")
    call_date_str = call_date.strftime("%Y-%m-%d %H:%M:%S") if isinstance(call_date, datetime) else "Unknown"
    
    # Use E.164 format for phone number in ticket description
    from app.core.phone_utils import normalize_to_e164
    phone_number = recording.get("phone_number", "Unknown")
    if phone_number != "Unknown":
        e164_number = normalize_to_e164(phone_number)
        phone_number = e164_number if e164_number else phone_number
    
    description = f"""**Call Recording Summary**

**Date:** {call_date_str}
**Phone Number:** {phone_number}
**Staff:** {staff_name}
**Duration:** {recording.get('duration_seconds', 0)} seconds

**Summary:**
{summary}

[View Full Transcript](#transcript-{recording_id})
"""

    # Create the ticket
    ticket = await tickets_repo.create_ticket(
        company_id=company_id,
        subject=subject,
        description=description,
        requester_id=user_id,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category=None,
        module_slug="call-recordings",
        external_reference=None,
    )

    # Link the recording to the ticket
    await call_recordings_repo.link_recording_to_ticket(recording_id, ticket["id"])

    # Add initial reply with full transcription
    await tickets_repo.create_reply(
        ticket_id=ticket["id"],
        author_id=user_id,
        body=f"**Full Call Transcription:**\n\n{transcription}",
        is_internal=True,
    )

    logger.info(f"Created ticket {ticket['id']} from recording {recording_id}")
    return ticket
