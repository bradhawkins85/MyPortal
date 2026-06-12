from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from html import escape
from typing import Any

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger

from app.api.dependencies.auth import (
    get_current_session,
    get_current_user,
    require_super_admin,
)
from app.api.dependencies.database import require_database
from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.repositories import auth as auth_repo
from app.repositories import companies as company_repo
from app.repositories import staff as staff_repo
from app.repositories import users as user_repo
from app.repositories import user_companies as user_company_repo
from app.schemas.auth import (
    ImpersonationRequest,
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
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
from app.services import company_access
from app.services import impersonation as impersonation_service
from app.services import message_templates as message_templates_service
from app.services import email as email_service
from app.services import staff_access as staff_access_service


router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()
LOGIN_RATE_LIMIT_WINDOW = 300  # 5 minutes
LOGIN_RATE_LIMIT_ATTEMPTS = 5



def _html_to_text(html: str) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&nbsp;", " ").strip()


def _template_context(*, user: dict[str, Any], token: str, link: str, company: dict[str, Any] | None = None) -> dict[str, Any]:
    first_name = user.get("first_name") or ""
    last_name = user.get("last_name") or ""
    full_name = " ".join(part for part in (first_name, last_name) if part).strip() or user.get("email") or "there"
    portal_url = str(settings.portal_url).rstrip("/") if settings.portal_url else ""
    return {
        "app": {"name": settings.app_name},
        "portal": {"url": portal_url, "login_url": f"{portal_url}/login" if portal_url else "/login"},
        "user": {
            "email": user.get("email") or "",
            "first_name": first_name,
            "last_name": last_name,
            "name": full_name,
        },
        "company": {"name": (company or {}).get("name") or ""},
        "token": token,
        "link": link,
        "verification": {"link": link, "token": token},
        "invitation": {"link": link, "token": token},
    }


async def _render_email_template(slug: str, context: dict[str, Any], default_html: str) -> tuple[str, str]:
    rendered, content_type = await message_templates_service.render_template_content(
        slug,
        context,
        default_content=default_html,
        default_content_type="text/html",
    )
    if content_type == "text/html":
        return rendered, _html_to_text(rendered)
    text_body = rendered
    html_body = "<p>" + escape(text_body).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    return html_body, text_body


async def _send_signup_verification_email(user: dict[str, Any], token: str) -> None:
    base_url = str(settings.portal_url).rstrip("/") if settings.portal_url else None
    verify_path = f"/auth/verify-email?token={token}"
    verify_link = f"{base_url}{verify_path}" if base_url else verify_path
    context = _template_context(user=user, token=token, link=verify_link)
    default_html = (
        "<p>Hello {{ user.name }},</p>"
        "<p>Thanks for signing up for {{ app.name }}. Please verify your email address before signing in.</p>"
        "<p><a href=\"{{ verification.link }}\">Verify your signup</a></p>"
        "<p>This link expires in 24 hours. If you did not create this account, you can ignore this email.</p>"
    )
    html_body, text_body = await _render_email_template("signup_verification", context, default_html)
    try:
        sent, event_metadata = await email_service.send_email(
            subject=f"Verify your {settings.app_name} signup",
            recipients=[user["email"]],
            text_body=text_body,
            html_body=html_body,
        )
        if not sent:
            logger.warning(
                "Signup verification email skipped because SMTP is not configured",
                user_id=user["id"],
                event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
            )
    except email_service.EmailDispatchError as exc:  # pragma: no cover - log and continue
        logger.error("Failed to dispatch signup verification email", user_id=user["id"], error=str(exc))


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
        impersonator_user_id=session.impersonator_user_id,
        impersonator_session_id=session.impersonator_session_id,
        impersonation_started_at=session.impersonation_started_at,
    )


def _build_login_response(user: dict, session: SessionData) -> LoginResponse:
    return LoginResponse(
        user=UserResponse.model_validate(user),
        session=_serialize_session(session),
    )


async def _determine_active_company_id(user: dict[str, Any]) -> int | None:
    return await company_access.first_accessible_company_id(user)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "unknown")


def _log_login_failure(request: Request, email: str, reason: str) -> None:
    normalized_email = str(email or "").lower()
    ip = _client_ip(request)
    log_error(
        f"AUTH LOGIN FAIL email={normalized_email} ip={ip} reason={reason}",
        user_agent=_user_agent(request),
    )


def _log_login_success(request: Request, user: dict[str, Any]) -> None:
    email = str(user.get("email", "")).lower()
    ip = _client_ip(request)
    user_id = user.get("id")
    log_info(
        f"AUTH LOGIN SUCCESS email={email} user_id={user_id} ip={ip}",
        user_agent=_user_agent(request),
    )


@router.post(
    "/register",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
)
async def register(
    payload: RegistrationRequest,
    request: Request,
    _: None = Depends(require_database),
) -> Response:
    existing_users = await user_repo.count_users()
    is_first_user = existing_users == 0

    existing_user = await user_repo.get_user_by_email(payload.email)
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    matched_company_id: int | None = None
    matched_staff: dict[str, Any] | None = None
    if not is_first_user:
        staff_matches = await staff_repo.list_staff_by_email(payload.email)
        active_staff_matches = [staff for staff in staff_matches if bool(staff.get("enabled", True))]
        if active_staff_matches:
            matched_staff = active_staff_matches[0]
            raw_staff_company_id = matched_staff.get("company_id")
            try:
                matched_company_id = int(raw_staff_company_id) if raw_staff_company_id is not None else None
            except (TypeError, ValueError):
                matched_company_id = None

        if matched_company_id is None:
            domain = payload.email.split("@")[-1].strip().lower()
            matched = await company_repo.get_company_by_email_domain(domain)
            if not matched:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Registration is restricted to approved company domains or existing staff records",
                )

            raw_company_id = matched.get("id")
            if raw_company_id is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Registration is restricted to approved company domains or existing staff records",
                )

            try:
                matched_company_id = int(raw_company_id)
            except (TypeError, ValueError) as exc:
                log_error(
                    "Failed to coerce matched company identifier during registration",
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Registration is restricted to approved company domains or existing staff records",
                ) from exc

    created = await user_repo.create_user(
        email=payload.email,
        password=payload.password,
        first_name=payload.first_name or (matched_staff or {}).get("first_name"),
        last_name=payload.last_name or (matched_staff or {}).get("last_name"),
        mobile_phone=payload.mobile_phone or (matched_staff or {}).get("mobile_phone"),
        company_id=payload.company_id if is_first_user else matched_company_id,
        is_super_admin=is_first_user,
    )

    if matched_company_id is not None:
        await user_company_repo.assign_user_to_company(
            user_id=created["id"],
            company_id=matched_company_id,
        )

    await staff_access_service.apply_pending_access_for_user(created)

    if not is_first_user:
        await user_repo.update_user(created["id"], is_active=0, email_verified_at=None)
        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        await auth_repo.create_account_verification_token(
            user_id=created["id"], token=token, expires_at=expires_at
        )
        await _send_signup_verification_email(created, token)
        return JSONResponse(
            content={
                "detail": "Account created. Check your email to verify your signup before signing in.",
                "verification_required": True,
            },
            status_code=status.HTTP_202_ACCEPTED,
        )

    active_company_id = await _determine_active_company_id(created)
    session = await session_manager.create_session(
        created["id"], request, active_company_id=active_company_id
    )
    if active_company_id is not None:
        created["company_id"] = active_company_id
    response_model = _build_login_response(created, session)
    response = JSONResponse(
        content=response_model.model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
    )
    session_manager.apply_session_cookies(response, session, request)
    return response



