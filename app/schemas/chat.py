from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ChatRoomStatus(str, Enum):
    open = "open"
    closed = "closed"


class ParticipantRole(str, Enum):
    creator = "creator"
    technician = "technician"
    admin = "admin"
    guest = "guest"


class InviteStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    accepted = "accepted"
    expired = "expired"
    revoked = "revoked"


class DeliveryMethod(str, Enum):
    email = "email"
    sms = "sms"
    manual = "manual"


class ChatRoomCreate(BaseModel):
    subject: str = Field(..., min_length=1, max_length=500)
    linked_ticket_id: Optional[int] = None
    e2ee_enabled: bool = False


class ChatRoomResponse(BaseModel):
    id: int
    matrix_room_id: Optional[str]
    room_alias: Optional[str]
    created_by_user_id: int
    company_id: int
    subject: str
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    last_message_at: Optional[datetime]
    linked_ticket_id: Optional[int]
    e2ee_enabled: bool = False
    participant_count: Optional[int] = 0
    message_count: Optional[int] = 0


class ChatMessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=65535)


class ChatMessageResponse(BaseModel):
    id: int
    room_id: int
    matrix_event_id: Optional[str]
    sender_matrix_id: str
    sender_user_id: Optional[int]
    sender_display_name: Optional[str] = None
    body: Optional[str]
    msgtype: str
    sent_at: datetime
    redacted_at: Optional[datetime]


class ExternalInviteCreate(BaseModel):
    target_email: Optional[str] = Field(default=None, max_length=255)
    target_phone: Optional[str] = Field(default=None, max_length=32)
    target_display_name: str = Field(..., min_length=1, max_length=255)
    delivery_method: DeliveryMethod = DeliveryMethod.email


class InviteResponse(BaseModel):
    id: int
    room_id: int
    target_email: Optional[str]
    target_phone: Optional[str]
    target_display_name: Optional[str]
    provisioned_matrix_user_id: Optional[str]
    delivery_method: str
    status: str
    expires_at: Optional[datetime]
    created_at: datetime


class MatrixSettingsUpdate(BaseModel):
    homeserver_url: Optional[str] = None
    server_name: Optional[str] = None
    bot_user_id: Optional[str] = None
    bot_access_token: Optional[str] = None
    is_self_hosted: bool = False
    admin_access_token: Optional[str] = None
    default_room_preset: str = "private_chat"
    e2ee_enabled: bool = False
    invite_domain: Optional[str] = None
