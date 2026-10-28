"""Public endpoints used by the MyPortal Outlook signature add-in.

Outlook loads the add-in's launch page and script without a portal session,
so these routes take no cookies.  The signature endpoint authenticates each
call with the signer's Microsoft access token instead (see
:func:`app.services.m365_signature_addin.validate_addin_token`).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.repositories import companies as companies_repo
from app.services import m365 as m365_service
from app.services import m365_signature_addin as addin_service


router = APIRouter(tags=["Outlook Signature Add-in"])

_ADDIN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ASSET_HEADERS = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}


class AddinSignatureResponse(BaseModel):
    html: str
    name: str


async def _addin_config(addin_id: str) -> tuple[str, str]:
    """Return ``(base_url, client_id)`` for a known add-in id, else 404."""
    base_url = addin_service.portal_base_url()
    company = (
        await companies_repo.get_company_by_outlook_addin_id(addin_id)
        if _ADDIN_ID.match(addin_id)
        else None
    )
    creds = await m365_service.get_credentials(int(company["id"])) if company else None
    client_id = str((creds or {}).get("client_id") or "").strip()
    if not base_url or not client_id:
        raise HTTPException(status_code=404, detail="Add-in not found")
    return base_url, client_id


@router.get("/outlook-addin/{addin_id}/launch.html", include_in_schema=False)
async def addin_launch_page(addin_id: str) -> Response:
    base_url, _ = await _addin_config(addin_id)
    return Response(
        addin_service.build_launch_html(addin_id=addin_id, base_url=base_url),
        media_type="text/html",
        headers=_ASSET_HEADERS,
    )


@router.get("/outlook-addin/{addin_id}/launch.js", include_in_schema=False)
async def addin_launch_script(addin_id: str) -> Response:
    base_url, client_id = await _addin_config(addin_id)
    return Response(
        addin_service.build_launch_script(client_id=client_id, base_url=base_url),
        media_type="text/javascript",
        headers=_ASSET_HEADERS,
    )


@router.get("/outlook-addin/icon-{size}.png", include_in_schema=False)
async def addin_icon(size: int) -> Response:
    if size not in addin_service.ICON_SIZES:
        raise HTTPException(status_code=404, detail="Icon not found")
    return Response(
        addin_service.render_icon(size),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get(
    "/api/outlook-addin/signature",
    response_model=AddinSignatureResponse,
    summary="Rendered MyPortal signature for the signed-in Outlook user",
    responses={
        204: {"description": "Nothing to insert for this user"},
        401: {"description": "Missing or invalid Microsoft access token"},
    },
)
async def addin_signature(request: Request) -> Response:
    """Return the caller's primary signature for the Outlook add-in to insert.

    Authenticate with ``Authorization: Bearer <token>``, a Microsoft v2 access
    token for the company's MyPortal app carrying the ``access_as_user`` scope.
    Returns 204 when the company does not deliver signatures through the
    add-in or no active signature applies to the user.
    """
    scheme, _, token = str(request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        signature = await addin_service.signature_for_token(token.strip())
    except addin_service.AddinAuthError:
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    if not signature:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        AddinSignatureResponse(**signature).model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )
