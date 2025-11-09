from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx
from loguru import logger

from app.repositories import call_recordings as call_recordings_repo
from app.repositories import integration_modules as modules_repo
from app.services import webhook_monitor


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
    # Validate and resolve the path
    try:
        base_path = Path(recordings_path).expanduser().resolve()
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid recordings path: {recordings_path}")
    
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

        async with httpx.AsyncClient(timeout=300.0) as client:
            # Open and read the file
            try:
                with open(file_path, "rb") as audio_file:
                    files = {"audio_file": (recording["file_name"], audio_file, "audio/wav")}

                    # Prepare form data if language is specified
                    data = {}
                    if settings.get("language"):
                        data["language"] = settings.get("language")

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
