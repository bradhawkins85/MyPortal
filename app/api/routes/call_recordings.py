from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import call_recordings as call_recordings_repo
from app.schemas.call_recordings import (
    CallRecordingCreate,
    CallRecordingResponse,
    CallRecordingUpdate,
    LinkRecordingRequest,
    TranscriptionRequest,
)
from app.services import call_recordings as call_recordings_service


router = APIRouter(prefix="/api/call-recordings", tags=["Call Recordings"])


@router.get("", response_model=list[CallRecordingResponse])
async def list_call_recordings(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    transcription_status: str | None = Query(default=None, alias="transcriptionStatus"),
    linked_ticket_id: int | None = Query(default=None, alias="linkedTicketId"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """List call recordings (super admin only)."""
    records = await call_recordings_repo.list_call_recordings(
        limit=limit,
        offset=offset,
        search=search,
        transcription_status=transcription_status,
        linked_ticket_id=linked_ticket_id,
    )
    return [CallRecordingResponse.model_validate(record) for record in records]


@router.get("/count", response_model=dict[str, int])
async def count_call_recordings(
    search: str | None = None,
    transcription_status: str | None = Query(default=None, alias="transcriptionStatus"),
    linked_ticket_id: int | None = Query(default=None, alias="linkedTicketId"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Count call recordings with optional filtering (super admin only)."""
    count = await call_recordings_repo.count_call_recordings(
        search=search,
        transcription_status=transcription_status,
        linked_ticket_id=linked_ticket_id,
    )
    return {"count": count}


@router.post("/sync", response_model=dict[str, int | str | list[str]])
async def sync_call_recordings(
    recordings_path: str | None = Query(default=None, alias="recordingsPath"),
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Sync call recordings from filesystem (super admin only)."""
    # Get the recordings path from query param or module settings
    if not recordings_path:
        from app.repositories import integration_modules as modules_repo
        module = await modules_repo.get_module("call-recordings")
        if module and module.get("settings"):
            recordings_path = module["settings"].get("recordings_path")
    
    if not recordings_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No recordings path configured. Please configure the call-recordings module or provide a path.",
        )
    
    try:
        result = await call_recordings_service.sync_recordings_from_filesystem(recordings_path)
        return result
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recordings path does not exist or is not accessible.",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid recordings path configuration.",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sync recordings. Please check the logs for details.",
        )


@router.post("", response_model=CallRecordingResponse, status_code=status.HTTP_201_CREATED)
async def create_call_recording(
    payload: CallRecordingCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Create a new call recording (super admin only)."""
    created = await call_recordings_repo.create_call_recording(
        **payload.model_dump(by_alias=False)
    )
    return CallRecordingResponse.model_validate(created)


@router.get("/{recording_id}", response_model=CallRecordingResponse)
async def get_call_recording(
    recording_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Get a single call recording by ID (super admin only)."""
    recording = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not recording:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call recording not found"
        )
    return CallRecordingResponse.model_validate(recording)


@router.put("/{recording_id}", response_model=CallRecordingResponse)
async def update_call_recording(
    recording_id: int,
    payload: CallRecordingUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Update a call recording (super admin only)."""
    existing = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call recording not found"
        )
    
    updated = await call_recordings_repo.update_call_recording(
        recording_id,
        **payload.model_dump(exclude_unset=True, by_alias=False)
    )
    return CallRecordingResponse.model_validate(updated)


@router.delete("/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_call_recording(
    recording_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Delete a call recording (super admin only)."""
    existing = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call recording not found"
        )
    await call_recordings_repo.delete_call_recording(recording_id)
    return None


@router.post("/{recording_id}/link", response_model=CallRecordingResponse)
async def link_recording_to_ticket(
    recording_id: int,
    payload: LinkRecordingRequest,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Link a call recording to a ticket (super admin only)."""
    existing = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call recording not found"
        )
    
    # Verify ticket exists
    from app.repositories import tickets as tickets_repo
    ticket = await tickets_repo.get_ticket(payload.ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found"
        )
    
    updated = await call_recordings_repo.link_recording_to_ticket(
        recording_id, payload.ticket_id
    )
    return CallRecordingResponse.model_validate(updated)


@router.post("/{recording_id}/unlink", response_model=CallRecordingResponse)
async def unlink_recording_from_ticket(
    recording_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Unlink a call recording from its ticket (super admin only)."""
    existing = await call_recordings_repo.get_call_recording_by_id(recording_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Call recording not found"
        )
    
    updated = await call_recordings_repo.unlink_recording_from_ticket(recording_id)
    return CallRecordingResponse.model_validate(updated)


@router.post("/{recording_id}/transcribe", response_model=CallRecordingResponse)
async def transcribe_recording(
    recording_id: int,
    payload: TranscriptionRequest,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Transcribe a call recording using WhisperX (super admin only)."""
    try:
        updated = await call_recordings_service.transcribe_recording(
            recording_id, force=payload.force
        )
        return CallRecordingResponse.model_validate(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/{recording_id}/create-ticket")
async def create_ticket_from_recording(
    recording_id: int,
    company_id: int = Query(..., alias="companyId"),
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    """Create a ticket from a call recording with AI summary (super admin only)."""
    try:
        ticket = await call_recordings_service.create_ticket_from_recording(
            recording_id,
            company_id=company_id,
            user_id=current_user["id"],
        )
        return {"ticket_id": ticket["id"], "ticket_number": ticket.get("ticket_number")}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
