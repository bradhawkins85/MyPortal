from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import user_companies as user_company_repo
from app.repositories import vault as repo
from app.schemas.vault import (
    CredentialCreate,
    CredentialMetadata,
    SecretReplace,
    SecretReveal,
)
from app.services import audit

router = APIRouter(prefix="/api/vault", tags=["Credential vault"])


async def _authorize(user: dict, company_id: int, *, write: bool = False) -> None:
    if user.get("is_super_admin"):
        return
    try:
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Vault access denied"
        ) from None
    membership = await user_company_repo.get_user_company(user_id, company_id)
    if not membership or (write and not membership.get("is_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Vault access denied"
        )


@router.get(
    "/companies/{company_id}/credentials", response_model=list[CredentialMetadata]
)
async def list_credentials(
    company_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List searchable metadata. Secret material and ciphertext are never selected."""
    await _authorize(user, company_id)
    return await repo.list_credentials(company_id)


@router.post("/credentials", response_model=CredentialMetadata, status_code=201)
async def create_credential(
    payload: CredentialCreate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _authorize(user, payload.company_id, write=True)
    try:
        row = await repo.create_credential(
            company_id=payload.company_id,
            name=payload.name.strip(),
            username=payload.username,
            plaintext=payload.secret.get_secret_value(),
            created_by=user.get("id"),
            links=payload.links,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Invalid credential link target"
        ) from exc
    await audit.record(
        action="vault.credential.create",
        request=request,
        user_id=user.get("id"),
        entity_type="credential",
        entity_id=row["id"],
        after={"company_id": payload.company_id, "name": row["name"], "version": 1},
    )
    return row


@router.post(
    "/companies/{company_id}/credentials/{credential_id}/reveal",
    response_model=SecretReveal,
)
async def reveal_credential(
    company_id: int,
    credential_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Deliberate, auditable reveal. Responses prohibit caching and indexing."""
    await _authorize(user, company_id)
    revealed = await repo.reveal(company_id, credential_id)
    if revealed is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    version, plaintext = revealed
    await audit.record(
        action="vault.secret.reveal",
        request=request,
        user_id=user.get("id"),
        entity_type="credential",
        entity_id=credential_id,
        after={"company_id": company_id, "version": version},
    )
    response = SecretReveal(
        credential_id=credential_id, version=version, secret=plaintext
    )
    from fastapi.responses import JSONResponse

    return JSONResponse(
        response.model_dump(),
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.post(
    "/companies/{company_id}/credentials/{credential_id}/versions",
    response_model=CredentialMetadata,
)
async def replace_secret(
    company_id: int,
    credential_id: int,
    payload: SecretReplace,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _authorize(user, company_id, write=True)
    row = await repo.add_version(
        company_id=company_id,
        credential_id=credential_id,
        plaintext=payload.secret.get_secret_value(),
        created_by=user.get("id"),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    await audit.record(
        action="vault.secret.rotate",
        request=request,
        user_id=user.get("id"),
        entity_type="credential",
        entity_id=credential_id,
        after={"company_id": company_id, "version": row["current_version"]},
    )
    return row
