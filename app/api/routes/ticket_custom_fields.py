"""REST API for custom ticket details (definitions and per-ticket values)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_helpdesk_technician, require_super_admin
from app.repositories import ticket_custom_fields as repo
from app.repositories import tickets as tickets_repo
from app.services import audit as audit_service
from app.services import ticket_custom_fields as service

router = APIRouter(tags=["Ticket Custom Details"])

FieldTypeLiteral = Literal[
    "text", "textarea", "email", "number", "time", "url", "date",
    "select", "radio", "multiselect", "checkbox", "rating",
]
VisibilityLiteral = Literal["public", "private", "admin_only"]


class TicketCustomFieldDefinitionIn(BaseModel):
    label: str = Field(..., max_length=255, description="Name shown on the ticket.")
    field_key: Optional[str] = Field(
        default=None, max_length=100, description="Key used by automations; generated from the label when blank."
    )
    help_text: Optional[str] = Field(default=None, max_length=500)
    field_type: FieldTypeLiteral = "text"
    options: list[str] = Field(default_factory=list, description="Choices for select, radio and multiselect fields.")
    rating_max: Optional[int] = Field(default=None, ge=1, le=10, description="Stars for a rating field (default 5).")
    visibility: VisibilityLiteral = Field(
        default="private",
        description="public: requesters see it; private: technicians and admins; admin_only: super admins.",
    )
    editable: bool = Field(default=True, description="False makes it read-only on tickets; automations can still set it.")
    show_in_list: bool = Field(default=False, description="Offer the detail as a ticket list column.")
    is_active: bool = True
    sort_order: int = 0


class TicketCustomFieldDefinition(BaseModel):
    id: int
    field_key: str
    label: str
    help_text: Optional[str] = None
    field_type: str
    options: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    visibility: str
    editable: bool
    show_in_list: bool
    is_active: bool
    sort_order: int


class TicketCustomFieldValue(BaseModel):
    field_key: str
    label: str
    field_type: str
    visibility: str
    editable: bool
    can_edit: bool
    value: Any = None
    display: str = ""


class TicketCustomFieldValuesUpdate(BaseModel):
    values: dict[str, Any] = Field(
        ...,
        description="Field key to new value. Use null or an empty string to clear; multiselect takes a list.",
    )


def _definition_payload(body: TicketCustomFieldDefinitionIn) -> dict[str, Any]:
    payload = body.model_dump()
    payload["config"] = {"max": body.rating_max} if body.rating_max else {}
    return payload


def _viewer(current_user: dict) -> dict[str, bool]:
    return {"is_super_admin": bool(current_user.get("is_super_admin")), "is_staff": True}


@router.get(
    "/api/ticket-custom-fields/definitions",
    response_model=list[TicketCustomFieldDefinition],
)
async def list_ticket_custom_field_definitions(
    current_user: dict = Depends(require_helpdesk_technician),
):
    """List custom ticket detail definitions the caller may see."""
    scopes = service.visible_scopes(**_viewer(current_user))
    return [
        TicketCustomFieldDefinition(**definition)
        for definition in await repo.list_definitions()
        if definition["visibility"] in scopes
    ]


@router.post(
    "/api/ticket-custom-fields/definitions",
    response_model=TicketCustomFieldDefinition,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket_custom_field_definition(
    body: TicketCustomFieldDefinitionIn,
    request: Request,
    current_user: dict = Depends(require_super_admin),
):
    """Create a custom ticket detail (super admins)."""
    definition_id, errors = await service.save_definition(_definition_payload(body))
    if errors or not definition_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=" ".join(errors) or "Not saved")
    created = await repo.get_definition(definition_id)
    await audit_service.log_action(
        action="ticket_custom_field.create", user_id=current_user.get("id"),
        entity_type="ticket_custom_field", entity_id=definition_id, new_value=created, request=request,
    )
    return TicketCustomFieldDefinition(**created)


@router.get(
    "/api/ticket-custom-fields/definitions/{definition_id}",
    response_model=TicketCustomFieldDefinition,
)
async def get_ticket_custom_field_definition(
    definition_id: int, current_user: dict = Depends(require_helpdesk_technician)
):
    """Get one custom ticket detail definition."""
    definition = await repo.get_definition(definition_id)
    if not definition or definition["visibility"] not in service.visible_scopes(**_viewer(current_user)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom ticket detail not found")
    return TicketCustomFieldDefinition(**definition)


@router.put(
    "/api/ticket-custom-fields/definitions/{definition_id}",
    response_model=TicketCustomFieldDefinition,
)
async def update_ticket_custom_field_definition(
    definition_id: int,
    body: TicketCustomFieldDefinitionIn,
    request: Request,
    current_user: dict = Depends(require_super_admin),
):
    """Replace a custom ticket detail definition (super admins)."""
    previous = await repo.get_definition(definition_id)
    if not previous:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom ticket detail not found")
    _, errors = await service.save_definition(_definition_payload(body), definition_id=definition_id)
    if errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=" ".join(errors))
    updated = await repo.get_definition(definition_id)
    await audit_service.log_action(
        action="ticket_custom_field.update", user_id=current_user.get("id"),
        entity_type="ticket_custom_field", entity_id=definition_id,
        previous_value=previous, new_value=updated, request=request,
    )
    return TicketCustomFieldDefinition(**updated)


@router.delete(
    "/api/ticket-custom-fields/definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_ticket_custom_field_definition(
    definition_id: int, request: Request, current_user: dict = Depends(require_super_admin)
):
    """Delete a custom ticket detail and every saved value (super admins)."""
    previous = await repo.get_definition(definition_id)
    if not previous:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom ticket detail not found")
    await repo.delete_definition(definition_id)
    await audit_service.log_action(
        action="ticket_custom_field.delete", user_id=current_user.get("id"),
        entity_type="ticket_custom_field", entity_id=definition_id, previous_value=previous, request=request,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _ticket_values(ticket_id: int, current_user: dict) -> list[TicketCustomFieldValue]:
    fields = await service.build_ticket_fields(ticket_id, **_viewer(current_user))
    return [TicketCustomFieldValue(**field) for field in fields]


@router.get(
    "/api/tickets/{ticket_id}/custom-fields",
    response_model=list[TicketCustomFieldValue],
)
async def get_ticket_custom_field_values(
    ticket_id: int, current_user: dict = Depends(require_helpdesk_technician)
):
    """Custom details on a ticket that the caller may see."""
    if not await tickets_repo.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    return await _ticket_values(ticket_id, current_user)


@router.put(
    "/api/tickets/{ticket_id}/custom-fields",
    response_model=list[TicketCustomFieldValue],
)
async def update_ticket_custom_field_values(
    ticket_id: int,
    body: TicketCustomFieldValuesUpdate,
    request: Request,
    current_user: dict = Depends(require_helpdesk_technician),
):
    """Change editable custom details on a ticket. Read-only details are refused."""
    if not await tickets_repo.get_ticket(ticket_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    changes, errors = await service.save_ticket_values(
        ticket_id, body.values, user_id=current_user.get("id"), **_viewer(current_user)
    )
    if errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=" ".join(errors))
    if changes:
        await audit_service.log_action(
            action="ticket.custom_fields.update", user_id=current_user.get("id"),
            entity_type="ticket", entity_id=ticket_id,
            previous_value={change["field_key"]: change["previous"] for change in changes},
            new_value={change["field_key"]: change["value"] for change in changes},
            request=request,
        )
    return await _ticket_values(ticket_id, current_user)
