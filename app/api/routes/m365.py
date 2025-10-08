from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.schemas.m365 import M365CredentialCreate, M365CredentialResponse
from app.services import m365 as m365_service


router = APIRouter(prefix="/companies/{company_id}/m365-credentials", tags=["Office365"])


async def _ensure_company(company_id: int) -> None:
    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")


@router.get("", response_model=M365CredentialResponse | None)
async def get_credentials(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    creds = await m365_service.get_credentials(company_id)
    if not creds:
        return None
    return M365CredentialResponse.model_validate(
        {
            "tenantId": creds.get("tenant_id"),
            "clientId": creds.get("client_id"),
            "tokenExpiresAt": creds.get("token_expires_at"),
        }
    )


@router.post("", response_model=M365CredentialResponse, status_code=status.HTTP_200_OK)
async def upsert_credentials(
    company_id: int,
    payload: M365CredentialCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    await m365_service.upsert_credentials(
        company_id=company_id,
        tenant_id=payload.tenant_id,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
    )
    creds = await m365_service.get_credentials(company_id)
    return M365CredentialResponse.model_validate(
        {
            "tenantId": creds.get("tenant_id") if creds else payload.tenant_id,
            "clientId": creds.get("client_id") if creds else payload.client_id,
            "tokenExpiresAt": creds.get("token_expires_at") if creds else None,
        }
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credentials(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    await m365_service.delete_credentials(company_id)
    return None

