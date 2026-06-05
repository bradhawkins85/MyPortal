"""Server-side flash messaging via HMAC-signed HttpOnly cookie.

Flash messages are stored in a short-lived ``_flash`` cookie instead of URL
query parameters, so success/error text is never visible in the browser
address bar.

Usage (in a POST handler)::

    from app.security.flash import flash_redirect

    return flash_redirect("/admin/foo", "Saved successfully.", "success")

Usage (read automatically in ``_render_template``; no action required in GET
handlers unless the feature pack renders its own response).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal

from fastapi import Request, Response
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_303_SEE_OTHER

from app.core.config import get_settings

_COOKIE_NAME = "_flash"
_MAX_AGE = 60  # seconds – cookie is only needed until the next page load
_VALID_VARIANTS = frozenset({"info", "success", "warning", "error"})


def _secret() -> bytes:
    return get_settings().secret_key.encode()


def _sign(payload: str) -> str:
    """Return ``payload|sig`` where *sig* is a truncated HMAC-SHA256 hex."""
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}|{sig}"


def _verify(signed: str) -> str | None:
    """Return the raw payload if the signature is valid, else *None*."""
    if "|" not in signed:
        return None
    payload, sig = signed.rsplit("|", 1)
    expected_sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return None
    return payload


def set_flash(
    response: Response,
    message: str,
    variant: Literal["info", "success", "warning", "error"] = "info",
) -> None:
    """Attach a flash cookie to *response*.

    The cookie is ``HttpOnly``, ``SameSite=Lax``, and expires after 60 s.
    It is readable only by the server (``pop_flash``).
    """
    safe_variant = variant if variant in _VALID_VARIANTS else "info"
    payload = json.dumps({"message": str(message)[:200], "variant": safe_variant})
    signed = _sign(payload)
    settings = get_settings()
    secure = settings.environment.lower() == "production"
    response.set_cookie(
        _COOKIE_NAME,
        signed,
        httponly=True,
        secure=secure,
        max_age=_MAX_AGE,
        samesite="lax",
    )


def pop_flash(request: Request) -> dict[str, str] | None:
    """Read and validate the flash cookie from *request*.

    Returns a ``{"message": ..., "variant": ...}`` dict, or *None* if the
    cookie is absent or has an invalid signature.  The caller is responsible
    for deleting the cookie from the response (use ``clear_flash``).
    """
    raw = request.cookies.get(_COOKIE_NAME)
    if not raw:
        return None
    payload = _verify(raw)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    message = data.get("message")
    variant = data.get("variant", "info")
    if not isinstance(message, str) or not message.strip():
        return None
    if variant not in _VALID_VARIANTS:
        variant = "info"
    return {"message": message.strip(), "variant": variant}


def clear_flash(response: Response) -> None:
    """Delete the flash cookie from *response*."""
    response.delete_cookie(_COOKIE_NAME)


def flash_redirect(
    url: str,
    message: str,
    variant: Literal["info", "success", "warning", "error"] = "info",
    *,
    status_code: int = HTTP_303_SEE_OTHER,
) -> RedirectResponse:
    """Return a ``RedirectResponse`` carrying a flash cookie.

    Example::

        return flash_redirect("/admin/foo", "Saved.", "success")
    """
    response = RedirectResponse(url=url, status_code=status_code)
    set_flash(response, message, variant)
    return response
