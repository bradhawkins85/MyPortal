from __future__ import annotations

import secrets
from typing import Any

from app.core.config import get_settings
from app.services.matrix import MatrixConfigError, _admin_headers, _request

_settings = get_settings()


async def create_or_update_user(
    user_id: str,
    *,
    password: str,
    display_name: str | None = None,
    deactivated: bool = False,
) -> dict[str, Any]:
    """Create or update a Synapse user via admin API."""
    body: dict[str, Any] = {"password": password, "deactivated": deactivated}
    if display_name:
        body["displayname"] = display_name
    return await _request(
        "PUT",
        f"/_synapse/admin/v2/users/{user_id}",
        headers=_admin_headers(),
        json=body,
    )


async def deactivate_user(user_id: str) -> dict[str, Any]:
    """Deactivate a Synapse user and erase their data."""
    return await _request(
        "POST",
        f"/_synapse/admin/v1/deactivate/{user_id}",
        headers=_admin_headers(),
        json={"erase": False},
    )


async def reset_user_password(user_id: str, new_password: str) -> None:
    """Reset a Synapse user's password."""
    await create_or_update_user(user_id, password=new_password)


def generate_password() -> str:
    """Generate a strong random password for provisioned users."""
    return secrets.token_urlsafe(24)