@router.get(
    "/verify-email",
    summary="Verify a signup email address",
)
async def verify_email(token: str, _: None = Depends(require_database)) -> Response:
    token = token.strip()
    record = await auth_repo.get_account_verification_token(token)
    if not record:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link")

    user = await user_repo.get_user_by_id(record["user_id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link")

    if user.get("email_verified_at"):
        return RedirectResponse(url="/login?verified=1", status_code=status.HTTP_303_SEE_OTHER)

    if int(record.get("used", 0)) == 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link")

    expires_at = record.get("expires_at")
    if expires_at and datetime.utcnow() > ensure_datetime(expires_at):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired verification link")

    await user_repo.update_user(record["user_id"], is_active=1, email_verified_at=datetime.utcnow())
    await auth_repo.mark_account_verification_token_used(token)
    return RedirectResponse(url="/login?verified=1", status_code=status.HTTP_303_SEE_OTHER)


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
        _log_login_failure(request, payload.email, "rate_limited")
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts")

    user = await user_repo.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        _log_login_failure(request, payload.email, "invalid_credentials")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if int(user.get("is_active", 1)) != 1:
        _log_login_failure(request, payload.email, "account_disabled")
        detail = "Please verify your email address before signing in." if not user.get("email_verified_at") else "Account is disabled"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    totp_devices = await auth_repo.get_totp_authenticators(user["id"])
    if totp_devices:
        if not payload.totp_code:
            _log_login_failure(request, payload.email, "totp_required")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOTP code required")
        verified = False
        for device in totp_devices:
            totp = pyotp.TOTP(device["secret"])
            if totp.verify(payload.totp_code, valid_window=1):
                verified = True
                break
        if not verified:
            _log_login_failure(request, payload.email, "invalid_totp")
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
    session_manager.apply_session_cookies(response, session, request)
    _log_login_success(request, user)
    return response


@router.post(
    "/impersonate",
    response_model=LoginResponse,
    summary="Begin impersonating a user with assigned permissions",
)
async def impersonate_user(
    payload: ImpersonationRequest,
    request: Request,
    current_user: dict = Depends(require_super_admin),
    session: SessionData = Depends(get_current_session),
) -> Response:
    try:
        target_user, impersonated_session = await impersonation_service.start_impersonation(
            request=request,
            actor_user=current_user,
            actor_session=session,
            target_user_id=payload.user_id,
        )
    except impersonation_service.SelfImpersonationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except impersonation_service.AlreadyImpersonatingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except impersonation_service.NotImpersonatableError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    response_model = _build_login_response(target_user, impersonated_session)
    response = JSONResponse(content=response_model.model_dump(mode="json"))
    session_manager.apply_session_cookies(response, impersonated_session, request)
    request.state.session = impersonated_session
    request.state.active_company_id = impersonated_session.active_company_id
    request.state.impersonator_user_id = impersonated_session.impersonator_user_id
    request.state.impersonator_session_id = impersonated_session.impersonator_session_id
    return response


@router.post(
    "/impersonation/exit",
    response_model=LoginResponse,
    summary="Exit impersonation mode and restore the original session",
)
async def exit_impersonation(
    request: Request,
    session: SessionData = Depends(get_current_session),
) -> Response:
    try:
        restored_user, restored_session = await impersonation_service.end_impersonation(
            request=request,
            session=session,
        )
    except impersonation_service.NotImpersonatingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except impersonation_service.OriginalSessionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    accept_header = request.headers.get("accept", "").lower()
    prefers_html = (
        ("text/html" in accept_header or "application/xhtml+xml" in accept_header)
        and "application/json" not in accept_header
    )

    if prefers_html:
        response = RedirectResponse(
            url="/admin/impersonation",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    else:
        response_model = _build_login_response(restored_user, restored_session)
        response = JSONResponse(content=response_model.model_dump(mode="json"))
    session_manager.apply_session_cookies(response, restored_session, request)
    request.state.session = restored_session
    request.state.active_company_id = restored_session.active_company_id
    if hasattr(request.state, "impersonator_user_id"):
        request.state.impersonator_user_id = None
    if hasattr(request.state, "impersonator_session_id"):
        request.state.impersonator_session_id = None
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

    base_url = str(settings.portal_url).rstrip("/") if settings.portal_url else None
    reset_path = f"/reset-password?token={token}"
    reset_link = f"{base_url}{reset_path}" if base_url else reset_path
    display_name = escape(user.get("first_name") or user.get("email") or "there")
    text_body = (
        f"Hello {user.get('first_name') or 'there'},\n\n"
        f"We received a request to reset your {settings.app_name} password. "
        f"Use the link below to choose a new password:\n\n"
        f"{reset_link}\n\n"
        f"If the link does not work, use the following token when prompted: {token}\n\n"
        "If you did not request a reset you can ignore this email."
    )
    html_body = (
        f"<p>Hello {display_name},</p>"
        f"<p>We received a request to reset your {escape(settings.app_name)} password.</p>"
        f"<p><a href=\"{escape(reset_link)}\">Reset your password</a></p>"
        f"<p>If the link does not work, use this token when prompted: <code>{escape(token)}</code></p>"
        "<p>If you did not request a reset you can ignore this email.</p>"
    )
    try:
        sent, event_metadata = await email_service.send_email(
            subject=f"Reset your {settings.app_name} password",
            recipients=[user["email"]],
            text_body=text_body,
            html_body=html_body,
        )
        if not sent:
            logger.warning(
                "Password reset email skipped because SMTP is not configured",
                user_id=user["id"],
                event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
            )
    except email_service.EmailDispatchError as exc:  # pragma: no cover - log and continue
        logger.error(
            "Failed to dispatch password reset email",
            user_id=user["id"],
            error=str(exc),
        )

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


@router.post(
    "/password/change",
    response_model=PasswordResetStatus,
    summary="Change the authenticated user's password",
)
async def change_password(
    payload: PasswordChangeRequest,
    current_user: dict = Depends(get_current_user),
) -> PasswordResetStatus:
    stored_hash = current_user.get("password_hash")
    if not stored_hash or not verify_password(payload.current_password, stored_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password",
        )

    await user_repo.set_user_password(current_user["id"], payload.new_password)
    return PasswordResetStatus(detail="Password updated successfully.")


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
