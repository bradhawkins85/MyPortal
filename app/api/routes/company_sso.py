from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.services import company_management, sso_admin, sso_policy
from app.services.sso_admin import SsoAdminError

router = APIRouter(tags=["Company SSO"])

GUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class SsoModeUpdate(BaseModel):
    mode: Literal["off", "optional", "required"] = Field(
        description="Microsoft sign-in mode: `off`, `optional` or `required`."
    )


class SsoIdentityItem(BaseModel):
    user_id: int = Field(description="MyPortal user the Microsoft account is linked to.")
    email: str | None = Field(default=None, description="The MyPortal user's email address.")
    upn: str | None = Field(default=None, description="Microsoft user principal name.")
    linked_via: str = Field(description="How the link was made: `auto` or `admin`.")
    last_login_at: datetime | None = Field(
        default=None, description="Last Microsoft sign-in (UTC), if any."
    )


class CompanySsoResponse(BaseModel):
    mode: str = Field(description="Current sign-in mode: `off`, `optional` or `required`.")
    tenant_connected: bool = Field(
        description="Whether the company has a Microsoft 365 tenant connected."
    )
    identities: list[SsoIdentityItem] = Field(
        description="Linked Microsoft accounts for the company's users."
    )


class DirectoryUser(BaseModel):
    object_id: str = Field(description="Microsoft Entra object ID (GUID).")
    display_name: str | None = Field(default=None, description="Display name.")
    upn: str | None = Field(default=None, description="User principal name.")
    mail: str | None = Field(default=None, description="Primary mailbox address.")


class SsoLinkRequest(BaseModel):
    company_id: int = Field(
        description="Company whose Microsoft 365 tenant the account belongs to."
    )
    object_id: str = Field(
        pattern=GUID_PATTERN,
        min_length=36,
        max_length=36,
        description="Microsoft Entra object ID (GUID) of the account to link.",
    )


class SsoLinkResponse(BaseModel):
    user_id: int = Field(description="MyPortal user that was linked.")
    upn: str | None = Field(default=None, description="Microsoft user principal name.")
    email: str | None = Field(default=None, description="Microsoft mailbox address.")
    linked_via: str = Field(description="How the link was made; `admin` for this endpoint.")


async def _managed_company_ids(request: Request, user: dict[str, Any]) -> set[int]:
    _, companies, _ = await company_management.management_scope(user)
    return {int(company["id"]) for company in companies}


async def _require_company(request: Request, user: dict[str, Any], company_id: int) -> None:
    if company_id not in await _managed_company_ids(request, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


_FORBIDDEN = {
    status.HTTP_403_FORBIDDEN: {
        "description": "You do not manage this company or this user's Microsoft sign-in."
    }
}
_NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"description": "The MyPortal user does not exist."}}
_GRAPH_UNREACHABLE = {
    status.HTTP_502_BAD_GATEWAY: {"description": "Microsoft 365 could not be reached."}
}


def _error_response(status_code: int, message: str) -> JSONResponse:
    # Returned directly (not raised) so the global handler keeps 5xx copy.
    return JSONResponse(status_code=status_code, content={"detail": message})


@router.get(
    "/api/companies/{company_id}/sso",
    response_model=CompanySsoResponse,
    summary="Get a company's Microsoft sign-in settings",
    responses={**_FORBIDDEN},
)
async def get_company_sso(
    company_id: int,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _require_company(request, current_user, company_id)
    return await sso_admin.company_sso_summary(company_id)


@router.put(
    "/api/companies/{company_id}/sso",
    response_model=CompanySsoResponse,
    summary="Set a company's Microsoft sign-in mode",
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": (
                "`required` was chosen before the company's Microsoft 365 tenant is "
                "connected or before any user is linked."
            )
        },
        **_FORBIDDEN,
    },
)
async def set_company_sso(
    company_id: int,
    payload: SsoModeUpdate,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _require_company(request, current_user, company_id)
    try:
        await sso_policy.set_company_sso_mode(
            company_id, payload.mode, actor=current_user, request=request
        )
    except sso_policy.SsoModeError as exc:
        return _error_response(
            status.HTTP_400_BAD_REQUEST, sso_policy.mode_error_message(exc.code)
        )
    return await sso_admin.company_sso_summary(company_id)


@router.get(
    "/api/companies/{company_id}/sso/directory",
    response_model=list[DirectoryUser],
    summary="Search the company's Microsoft 365 directory",
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "The search is too short or the company has no Microsoft 365 tenant."
        },
        **_FORBIDDEN,
        **_GRAPH_UNREACHABLE,
    },
)
async def search_company_directory(
    company_id: int,
    request: Request,
    q: Annotated[
        str,
        Query(min_length=2, max_length=100, description="Name or user principal name to search for."),
    ],
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    await _require_company(request, current_user, company_id)
    try:
        return await sso_admin.search_directory(company_id, q)
    except SsoAdminError as exc:
        return _error_response(exc.status_code, exc.message)


@router.put(
    "/api/users/{user_id}/sso-identity",
    response_model=SsoLinkResponse,
    summary="Link a user to a Microsoft account",
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": (
                "The object ID is not a valid GUID or the company has no Microsoft 365 tenant."
            )
        },
        **_FORBIDDEN,
        status.HTTP_404_NOT_FOUND: {
            "description": "The MyPortal user or the Microsoft account does not exist."
        },
        status.HTTP_409_CONFLICT: {
            "description": "The Microsoft account is already linked to another user."
        },
        **_GRAPH_UNREACHABLE,
    },
)
async def link_user_sso_identity(
    user_id: int,
    payload: SsoLinkRequest,
    request: Request,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    managed = await _managed_company_ids(request, current_user)
    try:
        identity = await sso_admin.link_identity(
            actor=current_user,
            target_user_id=user_id,
            company_id=payload.company_id,
            object_id=payload.object_id,
            managed_company_ids=managed,
            request=request,
        )
    except SsoAdminError as exc:
        return _error_response(exc.status_code, exc.message)
    return identity


@router.delete(
    "/api/users/{user_id}/sso-identity",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink a user's Microsoft account",
    responses={**_FORBIDDEN, **_NOT_FOUND},
)
async def unlink_user_sso_identity(
    user_id: int,
    request: Request,
    company_id: Annotated[
        int, Query(description="Company through which the actor manages this user.")
    ],
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    managed = await _managed_company_ids(request, current_user)
    try:
        await sso_admin.unlink_identity(
            actor=current_user,
            target_user_id=user_id,
            company_id=company_id,
            managed_company_ids=managed,
            request=request,
        )
    except SsoAdminError as exc:
        return _error_response(exc.status_code, exc.message)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
