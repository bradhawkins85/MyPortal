from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import require_super_admin
from app.schemas.message_templates import (
    MessageTemplateCreate,
    MessageTemplateResponse,
    MessageTemplateUpdate,
)
from app.services import message_templates as message_templates_service

router = APIRouter(prefix="/api/message-templates", tags=["Message Templates"])


@router.get("/", response_model=list[MessageTemplateResponse])
async def list_message_templates(
    search: str | None = Query(default=None),
    content_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_super_admin),
) -> list[MessageTemplateResponse]:
    records = await message_templates_service.list_templates(
        search=search,
        content_type=content_type,
        limit=limit,
        offset=offset,
    )
    return [MessageTemplateResponse(**record) for record in records]


@router.post("/", response_model=MessageTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_message_template(
    payload: MessageTemplateCreate,
    current_user: dict = Depends(require_super_admin),
) -> MessageTemplateResponse:
    try:
        record = await message_templates_service.create_template(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MessageTemplateResponse(**record)


@router.get("/{template_id}", response_model=MessageTemplateResponse)
async def get_message_template(
    template_id: int,
    current_user: dict = Depends(require_super_admin),
) -> MessageTemplateResponse:
    record = await message_templates_service.get_template(template_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return MessageTemplateResponse(**record)


@router.get("/slug/{slug}", response_model=MessageTemplateResponse)
async def get_message_template_by_slug(
    slug: str,
    current_user: dict = Depends(require_super_admin),
) -> MessageTemplateResponse:
    record = await message_templates_service.get_template_by_slug(slug)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return MessageTemplateResponse(**record)


@router.put("/{template_id}", response_model=MessageTemplateResponse)
async def update_message_template(
    template_id: int,
    payload: MessageTemplateUpdate,
    current_user: dict = Depends(require_super_admin),
) -> MessageTemplateResponse:
    existing = await message_templates_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    try:
        record = await message_templates_service.update_template(template_id, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return MessageTemplateResponse(**record)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message_template(
    template_id: int,
    current_user: dict = Depends(require_super_admin),
) -> None:
    existing = await message_templates_service.get_template(template_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    await message_templates_service.delete_template(template_id)
