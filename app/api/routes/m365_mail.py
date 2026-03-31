from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.core.errors import build_client_http_error, log_exception_with_error_id, new_error_id
from app.schemas.m365_mail import (
    M365MailAccountCreate,
    M365MailAccountResponse,
    M365MailAccountUpdate,
    M365MailSyncResponse,
)
from app.services import m365_mail as m365_mail_service

router = APIRouter(prefix="/m365-mail", tags=["Office365 Mail"])


@router.get("/accounts", response_model=list[M365MailAccountResponse])
async def list_accounts(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> list[M365MailAccountResponse]:
    accounts = await m365_mail_service.list_accounts()
    return [M365MailAccountResponse.model_validate(account) for account in accounts]


@router.post("/accounts", response_model=M365MailAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: M365MailAccountCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> M365MailAccountResponse:
    data = payload.model_dump()
    try:
        account = await m365_mail_service.create_account(data)
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to create M365 mail account",
            error_id=error_id,
            route="m365_mail.create_account",
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to create Office 365 mail account. Please verify the account details and try again.",
            error_id=error_id,
        ) from exc
    return M365MailAccountResponse.model_validate(account)


@router.get("/accounts/{account_id}", response_model=M365MailAccountResponse)
async def get_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> M365MailAccountResponse:
    account = await m365_mail_service.get_account(account_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return M365MailAccountResponse.model_validate(account)


@router.put("/accounts/{account_id}", response_model=M365MailAccountResponse)
async def update_account(
    account_id: int,
    payload: M365MailAccountUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> M365MailAccountResponse:
    data = payload.model_dump(exclude_unset=True)
    try:
        account = await m365_mail_service.update_account(account_id, data)
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to update M365 mail account",
            error_id=error_id,
            route="m365_mail.update_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to update Office 365 mail account. Please verify the account details and try again.",
            error_id=error_id,
        ) from exc
    return M365MailAccountResponse.model_validate(account)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> None:
    await m365_mail_service.delete_account(account_id)
    return None


@router.post("/accounts/{account_id}/sync", response_model=M365MailSyncResponse)
async def sync_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> M365MailSyncResponse:
    result = await m365_mail_service.sync_account(account_id)
    return M365MailSyncResponse.model_validate(result)


@router.post(
    "/accounts/{account_id}/clone",
    response_model=M365MailAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> M365MailAccountResponse:
    try:
        account = await m365_mail_service.clone_account(account_id)
    except LookupError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to clone M365 mail account",
            error_id=error_id,
            route="m365_mail.clone_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_404_NOT_FOUND,
            "Office 365 mail account not found.",
            error_id=error_id,
        ) from exc
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to clone M365 mail account",
            error_id=error_id,
            route="m365_mail.clone_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to clone Office 365 mail account.",
            error_id=error_id,
        ) from exc
    return M365MailAccountResponse.model_validate(account)


@router.post("/accounts/{account_id}/disconnect", response_model=M365MailAccountResponse)
async def disconnect_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> M365MailAccountResponse:
    """Remove the per-account delegated OAuth tokens (revert to company credentials)."""
    account = await m365_mail_service.get_account(account_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    result = await m365_mail_service.clear_delegated_tokens(account_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return M365MailAccountResponse.model_validate(m365_mail_service.enrich_account_response(result))
