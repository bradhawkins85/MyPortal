from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.core.errors import build_client_http_error, log_exception_with_error_id, new_error_id
from app.schemas.imap import (
    IMAPAccountCreate,
    IMAPAccountResponse,
    IMAPAccountUpdate,
    IMAPSyncResponse,
)
from app.services import imap as imap_service

router = APIRouter(prefix="/imap", tags=["IMAP"])


@router.get("/accounts", response_model=list[IMAPAccountResponse])
async def list_accounts(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> list[IMAPAccountResponse]:
    accounts = await imap_service.list_accounts()
    return [IMAPAccountResponse.model_validate(account) for account in accounts]


@router.post("/accounts", response_model=IMAPAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: IMAPAccountCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    data = payload.model_dump()
    data["password"] = payload.password.get_secret_value()
    try:
        account = await imap_service.create_account(data)
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id("Failed to create IMAP account", error_id=error_id, route="imap.create_account")
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to create IMAP account. Please verify the account details and try again.",
            error_id=error_id,
        ) from exc
    return IMAPAccountResponse.model_validate(account)


@router.get("/accounts/{account_id}", response_model=IMAPAccountResponse)
async def get_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    account = await imap_service.get_account(account_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return IMAPAccountResponse.model_validate(account)


@router.put("/accounts/{account_id}", response_model=IMAPAccountResponse)
async def update_account(
    account_id: int,
    payload: IMAPAccountUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"] is not None:
        if payload.password is None:
            data.pop("password", None)
        else:
            data["password"] = payload.password.get_secret_value()
    try:
        account = await imap_service.update_account(account_id, data)
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to update IMAP account",
            error_id=error_id,
            route="imap.update_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to update IMAP account. Please verify the account details and try again.",
            error_id=error_id,
        ) from exc
    return IMAPAccountResponse.model_validate(account)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> None:
    await imap_service.delete_account(account_id)
    return None


@router.post("/accounts/{account_id}/sync", response_model=IMAPSyncResponse)
async def sync_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPSyncResponse:
    result = await imap_service.sync_account(account_id)
    return IMAPSyncResponse.model_validate(result)


@router.post(
    "/accounts/{account_id}/clone",
    response_model=IMAPAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
async def clone_account(
    account_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
) -> IMAPAccountResponse:
    try:
        account = await imap_service.clone_account(account_id)
    except LookupError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to clone IMAP account",
            error_id=error_id,
            route="imap.clone_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_404_NOT_FOUND,
            "IMAP account not found.",
            error_id=error_id,
        ) from exc
    except ValueError as exc:
        error_id = new_error_id()
        log_exception_with_error_id(
            "Failed to clone IMAP account",
            error_id=error_id,
            route="imap.clone_account",
            account_id=account_id,
        )
        raise build_client_http_error(
            status.HTTP_400_BAD_REQUEST,
            "Unable to clone IMAP account.",
            error_id=error_id,
        ) from exc
    return IMAPAccountResponse.model_validate(account)
