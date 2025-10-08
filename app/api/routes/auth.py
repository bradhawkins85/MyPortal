from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.dependencies.auth import get_current_session, get_current_user
from app.api.dependencies.database import require_database
from app.core.config import get_settings
from app.repositories import auth as auth_repo
from app.repositories import users as user_repo
from app.repositories import user_companies as user_company_repo
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetStatus,
    RegistrationRequest,
    SessionInfo,
    SessionResponse,
    TOTPAuthenticator,
    TOTPListResponse,
    TOTPSetupResponse,
    TOTPVerifyRequest,
)
from app.schemas.users import UserResponse
from app.security.passwords import verify_password
from app.security.session import SessionData, ensure_datetime, session_manager


router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()
LOGIN_RATE_LIMIT_WINDOW = 300  # 5 minutes
LOGIN_RATE_LIMIT_ATTEMPTS = 5


def _serialize_session(session: SessionData) -> SessionInfo:
    return SessionInfo(
        id=session.id,
        user_id=session.user_id,
        created_at=session.created_at,
        expires_at=session.expires_at,
        last_seen_at=session.last_seen_at,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        csrf_token=session.csrf_token,
        active_company_id=session.active_company_id,
    )


def _build_login_response(user: dict, session: SessionData) -> LoginResponse:
    return LoginResponse(
        user=UserResponse.model_validate(user),
        session=_serialize_session(session),
    )


async def _determine_active_company_id(user: dict[str, Any]) -> int | None:
    raw_company = user.get("company_id")
    if raw_company is not None:
        try:
            return int(raw_company)
        except (TypeError, ValueError):
            pass
    companies = await user_company_repo.list_companies_for_user(user["id"])
    if companies:
        return int(companies[0].get("company_id"))
    return None


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register the initial super administrator",
)
async def register(
    payload: RegistrationRequest,
    request: Request,
    _: None = Depends(require_database),
) -> Response:
    existing_users = await user_repo.count_users()
    if existing_users > 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is closed")

    created = await user_repo.create_user(
        email=payload.email,
        password=payload.password,
        first_name=payload.first_name,
        last_name=payload.last_name,
        mobile_phone=payload.mobile_phone,
        company_id=payload.company_id,
        is_super_admin=True,
    )

    active_company_id = await _determine_active_company_id(created)
    session = await session_manager.create_session(
        created["id"], request, active_company_id=active_company_id
    )
    if active_company_id is not None:
        created["company_id"] = active_company_id
    response_model = _build_login_response(created, session)
    response = JSONResponse(content=response_model.model_dump(mode="json"))
    session_manager.apply_session_cookies(response, session)
    return response


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate a user and establish a session",
)
async def login(
    payload: LoginRequest,
    request: Request,
    _: None = Depends(require_database),
) -> Response:
    identifier_parts = [payload.email.lower()]
    if request.client:
        identifier_parts.append(request.client.host)
    identifier = ":".join(identifier_parts)

    allowed = await auth_repo.register_login_attempt(
        identifier, window_seconds=LOGIN_RATE_LIMIT_WINDOW, max_attempts=LOGIN_RATE_LIMIT_ATTEMPTS
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts")

    user = await user_repo.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    totp_devices = await auth_repo.get_totp_authenticators(user["id"])
    if totp_devices:
        if not payload.totp_code:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOTP code required")
        verified = False
        for device in totp_devices:
            totp = pyotp.TOTP(device["secret"])
            if totp.verify(payload.totp_code, valid_window=1):
                verified = True
                break
        if not verified:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")

    await auth_repo.clear_login_attempts(identifier)

    active_company_id = await _determine_active_company_id(user)
    session = await session_manager.create_session(
        user["id"], request, active_company_id=active_company_id
    )
    if active_company_id is not None:
        user["company_id"] = active_company_id
    response_model = _build_login_response(user, session)
    response = JSONResponse(content=response_model.model_dump(mode="json"))
    session_manager.apply_session_cookies(response, session)
    return response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate the current session",
)
async def logout(
    response: Response,
    session: SessionData = Depends(get_current_session),
) -> Response:
    await session_manager.revoke_session(session)
    session_manager.clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Return the current session",
)
async def get_session(
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> SessionResponse:
    return _build_login_response(current_user, session)


@router.post(
    "/password/forgot",
    response_model=PasswordResetStatus,
    summary="Request a password reset email",
)
async def password_forgot(
    payload: PasswordResetRequest,
    _: None = Depends(require_database),
) -> PasswordResetStatus:
    user = await user_repo.get_user_by_email(payload.email)
    if not user:
        return PasswordResetStatus(detail="If the email is registered, reset instructions have been sent.")

    token = secrets.token_hex(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    await auth_repo.create_password_reset_token(
        user_id=user["id"], token=token, expires_at=expires_at
    )

    # The email dispatch mirrors the legacy behaviour but is handled asynchronously by the worker service.
    return PasswordResetStatus(detail="If the email is registered, reset instructions have been sent.")


@router.post(
    "/password/reset",
    response_model=PasswordResetStatus,
    summary="Reset a password using a valid token",
)
async def password_reset(
    payload: PasswordResetConfirm,
    _: None = Depends(require_database),
) -> PasswordResetStatus:
    record = await auth_repo.get_password_reset_token(payload.token)
    if not record or int(record.get("used", 0)) == 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    expires_at = record.get("expires_at")
    if expires_at:
        expires_dt = ensure_datetime(expires_at)
        if datetime.utcnow() > expires_dt:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    await user_repo.set_user_password(record["user_id"], payload.password)
    await auth_repo.mark_password_reset_token_used(payload.token)
    return PasswordResetStatus(detail="Password reset successful.")


@router.get(
    "/totp",
    response_model=TOTPListResponse,
    summary="List registered TOTP authenticators",
)
async def list_totp_devices(
    current_user: dict = Depends(get_current_user),
) -> TOTPListResponse:
    devices = await auth_repo.get_totp_authenticators(current_user["id"])
    return TOTPListResponse(
        items=[TOTPAuthenticator(id=device["id"], name=device["name"]) for device in devices]
    )


@router.post(
    "/totp/setup",
    response_model=TOTPSetupResponse,
    summary="Begin TOTP enrolment",
)
async def setup_totp(
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> TOTPSetupResponse:
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=current_user["email"], issuer_name=settings.app_name)
    await session_manager.store_pending_totp_secret(session, secret)
    return TOTPSetupResponse(secret=secret, otpauth_url=provisioning_uri)


@router.post(
    "/totp/verify",
    response_model=TOTPAuthenticator,
    summary="Verify and activate a TOTP authenticator",
)
async def verify_totp(
    payload: TOTPVerifyRequest,
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> TOTPAuthenticator:
    secret = session.pending_totp_secret
    if not secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No pending TOTP secret")

    totp = pyotp.TOTP(secret)
    if not totp.verify(payload.code, valid_window=1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TOTP code")

    name = payload.name or "Authenticator"
    authenticator = await auth_repo.create_totp_authenticator(
        user_id=current_user["id"], name=name, secret=secret
    )
    await session_manager.clear_pending_totp_secret(session)
    return TOTPAuthenticator(id=authenticator["id"], name=authenticator["name"])


@router.delete(
    "/totp/{authenticator_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a TOTP authenticator",
)
async def delete_totp(
    authenticator_id: int,
    current_user: dict = Depends(get_current_user),
) -> Response:
    await auth_repo.delete_totp_authenticator(current_user["id"], authenticator_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
