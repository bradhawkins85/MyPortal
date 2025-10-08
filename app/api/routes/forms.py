from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import forms as forms_repo
from app.repositories import user_companies as user_company_repo
from app.schemas.forms import (
    FormCreate,
    FormPermissionUpdate,
    FormResponse,
    FormUpdate,
)
from app.services.opnform import (
    OpnformValidationError,
    extract_allowed_host,
    normalize_opnform_embed_code,
    normalize_opnform_form_url,
)
from app.core.config import get_settings


router = APIRouter(prefix="/forms", tags=["Forms"])

settings = get_settings()
OPNFORM_ALLOWED_HOST = extract_allowed_host(str(settings.opnform_base_url) if settings.opnform_base_url else None)


async def _ensure_admin_for_company(current_user: dict, company_id: int) -> None:
    if current_user.get("is_super_admin"):
        return
    membership = await user_company_repo.get_user_company(current_user["id"], company_id)
    if not membership or not membership.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company admin privileges required",
        )


def _normalise_form_payload(payload: FormCreate | FormUpdate) -> tuple[str, str | None, str | None]:
    allowed_host = OPNFORM_ALLOWED_HOST
    embed_code = payload.embed_code.strip() if payload.embed_code else None
    url = payload.url.strip() if payload.url else None
    description = payload.description.strip() if payload.description else None
    if url is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Form URL is required")
    try:
        normalised_url = normalize_opnform_form_url(url, allowed_host=allowed_host)
        sanitized_embed = None
        if embed_code:
            normalized = normalize_opnform_embed_code(embed_code, allowed_host=allowed_host)
            sanitized_embed = normalized.sanitized_embed_code
            normalised_url = normalized.form_url
        return normalised_url, sanitized_embed, description
    except OpnformValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=list[FormResponse])
async def list_forms_for_current_user(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    forms = await forms_repo.list_forms_for_user(current_user["id"])
    return forms


@router.get("/companies/{company_id}", response_model=list[FormResponse])
async def list_forms_for_company(
    company_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_admin_for_company(current_user, company_id)
    forms = await forms_repo.list_forms_for_company(company_id)
    return forms


@router.get("/all", response_model=list[FormResponse])
async def list_all_forms(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    return await forms_repo.list_forms()


@router.post("", response_model=FormResponse, status_code=status.HTTP_201_CREATED)
async def create_form(
    payload: FormCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    sanitized_name = payload.name.strip()
    if not sanitized_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Form name cannot be empty")
    normalised_url, sanitized_embed, description = _normalise_form_payload(payload)
    created = await forms_repo.create_form(
        name=sanitized_name,
        url=normalised_url,
        embed_code=sanitized_embed,
        description=description,
    )
    return created


@router.patch("/{form_id}", response_model=FormResponse)
async def update_form(
    form_id: int,
    payload: FormUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await forms_repo.get_form(form_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Form name cannot be empty")
    else:
        name = existing["name"]
    description = (
        payload.description.strip() if payload.description is not None else existing.get("description")
    )
    description = description or None

    url = payload.url.strip() if payload.url is not None else existing["url"]
    embed_code = payload.embed_code.strip() if payload.embed_code is not None else existing.get("embed_code")
    try:
        normalised_url = normalize_opnform_form_url(url, allowed_host=OPNFORM_ALLOWED_HOST)
        sanitized_embed = None
        if embed_code:
            normalized = normalize_opnform_embed_code(embed_code, allowed_host=OPNFORM_ALLOWED_HOST)
            sanitized_embed = normalized.sanitized_embed_code
            normalised_url = normalized.form_url
    except OpnformValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    updated = await forms_repo.update_form(
        form_id,
        name=name,
        url=normalised_url,
        embed_code=sanitized_embed,
        description=description,
    )
    return updated


@router.delete("/{form_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_form(
    form_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await forms_repo.get_form(form_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await forms_repo.delete_form(form_id)
    return None


@router.get("/{form_id}/companies/{company_id}/permissions", response_model=list[int])
async def get_company_form_permissions(
    form_id: int,
    company_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_admin_for_company(current_user, company_id)
    form = await forms_repo.get_form(form_id)
    if not form:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    permissions = await forms_repo.list_form_permissions(form_id, company_id)
    return permissions


@router.put("/{form_id}/companies/{company_id}/permissions", response_model=list[int])
async def update_company_form_permissions(
    form_id: int,
    company_id: int,
    payload: FormPermissionUpdate,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_admin_for_company(current_user, company_id)
    form = await forms_repo.get_form(form_id)
    if not form:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await forms_repo.update_form_permissions(form_id, company_id, payload.user_ids)
    return await forms_repo.list_form_permissions(form_id, company_id)


@router.delete(
    "/{form_id}/companies/{company_id}/permissions/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_company_form_permission(
    form_id: int,
    company_id: int,
    user_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _ensure_admin_for_company(current_user, company_id)
    form = await forms_repo.get_form(form_id)
    if not form:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form not found")
    await forms_repo.delete_form_permission(form_id, user_id, company_id)
    return None
