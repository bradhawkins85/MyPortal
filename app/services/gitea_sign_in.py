"""MyPortal sign-in for the bundled Gitea.

nginx asks MyPortal who is signed in (``GET /api/rmm/gitea/identity``) on each
``/gitea/`` request and passes the answer to Gitea in the ``X-WEBAUTH-*``
headers, which Gitea trusts only from its proxy. Gitea itself keeps a
persistent session, so the proxy forwards the browser's cookies to Gitea only
while MyPortal forwarded an identity; otherwise it strips them so a stale
Gitea session cannot linger. That is what signs a technician out of Gitea on
their next click after they lose Script editing access or sign out of MyPortal.

The first time a technician comes through, MyPortal makes Gitea create their
account and adds it to the script repository as a collaborator, with read or
write access to match the ``menu.rmm_script_editing`` permission. The
background folder check (``rmm_scripts.folder_maintenance_loop``) lowers or
removes that access when the technician's roles change.

The login is always one MyPortal made (``gitea_login``); a stored value that is
not is dropped, because the sign-in headers create Gitea accounts on first use
and must never carry a hand-made or malformed name.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from app.api.dependencies.auth import is_user_active
from app.core.logging import log_info, log_warning
from app.repositories import company_memberships as membership_repo
from app.repositories import rmm as rmm_repo
from app.repositories import users as user_repo
from app.security.menu_permissions import normalize_menu_permissions
from app.services import gitea

PERMISSION = "menu.rmm_script_editing"
_RANK = {"none": 0, "read": 1, "write": 2}
# How long a worker trusts that it already gave a technician their access.
GRANT_REFRESH_SECONDS = 600
_LOGIN_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_LOGIN_PUNCTUATION = re.compile(r"[._-]{2,}")
_HEADER_UNSAFE = re.compile(r"[^\x20-\x7e]+")
# The shape of a finished Gitea login (what gitea_login() returns): an
# email-derived name of letters, digits, ".", "_" or "-", with no leading or
# trailing punctuation and at most 30 characters, plus the user's id. The id
# suffix keeps it from ever being a hand-made account, such as the "myportal"
# administrator.
_LOGIN_SHAPE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,28}[A-Za-z0-9])?-\d+")

_granted: dict[int, tuple[str, float]] = {}
_locks: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class GiteaIdentity:
    login: str
    email: str
    full_name: str
    permission: str


async def access_level(user: Mapping[str, Any]) -> str:
    """``none``, ``read`` or ``write``: the best Script editing access any of
    the user's active company roles grants. Super administrators can always
    edit."""

    if user.get("is_super_admin"):
        return "write"
    level = "none"
    for membership in await membership_repo.list_memberships_for_user(int(user["id"])):
        granted = normalize_menu_permissions(membership.get("permissions")).get(PERMISSION, "none")
        if _RANK.get(granted, 0) > _RANK[level]:
            level = granted
    return level


def gitea_login(user: Mapping[str, Any]) -> str:
    """The Gitea account name for a MyPortal user: the start of their email
    address plus their user id, so it can never match an account someone
    created by hand in Gitea (such as its ``myportal`` administrator)."""

    local = str(user.get("email") or "").split("@", 1)[0]
    base = _LOGIN_PUNCTUATION.sub("-", _LOGIN_UNSAFE.sub("-", local)).strip("._-")[:30].strip("._-")
    return f"{base or 'user'}-{int(user['id'])}"


def _is_gitea_login(login: str) -> bool:
    """Whether ``login`` is one of the accounts MyPortal makes -- and not a
    hand-made or malformed account it must never sign someone in as."""

    return bool(_LOGIN_SHAPE.fullmatch(login))


def _header_text(value: Any) -> str:
    """Printable ASCII only, as HTTP headers cannot carry other characters:
    accents are dropped ("Zoë" becomes "Zoe")."""

    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(_HEADER_UNSAFE.sub(" ", text).split())[:200]


def _full_name(user: Mapping[str, Any]) -> str:
    return _header_text(" ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")))


async def identity(user: Mapping[str, Any]) -> GiteaIdentity | None:
    """Who the user is in Gitea, or ``None`` when they may not sign in there."""

    level = await access_level(user)
    if level == "none":
        return None
    account = await rmm_repo.get_gitea_account(int(user["id"]))
    # The stored login keeps the Gitea account stable if the user's email
    # changes, but it must still be one MyPortal made: these sign-in headers
    # create Gitea accounts on first use, so a value that is not -- a tampered
    # row or a bug -- would be handed to Gitea as-is. An unexpected value falls
    # back to the canonical login.
    login = str(account["gitea_login"]) if account else gitea_login(user)
    if not _is_gitea_login(login):
        log_warning("Dropping an unexpected stored Gitea login", user_id=user["id"], login=login)
        login = gitea_login(user)
    result = GiteaIdentity(
        login=login,
        email=_header_text(user.get("email")),
        full_name=_full_name(user),
        permission=level,
    )
    await _ensure_access(int(user["id"]), result)
    return result


async def _ensure_access(user_id: int, account: GiteaIdentity) -> None:
    """Create the Gitea account and give it repository access, at most once
    per :data:`GRANT_REFRESH_SECONDS` per worker. A failure is logged and the
    technician still signs in; the next request retries."""

    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        cached = _granted.get(user_id)
        if cached and cached[0] == account.permission and time.monotonic() - cached[1] < GRANT_REFRESH_SECONDS:
            return
        try:
            settings = await gitea.load_settings()
            await gitea.sign_in_as(settings, account.login, email=account.email, full_name=account.full_name)
            await gitea.set_collaborator(settings, account.login, account.permission)
        except gitea.GiteaError as exc:
            log_warning("Could not give a technician access to the script repository", login=account.login, error=str(exc))
            return
        await rmm_repo.save_gitea_account(user_id, account.login, account.permission)
        _granted[user_id] = (account.permission, time.monotonic())


async def reconcile_accounts() -> int:
    """Match each Gitea account's repository access to its user's current
    Script editing access. Returns the number of accounts changed."""

    accounts = [row for row in await rmm_repo.list_gitea_accounts() if row.get("permission") != "none"]
    if not accounts:
        return 0
    settings = await gitea.load_settings()
    changed = 0
    for account in accounts:
        user_id = int(account["user_id"])
        user = await user_repo.get_user_by_id(user_id)
        level = await access_level(user) if user and is_user_active(user) else "none"
        if level == account["permission"]:
            continue
        login = str(account["gitea_login"])
        try:
            if level == "none":
                await gitea.remove_collaborator(settings, login)
            else:
                await gitea.set_collaborator(settings, login, level)
        except gitea.GiteaError as exc:
            log_warning("Could not update a technician's script repository access", login=login, error=str(exc))
            continue
        await rmm_repo.save_gitea_account(user_id, login, level)
        _granted.pop(user_id, None)
        changed += 1
        log_info("Script repository access updated", login=login, permission=level)
    return changed
