"""Tray deployment URLs: public download pages and admin API.

``/deploy/{slug}`` is public. The slug is the credential, so every response
is marked ``no-store`` and ``no-referrer`` to keep it out of caches and
third-party logs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.background import BackgroundTask

from app.api.dependencies.api_keys import require_api_key
from app.api.dependencies.auth import require_super_admin
from app.core.logging import log_error, log_info
from app.repositories import tray as tray_repo
from app.schemas.tray import (
    TrayDeploymentBuildFailure,
    TrayDeploymentLinkCreate,
    TrayDeploymentLinkResponse,
)
from app.services import audit as audit_service
from app.services import tray_deployment
from app.services import tray_deployment_builds as builds

router = APIRouter(tags=["Tray App"])

_PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


def _main():
    from app import main as main_module

    return main_module


async def _render_page(
    request: Request,
    *,
    slug: str,
    state: str,
    company_name: str | None = None,
    message: str | None = None,
    windows_ready: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    main = _main()
    portal_url = tray_deployment.resolve_portal_url(request)
    context = await main._build_public_context(
        request,
        extra={
            "title": "Install MyPortal Tray",
            "state": state,
            "message": message,
            "company_name": company_name,
            "slug": slug,
            "windows_ready": windows_ready,
            "macos_ready": state == "open" and tray_deployment.installer_path("macos") is not None,
            "deploy_base": portal_url + tray_deployment.link_path(slug) if slug else "",
        },
    )
    response = main.templates.TemplateResponse(request, "tray/deploy.html", context)
    response.status_code = status_code
    response.headers.update(_PRIVATE_HEADERS)
    return response


@router.get("/deploy/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def deployment_page(slug: str, request: Request) -> HTMLResponse:
    try:
        link, _token = await tray_deployment.resolve_active_link(slug)
    except tray_deployment.DeploymentLinkUnavailable as exc:
        return await _render_page(
            request,
            slug="",
            state="unavailable",
            message=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    windows_ready = await builds.ready_artifact(int(link["id"]), "exe") is not None
    return await _render_page(
        request,
        slug=slug,
        state="open",
        company_name=link.get("company_name"),
        windows_ready=windows_ready,
    )


async def _resolve_or_404(slug: str) -> tuple[dict, str]:
    try:
        return await tray_deployment.resolve_active_link(slug)
    except tray_deployment.DeploymentLinkUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _counted_file(link: dict, path: Path, kind: str, media_type: str, **kwargs) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        filename=tray_deployment.download_filename(link.get("company_name"), kind),
        headers=_PRIVATE_HEADERS,
        **kwargs,
    )


async def _windows_installer(slug: str, kind: str) -> FileResponse:
    link, _token = await _resolve_or_404(slug)
    path = await builds.ready_artifact(int(link["id"]), kind)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The Windows installer for this link is still being built.",
        )
    await tray_repo.mark_deployment_link_downloaded(int(link["id"]))
    log_info(
        "Tray deployment installer downloaded",
        link_id=link.get("id"),
        company_id=link.get("company_id"),
        kind=kind,
    )
    media_type = (
        "application/x-msi" if kind == "msi" else "application/vnd.microsoft.portable-executable"
    )
    return _counted_file(link, path, kind, media_type)


@router.get("/deploy/{slug}/windows.exe", include_in_schema=False)
async def deployment_windows_exe(slug: str) -> FileResponse:
    return await _windows_installer(slug, "exe")


@router.get("/deploy/{slug}/windows.msi", include_in_schema=False)
async def deployment_windows_msi(slug: str) -> FileResponse:
    return await _windows_installer(slug, "msi")


@router.get("/deploy/{slug}/macos.zip", include_in_schema=False)
async def deployment_macos_bundle(slug: str, request: Request) -> FileResponse:
    link, token = await _resolve_or_404(slug)
    portal_url = tray_deployment.resolve_portal_url(request)
    try:
        path = await asyncio.to_thread(tray_deployment.build_macos_bundle, portal_url, token)
    except tray_deployment.InstallerNotAvailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The macOS installer is not available on this server yet.",
        ) from exc
    except ValueError as exc:
        log_error("Unable to build tray deployment bundle", link_id=link.get("id"), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The installer package could not be built.",
        ) from exc
    await tray_repo.mark_deployment_link_downloaded(int(link["id"]))
    log_info(
        "Tray deployment installer downloaded",
        link_id=link.get("id"),
        company_id=link.get("company_id"),
        kind="macos",
    )
    return _counted_file(
        link,
        path,
        "macos",
        "application/zip",
        background=BackgroundTask(Path(path).unlink, missing_ok=True),
    )


async def _script(slug: str, request: Request, platform: str) -> PlainTextResponse:
    link, token = await _resolve_or_404(slug)
    portal_url = tray_deployment.resolve_portal_url(request)
    render = (
        tray_deployment.windows_script if platform == "windows" else tray_deployment.macos_script
    )
    try:
        body = render(portal_url, token)
    except ValueError as exc:
        log_error("Unable to render tray deployment script", link_id=link.get("id"), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The install script could not be built.",
        ) from exc
    await tray_repo.mark_deployment_link_downloaded(int(link["id"]))
    return PlainTextResponse(body, headers=_PRIVATE_HEADERS)


@router.get("/deploy/{slug}/install.ps1", include_in_schema=False)
async def deployment_windows_script(slug: str, request: Request) -> PlainTextResponse:
    return await _script(slug, request, "windows")


@router.get("/deploy/{slug}/install.sh", include_in_schema=False)
async def deployment_macos_script(slug: str, request: Request) -> PlainTextResponse:
    return await _script(slug, request, "macos")


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


@router.get(
    "/api/tray/admin/deployment-links",
    response_model=list[TrayDeploymentLinkResponse],
    summary="List tray deployment URLs (admin)",
)
async def list_deployment_links(
    request: Request,
    company_id: int | None = None,
    current_user: dict = Depends(require_super_admin),
) -> list[TrayDeploymentLinkResponse]:
    portal_url = tray_deployment.resolve_portal_url(request)
    rows = await tray_repo.list_deployment_links(company_id=company_id)
    return [
        TrayDeploymentLinkResponse(**await tray_deployment.serialise_link(row, portal_url))
        for row in rows
    ]


@router.post(
    "/api/tray/admin/deployment-links",
    response_model=TrayDeploymentLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tray deployment URL for a company (admin)",
)
async def create_deployment_link(
    payload: TrayDeploymentLinkCreate,
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> TrayDeploymentLinkResponse:
    try:
        record, _slug = await tray_deployment.create_deployment_link(
            company_id=payload.company_id,
            label=payload.label,
            created_by_user_id=int(current_user["id"]),
            expires_in_days=payload.expires_in_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await audit_service.log_action(
        action="tray.deployment_link.create",
        user_id=int(current_user["id"]),
        entity_type="tray_deployment_link",
        entity_id=int(record["id"]),
        new_value={
            "company_id": payload.company_id,
            "label": record.get("label"),
            "expires_at": str(record.get("expires_at") or "never"),
        },
        request=request,
    )
    portal_url = tray_deployment.resolve_portal_url(request)
    return TrayDeploymentLinkResponse(**await tray_deployment.serialise_link(record, portal_url))


@router.post(
    "/api/tray/admin/deployment-links/{link_id}/revoke",
    summary="Revoke a tray deployment URL and its install token (admin)",
)
async def revoke_deployment_link(
    link_id: int,
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    if not await tray_deployment.revoke_deployment_link(link_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment URL not found")
    await builds.purge_link_artifacts(link_id)
    await audit_service.log_action(
        action="tray.deployment_link.revoke",
        user_id=int(current_user["id"]),
        entity_type="tray_deployment_link",
        entity_id=int(link_id),
        request=request,
    )
    return {"status": "revoked"}


@router.post(
    "/api/tray/admin/deployment-links/{link_id}/rebuild",
    summary="Queue a new Windows installer build for a deployment URL (admin)",
)
async def rebuild_deployment_link(
    link_id: int,
    current_user: dict = Depends(require_super_admin),
) -> dict:
    link = await tray_repo.get_deployment_link(link_id)
    if not link or link.get("revoked_at"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment URL not found")
    build_id = await builds.queue_build(link_id)
    return {"status": "queued", "build_id": build_id}


# ---------------------------------------------------------------------------
# Windows build agent API
#
# Called by tray/build-agent/MyPortalBuildAgent.ps1 with an API key. Restrict
# the key to these paths (and the build server's IP) on the API keys page.
# ---------------------------------------------------------------------------


def _build_error(exc: Exception) -> HTTPException:
    if isinstance(exc, builds.BuildNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build not found")
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/api/tray/build-agent/jobs/claim",
    response_model=None,
    summary="Claim the next Windows installer build (build agent)",
    responses={204: {"description": "No build is waiting"}},
)
async def build_agent_claim(
    request: Request,
    _api_key: dict = Depends(require_api_key),
):
    portal_url = tray_deployment.resolve_portal_url(request)
    try:
        job = await builds.claim_next_build(portal_url)
    except ValueError as exc:
        log_error("Unable to hand out tray deployment build", error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    if job is None:
        # Header values must be latin-1, and the reasons are plain ASCII.
        reason = (await builds.idle_reason()).encode("ascii", "replace").decode("ascii")
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"X-MyPortal-Build-Status": reason},
        )
    log_info("Tray deployment build claimed", build_id=job["id"], release_tag=job["release_tag"])
    return JSONResponse(job, headers={"Cache-Control": "no-store"})


@router.put(
    "/api/tray/build-agent/jobs/{build_id}/artifacts/{kind}",
    summary="Upload a built installer as the raw request body (build agent)",
)
async def build_agent_upload(
    build_id: int,
    kind: Literal["msi", "exe"],
    request: Request,
    _api_key: dict = Depends(require_api_key),
) -> dict:
    try:
        sha256 = await builds.save_artifact(build_id, kind, request.stream())
    except (builds.BuildNotFound, builds.BuildStateError) as exc:
        raise _build_error(exc) from exc
    return {"status": "uploaded", "sha256": sha256}


@router.post(
    "/api/tray/build-agent/jobs/{build_id}/complete",
    summary="Mark a build complete after both installers are uploaded (build agent)",
)
async def build_agent_complete(
    build_id: int,
    _api_key: dict = Depends(require_api_key),
) -> dict:
    try:
        await builds.complete_build(build_id)
    except (builds.BuildNotFound, builds.BuildStateError) as exc:
        raise _build_error(exc) from exc
    return {"status": "ready"}


@router.post(
    "/api/tray/build-agent/jobs/{build_id}/fail",
    summary="Report a failed build (build agent)",
)
async def build_agent_fail(
    build_id: int,
    payload: TrayDeploymentBuildFailure,
    _api_key: dict = Depends(require_api_key),
) -> dict:
    try:
        await builds.fail_build(build_id, payload.error)
    except (builds.BuildNotFound, builds.BuildStateError) as exc:
        raise _build_error(exc) from exc
    return {"status": "failed"}
