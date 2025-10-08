from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import Request, Response

from app.core.config import get_settings
from app.repositories import auth as auth_repo
from app.security.encryption import decrypt_secret, encrypt_secret


@dataclass
class SessionData:
    id: int
    user_id: int
    session_token: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    ip_address: str | None
    user_agent: str | None
    active_company_id: int | None = None
    pending_totp_secret: str | None = None


class SessionManager:
    def __init__(self) -> None:
        self._settings = get_settings()
        self.session_cookie_name = self._settings.session_cookie_name
        self.csrf_cookie_name = f"{self.session_cookie_name}_csrf"
        self.session_ttl = timedelta(hours=12)

    def _is_secure(self) -> bool:
        return self._settings.environment.lower() == "production"

    async def create_session(
        self,
        user_id: int,
        request: Request,
        *,
        active_company_id: int | None = None,
    ) -> SessionData:
        now = datetime.utcnow()
        expires_at = now + self.session_ttl
        session_token = secrets_token()
        csrf_token = secrets_token()
        ip_address = request.headers.get("x-forwarded-for")
        if ip_address:
            ip_address = ip_address.split(",")[0].strip()
        elif request.client:
            ip_address = request.client.host
        user_agent = request.headers.get("user-agent")
        record = await auth_repo.create_session(
            user_id=user_id,
            active_company_id=active_company_id,
            session_token=session_token,
            csrf_token=csrf_token,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return self._map_session(record)

    async def load_session(
        self,
        request: Request,
        *,
        allow_inactive: bool = False,
    ) -> Optional[SessionData]:
        cached: SessionData | None = getattr(request.state, "session", None)
        if cached:
            return cached
        token = request.cookies.get(self.session_cookie_name)
        if not token:
            return None
        record = await auth_repo.get_session_by_token(token)
        if not record:
            return None
        if not allow_inactive and int(record.get("is_active", 0)) != 1:
            return None
        expires_at = ensure_datetime(record.get("expires_at"))
        now = datetime.utcnow()
        if expires_at and expires_at < now:
            await auth_repo.update_session(record["id"], is_active=False)
            return None
        session = self._map_session(record)
        await auth_repo.update_session(
            session.id,
            last_seen_at=now,
            expires_at=now + self.session_ttl,
        )
        session.expires_at = now + self.session_ttl
        session.last_seen_at = now
        request.state.session = session
        request.state.active_company_id = session.active_company_id
        return session

    async def refresh_csrf(self, session: SessionData) -> SessionData:
        new_token = secrets_token()
        await auth_repo.update_session(session.id, csrf_token=new_token)
        session.csrf_token = new_token
        return session

    async def store_pending_totp_secret(self, session: SessionData, secret: str) -> None:
        encrypted = encrypt_secret(secret)
        await auth_repo.update_session(session.id, pending_totp_secret=encrypted)
        session.pending_totp_secret = secret

    async def clear_pending_totp_secret(self, session: SessionData) -> None:
        await auth_repo.update_session(session.id, pending_totp_secret=None)
        session.pending_totp_secret = None

    async def revoke_session(self, session: SessionData) -> None:
        await auth_repo.deactivate_session(session.id)

    async def set_active_company(self, session: SessionData, company_id: int | None) -> None:
        await auth_repo.update_session(session.id, active_company_id=company_id)
        session.active_company_id = company_id

    def apply_session_cookies(self, response: Response, session: SessionData) -> None:
        max_age = int(self.session_ttl.total_seconds())
        secure = self._is_secure()
        response.set_cookie(
            self.session_cookie_name,
            session.session_token,
            httponly=True,
            secure=secure,
            max_age=max_age,
            samesite="lax",
        )
        response.set_cookie(
            self.csrf_cookie_name,
            session.csrf_token,
            httponly=False,
            secure=secure,
            max_age=max_age,
            samesite="lax",
        )

    def clear_session_cookies(self, response: Response) -> None:
        response.delete_cookie(self.session_cookie_name)
        response.delete_cookie(self.csrf_cookie_name)

    def _map_session(self, record: dict[str, Any]) -> SessionData:
        pending_secret = record.get("pending_totp_secret")
        if pending_secret:
            pending_secret = decrypt_secret(pending_secret)
        return SessionData(
            id=record["id"],
            user_id=record["user_id"],
            session_token=record["session_token"],
            csrf_token=record["csrf_token"],
            created_at=ensure_datetime(record.get("created_at")),
            expires_at=ensure_datetime(record.get("expires_at")),
            last_seen_at=ensure_datetime(record.get("last_seen_at")),
            ip_address=record.get("ip_address"),
            user_agent=record.get("user_agent"),
            active_company_id=(
                int(record["active_company_id"])
                if record.get("active_company_id") is not None
                else None
            ),
            pending_totp_secret=pending_secret,
        )


def ensure_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.utcnow()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")


def secrets_token() -> str:
    import secrets

    return secrets.token_hex(32)


session_manager = SessionManager()
