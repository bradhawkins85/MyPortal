from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from app.core.phone_utils import normalize_to_e164


class CallRecordingBase(BaseModel):
    file_path: str = Field(validation_alias="filePath")
    file_name: str = Field(validation_alias="fileName")
    phone_number: Optional[str] = Field(default=None, validation_alias="phoneNumber")
    call_date: datetime = Field(validation_alias="callDate")
    duration_seconds: Optional[int] = Field(default=None, validation_alias="durationSeconds")


class CallRecordingCreate(CallRecordingBase):
    transcription: Optional[str] = None
    transcription_status: str = Field(default="pending", validation_alias="transcriptionStatus")


class CallRecordingUpdate(BaseModel):
    transcription: Optional[str] = None
    transcription_status: Optional[str] = Field(default=None, validation_alias="transcriptionStatus")
    linked_ticket_id: Optional[int] = Field(default=None, validation_alias="linkedTicketId")
    minutes_spent: Optional[int] = Field(default=None, validation_alias="minutesSpent")
    is_billable: Optional[bool] = Field(default=None, validation_alias="isBillable")
    labour_type_id: Optional[int] = Field(default=None, validation_alias="labourTypeId")


class CallRecordingResponse(BaseModel):
    id: int
    file_path: str
    file_name: str
    phone_number: Optional[str] = None
    caller_staff_id: Optional[int] = None
    callee_staff_id: Optional[int] = None
    call_date: datetime
    duration_seconds: Optional[int] = None
    transcription: Optional[str] = None
    transcription_status: str
    linked_ticket_id: Optional[int] = None
    minutes_spent: Optional[int] = None
    is_billable: bool = False
    labour_type_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    
    # Joined fields from staff and tickets
    caller_first_name: Optional[str] = None
    caller_last_name: Optional[str] = None
    caller_email: Optional[str] = None
    callee_first_name: Optional[str] = None
    callee_last_name: Optional[str] = None
    callee_email: Optional[str] = None
    linked_ticket_number: Optional[int] = None
    linked_ticket_subject: Optional[str] = None
    labour_type_name: Optional[str] = None
    labour_type_code: Optional[str] = None

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
    }
    
    @computed_field
    @property
    def phone_number_e164(self) -> Optional[str]:
        """Return phone number in E.164 format if possible, otherwise return original."""
        if not self.phone_number:
            return None
        e164 = normalize_to_e164(self.phone_number)
        return e164 if e164 else self.phone_number


class LinkRecordingRequest(BaseModel):
    ticket_id: int = Field(validation_alias="ticketId")


class TranscriptionRequest(BaseModel):
    recording_id: int = Field(validation_alias="recordingId")
    force: bool = False  # Force re-transcription even if already done
