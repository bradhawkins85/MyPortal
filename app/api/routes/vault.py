from __future__ import annotations

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import user_companies as user_company_repo
from app.repositories import vault as repo
from app.schemas.vault import (
    CredentialCreate,
    CredentialMetadata,
    CredentialUpdate,
    SecretGenerate,
    SecretReplace,
    SecretReveal,
)
from app.services import audit
from app.security.rate_limiter import SimpleRateLimiter

router = APIRouter(prefix="/api/vault", tags=["Credential vault"])
_reveal_limiter = SimpleRateLimiter(10, 60, namespace="vault-reveal")
_write_limiter = SimpleRateLimiter(30, 60, namespace="vault-write")


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"


async def _limit(request: Request, user: dict, *, reveal: bool = False) -> None:
    limiter = _reveal_limiter if reveal else _write_limiter
    allowed, retry_after = await limiter.check(
        f"{user.get('id', 'anonymous')}:{request.url.path}"
    )
    if not allowed:
        raise HTTPException(
            429,
            "Credential action rate limit exceeded",
            headers={"Retry-After": str(int(retry_after or 60))},
        )


def _deny_impersonation(request: Request) -> None:
    if getattr(request.state, "impersonator_user_id", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="Credential secrets are unavailable while impersonating",
        )


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
    response: Response,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """List searchable metadata. Secret material and ciphertext are never selected."""
    await _authorize(user, company_id, write=True)
    _no_store(response)
    return await repo.list_credentials(company_id)


@router.post("/credentials", response_model=CredentialMetadata, status_code=201)
async def create_credential(
    payload: CredentialCreate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _authorize(user, payload.company_id, write=True)
    _deny_impersonation(request)
    await _limit(request, user)
    try:
        row = await repo.create_credential(
            company_id=payload.company_id,
            name=payload.name.strip(),
            username=payload.username,
            credential_class=payload.credential_class,
            owner=payload.owner,
            intended_recipient=payload.intended_recipient,
            expires_on=payload.expires_on,
            review_on=payload.review_on,
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
    await _authorize(user, company_id, write=True)
    _deny_impersonation(request)
    await _limit(request, user, reveal=True)
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
    _deny_impersonation(request)
    await _limit(request, user)
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


@router.post(
    "/companies/{company_id}/credentials/{credential_id}/generate",
    response_model=SecretReveal,
)
async def generate_secret(
    company_id: int,
    credential_id: int,
    payload: SecretGenerate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Generate and rotate a secret. The generated value is returned exactly once."""
    await _authorize(user, company_id, write=True)
    _deny_impersonation(request)
    await _limit(request, user)
    alphabet = string.ascii_letters + string.digits + "-_.!@#$%"
    plaintext = "".join(secrets.choice(alphabet) for _ in range(payload.length))
    row = await repo.add_version(
        company_id=company_id,
        credential_id=credential_id,
        plaintext=plaintext,
        created_by=user.get("id"),
    )
    if row is None:
        raise HTTPException(404, "Credential not found")
    await audit.record(
        action="vault.secret.generate",
        request=request,
        user_id=user.get("id"),
        entity_type="credential",
        entity_id=credential_id,
        after={"company_id": company_id, "version": row["current_version"]},
    )
    result = SecretReveal(
        credential_id=credential_id, version=row["current_version"], secret=plaintext
    )
    return JSONResponse(
        result.model_dump(),
        headers={
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.put(
    "/companies/{company_id}/credentials/{credential_id}",
    response_model=CredentialMetadata,
)
async def update_credential(
    company_id: int,
    credential_id: int,
    payload: CredentialUpdate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Edit metadata only. This model deliberately has no secret field."""
    await _authorize(user, company_id, write=True)
    _deny_impersonation(request)
    await _limit(request, user)
    row = await repo.update_metadata(company_id, credential_id, payload.model_dump())
    if row is None:
        raise HTTPException(404, "Credential not found")
    await audit.record(
        action="vault.credential.update",
        request=request,
        user_id=user.get("id"),
        entity_type="credential",
        entity_id=credential_id,
        after={"company_id": company_id},
    )
    return row


@router.post(
    "/companies/{company_id}/credentials/{credential_id}/{action}",
    response_model=CredentialMetadata,
)
async def credential_lifecycle(
    company_id: int,
    credential_id: int,
    action: str,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    if action not in {"archive", "revoke"}:
        raise HTTPException(404, "Unknown credential action")
    await _authorize(user, company_id, write=True)
    _deny_impersonation(request)
    await _limit(request, user)
    row = await repo.set_lifecycle(company_id, credential_id, action)
    if row is None:
        raise HTTPException(404, "Credential not found")
    await audit.record(
        action=f"vault.credential.{action}",
        request=request,
        user_id=user.get("id"),
        entity_type="credential",
        entity_id=credential_id,
        after={"company_id": company_id},
    )
    return row
