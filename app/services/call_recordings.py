from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx
from loguru import logger

from app.repositories import call_recordings as call_recordings_repo
from app.repositories import integration_modules as modules_repo


_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


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
            errors.append(message)
            continue
        except OSError as exc:  # pragma: no cover - filesystem dependent
            message = f"Unable to read metadata file {candidate}: {exc}"
            logger.warning(message)
            errors.append(message)
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


async def sync_recordings_from_filesystem(recordings_path: str) -> dict[str, Any]:
    """Discover recordings on disk and persist them to the database."""
    base_path = Path(recordings_path).expanduser()
    if not base_path.exists() or not base_path.is_dir():
        raise FileNotFoundError(f"Recordings path does not exist: {recordings_path}")

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    for audio_file in _iter_audio_files(base_path):
        metadata = _load_json_metadata(audio_file, errors=errors)
        transcription = _read_transcription(audio_file, metadata)
        transcription_status = (
            _first_non_empty(metadata, "transcription_status", "transcriptionStatus")
            or ("completed" if transcription else metadata.get("status"))
        )
        if not transcription_status:
            transcription_status = "completed" if transcription else "pending"

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

        caller_number = _first_non_empty(
            metadata,
            "caller_number",
            "callerNumber",
            "from",
            "from_number",
            "fromNumber",
            "caller",
        )
        callee_number = _first_non_empty(
            metadata,
            "callee_number",
            "calleeNumber",
            "to",
            "to_number",
            "toNumber",
            "callee",
        )

        existing = await call_recordings_repo.get_call_recording_by_file_path(str(audio_file))
        if existing:
            updates: dict[str, Any] = {}
            if transcription and transcription != existing.get("transcription"):
                updates["transcription"] = transcription
                updates["transcription_status"] = transcription_status or "completed"
            elif (
                not transcription
                and transcription_status
                and transcription_status != existing.get("transcription_status")
            ):
                updates["transcription_status"] = transcription_status

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
                caller_number=str(caller_number) if caller_number else None,
                callee_number=str(callee_number) if callee_number else None,
                call_date=call_date,
                duration_seconds=duration,
                transcription=transcription,
                transcription_status=transcription_status,
            )
            created += 1
        except Exception as exc:  # pragma: no cover - database dependent
            message = f"Failed to persist call recording {audio_file}: {exc}"
            logger.error(message)
            errors.append(message)

    return {
        "status": "ok",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
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

    try:
        # Call WhisperX API
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=300.0) as client:
            # Read the audio file and prepare for upload
            file_path = recording["file_path"]

            # Open and read the file
            try:
                with open(file_path, "rb") as audio_file:
                    files = {"audio_file": (recording["file_name"], audio_file, "audio/wav")}

                    # Prepare form data if language is specified
                    data = {}
                    if settings.get("language"):
                        data["language"] = settings.get("language")

                    response = await client.post(
                        f"{base_url.rstrip('/')}/asr",
                        files=files,
                        data=data if data else None,
                        headers=headers,
                    )
                    response.raise_for_status()
                    result = response.json()
            except FileNotFoundError:
                logger.error(f"Audio file not found: {file_path}")
                await call_recordings_repo.update_call_recording(
                    recording_id,
                    transcription_status="failed",
                )
                raise ValueError(f"Audio file not found: {file_path}")

            transcription = result.get("text", "")

            # Update recording with transcription
            updated = await call_recordings_repo.update_call_recording(
                recording_id,
                transcription=transcription,
                transcription_status="completed",
            )

            logger.info(f"Successfully transcribed recording {recording_id}")
            return updated

    except httpx.HTTPError as e:
        logger.error(f"Failed to transcribe recording {recording_id}: {e}")
        await call_recordings_repo.update_call_recording(
            recording_id,
            transcription_status="failed",
        )
        raise ValueError(f"Failed to transcribe recording: {e}")
    except Exception as e:
        logger.error(f"Unexpected error transcribing recording {recording_id}: {e}")
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

    # Determine caller/callee names
    caller_name = "Unknown Caller"
    if recording.get("caller_first_name") and recording.get("caller_last_name"):
        caller_name = f"{recording['caller_first_name']} {recording['caller_last_name']}"
    elif recording.get("caller_number"):
        caller_name = recording["caller_number"]

    callee_name = "Unknown Callee"
    if recording.get("callee_first_name") and recording.get("callee_last_name"):
        callee_name = f"{recording['callee_first_name']} {recording['callee_last_name']}"
    elif recording.get("callee_number"):
        callee_name = recording["callee_number"]

    # Build full description with summary and link to transcript
    call_date = recording.get("call_date")
    call_date_str = call_date.strftime("%Y-%m-%d %H:%M:%S") if isinstance(call_date, datetime) else "Unknown"
    description = f"""**Call Recording Summary**

**Date:** {call_date_str}
**Caller:** {caller_name}
**Callee:** {callee_name}
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
        created_by=user_id,
        status="open",
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
