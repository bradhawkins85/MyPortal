from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import user_companies as user_company_repo
from app.repositories import vault as repo
from app.repositories import credential_grants as grant_repo
from app.schemas.vault import (
    CredentialCreate,
    CredentialMetadata,
    CredentialUpdate,
    SecretGenerate,
    SecretReplace,
    SecretReveal,
    CredentialGrantCreate,
    CredentialGrant,
    ExternalGrantCreated,
    ShareVerification,
    ShareToken,
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


def _grant_audit_metadata(row: dict) -> dict:
    return {
        "company_id": row["company_id"],
        "credential_id": row["credential_id"],
        "credential_version": row["credential_version"],
        "staff_id": row["staff_id"],
        "grantor_user_id": row["grantor_user_id"],
        "recipient_user_id": row.get("recipient_user_id"),
        "recipient_email": row.get("recipient_email"),
    }


def _validate_grant_payload(payload: CredentialGrantCreate) -> None:
    if (payload.recipient_user_id is None) == (payload.recipient_email is None):
        raise HTTPException(
            422, "Choose exactly one named portal recipient or external recipient"
        )
    expires = payload.expires_at
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise HTTPException(422, "expires_at must include a timezone")
    if expires.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(422, "expires_at must be in the future")


@router.post(
    "/companies/{company_id}/credentials/{credential_id}/grants",
    response_model=CredentialGrant | ExternalGrantCreated,
    status_code=201,
)
async def create_credential_grant(
    company_id: int,
    credential_id: int,
    payload: CredentialGrantCreate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Grant one pinned onboarding-password version; no notification contains the password."""
    await _authorize(user, company_id, write=True)
    _deny_impersonation(request)
    await _limit(request, user)
    _validate_grant_payload(payload)
    external = payload.recipient_email is not None
    token = secrets.token_urlsafe(32) if external else None
    code = f"{secrets.randbelow(1_000_000):06d}" if external else None
    row = await grant_repo.create(
        credential_id=credential_id,
        company_id=company_id,
        staff_id=payload.staff_id,
        grantor_user_id=int(user["id"]),
        recipient_user_id=payload.recipient_user_id,
        recipient_email=(
            payload.recipient_email.lower().strip() if payload.recipient_email else None
        ),
        reason=payload.reason.strip(),
        expires_at=payload.expires_at.astimezone(timezone.utc).replace(tzinfo=None),
        token=token,
        verification_code=code,
    )
    if row is None:
        raise HTTPException(404, "Credential or recipient not found")
    metadata = _grant_audit_metadata(row)
    await audit.record(
        action="vault.grant.create",
        request=request,
        user_id=user.get("id"),
        entity_type="credential_grant",
        entity_id=row["id"],
        after={"reason": row["reason"], "expires_at": row["expires_at"]},
        metadata=metadata,
    )
    await audit.record(
        action="vault.grant.notify",
        request=request,
        user_id=user.get("id"),
        entity_type="credential_grant",
        entity_id=row["id"],
        metadata=metadata,
    )
    return (
        ExternalGrantCreated(**row, share_token=token, verification_code=code)
        if external
        else CredentialGrant(**row)
    )


@router.post(
    "/companies/{company_id}/grants/{grant_id}/reveal", response_model=SecretReveal
)
async def reveal_named_grant(
    company_id: int,
    grant_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Reveal only a grant addressed to the authenticated user, never generic company managers."""
    _deny_impersonation(request)
    await _limit(request, user, reveal=True)
    result = await grant_repo.reveal_named(grant_id, int(user["id"]), company_id)
    if result is None:
        expired = await grant_repo.claim_expiry(
            grant_id=grant_id, user_id=int(user["id"])
        )
        if expired:
            await audit.record(
                action="vault.grant.expire",
                request=request,
                user_id=user.get("id"),
                entity_type="credential_grant",
                entity_id=grant_id,
                metadata=_grant_audit_metadata(expired),
            )
        await audit.record(
            action="vault.grant.fail",
            request=request,
            user_id=user.get("id"),
            entity_type="credential_grant",
            entity_id=grant_id,
            metadata={"company_id": company_id},
        )
        raise HTTPException(404, "Share unavailable")
    row, secret = result
    metadata = _grant_audit_metadata(row)
    await audit.record(
        action="vault.grant.open",
        request=request,
        user_id=user.get("id"),
        entity_type="credential_grant",
        entity_id=grant_id,
        metadata=metadata,
    )
    await audit.record(
        action="vault.grant.reveal",
        request=request,
        user_id=user.get("id"),
        entity_type="credential_grant",
        entity_id=grant_id,
        metadata=metadata,
    )
    return JSONResponse(
        {
            "credential_id": row["credential_id"],
            "version": row["credential_version"],
            "secret": secret,
        },
        headers={
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@router.post(
    "/companies/{company_id}/grants/{grant_id}/revoke", response_model=CredentialGrant
)
async def revoke_credential_grant(
    company_id: int,
    grant_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    _deny_impersonation(request)
    row = await grant_repo.revoke(grant_id, company_id, int(user["id"]))
    if row is None:
        raise HTTPException(404, "Share unavailable")
    await audit.record(
        action="vault.grant.revoke",
        request=request,
        user_id=user.get("id"),
        entity_type="credential_grant",
        entity_id=grant_id,
        metadata=_grant_audit_metadata(row),
    )
    return row


@router.post("/shares/verify", status_code=204)
async def verify_external_share(
    payload: ShareVerification, request: Request, _: None = Depends(require_database)
):
    grant_id = await grant_repo.verify_external(
        payload.share_token, payload.verification_code
    )
    if grant_id is None:
        expired = await grant_repo.claim_expiry(token=payload.share_token)
        if expired:
            await audit.record(
                action="vault.share.expire",
                request=request,
                entity_type="credential_grant",
                entity_id=expired["id"],
                metadata=_grant_audit_metadata(expired),
                source="ui",
                actor="external_recipient",
            )
        await audit.record(
            action="vault.share.fail",
            request=request,
            entity_type="credential_grant",
            source="ui",
            actor="external_recipient",
        )
        raise HTTPException(404, "Share unavailable")
    await audit.record(
        action="vault.share.open",
        request=request,
        entity_type="credential_grant",
        entity_id=grant_id,
        source="ui",
        actor="external_recipient",
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/shares/reveal", response_model=SecretReveal)
async def reveal_external_share(
    payload: ShareToken, request: Request, _: None = Depends(require_database)
):
    result = await grant_repo.consume_external(payload.share_token)
    if result is None:
        expired = await grant_repo.claim_expiry(token=payload.share_token)
        if expired:
            await audit.record(
                action="vault.share.expire",
                request=request,
                entity_type="credential_grant",
                entity_id=expired["id"],
                metadata=_grant_audit_metadata(expired),
                source="ui",
                actor="external_recipient",
            )
        await audit.record(
            action="vault.share.fail",
            request=request,
            entity_type="credential_grant",
            source="ui",
            actor="external_recipient",
        )
        raise HTTPException(404, "Share unavailable")
    row, secret = result
    metadata = _grant_audit_metadata(row)
    await audit.record(
        action="vault.share.reveal",
        request=request,
        entity_type="credential_grant",
        entity_id=row["id"],
        metadata=metadata,
        source="ui",
        actor="external_recipient",
    )
    await audit.record(
        action="vault.share.consume",
        request=request,
        entity_type="credential_grant",
        entity_id=row["id"],
        metadata=metadata,
        source="ui",
        actor="external_recipient",
    )
    return JSONResponse(
        {
            "credential_id": row["credential_id"],
            "version": row["credential_version"],
            "secret": secret,
        },
        headers={
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )
