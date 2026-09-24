from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.repositories import companies as company_repo
from app.schemas.m365 import M365CredentialCreate, M365CredentialResponse
from app.services import m365 as m365_service


router = APIRouter(prefix="/companies/{company_id}/m365-credentials", tags=["Office365"])


def _public_connection(connection: dict) -> dict:
    """Never serialize encrypted credentials or token material to an API client."""
    blocked = {"client_secret", "refresh_token", "access_token"}
    return {key: value for key, value in connection.items() if key not in blocked}


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


@router.post("/connections/{connection_id}/verify", response_model=dict)
async def verify_connection_candidate(
    company_id: int,
    connection_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Run all non-destructive pre-cutover checks for a staged candidate."""
    await _ensure_company(company_id)
    connection = await m365_service.verify_connection_candidate(company_id, connection_id)
    return _public_connection(connection)


@router.post("/connections/{connection_id}/activate", response_model=dict)
async def activate_connection_candidate(
    company_id: int,
    connection_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Explicitly cut over after all candidate checks have passed."""
    await _ensure_company(company_id)
    try:
        connection = await m365_service.activate_connection_candidate(company_id, connection_id)
        return _public_connection(connection)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/connections/rollback", response_model=dict)
async def rollback_connection(
    company_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Restore the previous connection and its unchanged encrypted secret."""
    await _ensure_company(company_id)
    try:
        connection = await m365_service.rollback_connection(company_id)
        return _public_connection(connection)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/dependencies", response_model=list[dict])
async def connection_dependencies(
    company_id: int,
    connection_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    await _ensure_company(company_id)
    return await m365_service.connection_dependency_inventory(connection_id)


@router.post("/connections/{connection_id}/retire", status_code=status.HTTP_204_NO_CONTENT)
async def retire_connection(
    company_id: int,
    connection_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    """Explicit retirement only; this never deletes the Entra registration."""
    await _ensure_company(company_id)
    try:
        await m365_service.retire_connection(company_id, connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return None
