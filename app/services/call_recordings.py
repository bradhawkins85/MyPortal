from __future__ import annotations

import httpx
from typing import Any

from app.repositories import call_recordings as call_recordings_repo
from app.repositories import integration_modules as modules_repo
from loguru import logger


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
    call_date_str = recording["call_date"].strftime("%Y-%m-%d %H:%M:%S") if recording.get("call_date") else "Unknown"
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
