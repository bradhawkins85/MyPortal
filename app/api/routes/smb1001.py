"""SMB1001 compliance API."""

from __future__ import annotations

from datetime import date
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.api.routes.essential8 import _sanitize_requirement_evidence_filename as _sanitize_evidence_filename
from app.repositories import smb1001 as smb1001_repo
from app.repositories import user_companies as user_company_repo
from app.repositories import users as users_repo
from app.services import audit as audit_service

router = APIRouter(prefix="/api/smb1001", tags=["SMB1001 Compliance"])
_MAX_EVIDENCE_SIZE_BYTES = 15 * 1024 * 1024
_EVIDENCE_SUBDIR = ("compliance", "smb1001")

StatusLiteral = Literal["not_started", "in_progress", "compliant", "non_compliant", "not_applicable"]


class SMB1001ControlComplianceUpdate(BaseModel):
    status: Optional[StatusLiteral] = None
    evidence: Optional[str] = None
    notes: Optional[str] = None
    owner_user_id: Optional[int] = None
    last_reviewed_date: Optional[date] = None
    target_compliance_date: Optional[date] = None


class SMB1001TargetTierUpdate(BaseModel):
    target_tier: int = Field(ge=1, le=smb1001_repo.MAX_TIER)


async def _assert_company_compliance_access(user: dict, company_id: int, *, write: bool = False) -> None:
    if user.get("is_super_admin"):
        return
    if user.get("company_id") != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access compliance for your own company",
        )
    membership = None
    if user.get("id") is not None:
        membership = await user_company_repo.get_user_company(user["id"], company_id)
    if not membership or not membership.get("can_view_compliance"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to SMB1001 compliance for this company",
        )
    if write and not membership.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required to manage SMB1001 compliance",
        )


async def _validate_owner(company_id: int, owner_user_id: int | None) -> None:
    if owner_user_id is None:
        return
    owner = await users_repo.get_user_by_id(owner_user_id)
    if not owner or int(owner.get("company_id") or 0) != company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner must be a valid company member",
        )


@router.get("/tiers", response_model=list[dict])
async def list_tiers(
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List the five SMB1001 tiers (Bronze to Diamond)."""
    return await smb1001_repo.list_tiers()


@router.get("/controls", response_model=list[dict])
async def list_controls(
    tier_level: Optional[int] = None,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    """List SMB1001 controls, optionally for a single tier."""
    return await smb1001_repo.list_controls(tier_level=tier_level)


@router.get("/companies/{company_id}/overview", response_model=dict)
async def get_company_overview(
    company_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Controls with the company's status, per-tier progress and the tier achieved."""
    await _assert_company_compliance_access(user, company_id)
    await smb1001_repo.ensure_company_profile(company_id, user_id=user.get("id"))
    return await smb1001_repo.get_company_overview(company_id)


@router.patch("/companies/{company_id}/controls/{control_id}/compliance", response_model=dict)
async def update_control_compliance(
    company_id: int,
    control_id: int,
    payload: SMB1001ControlComplianceUpdate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Create or update the company's status, evidence and notes for one control."""
    await _assert_company_compliance_access(user, company_id, write=True)
    control = await smb1001_repo.get_control(control_id)
    if not control:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No changes supplied")
    await _validate_owner(company_id, updates.get("owner_user_id"))
    before = await smb1001_repo.get_company_control_compliance(company_id, control_id)
    try:
        record = await smb1001_repo.save_company_control_compliance(
            company_id,
            control_id,
            user_id=user.get("id"),
            change_summary=f"{control['code']} updated.",
            **updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await audit_service.record(
        action="smb1001.control.update",
        request=request,
        user_id=user.get("id"),
        entity_type="smb1001_control_compliance",
        entity_id=record.get("id"),
        before=before,
        after=record,
        metadata={"company_id": company_id, "control_id": control_id, "control_code": control["code"]},
    )
    return record


@router.get("/companies/{company_id}/controls/{control_id}/audit", response_model=list[dict])
async def list_control_audit(
    company_id: int,
    control_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _assert_company_compliance_access(user, company_id)
    return await smb1001_repo.list_control_audit(company_id, control_id)


@router.put("/companies/{company_id}/target-tier", response_model=dict)
async def set_target_tier(
    company_id: int,
    payload: SMB1001TargetTierUpdate,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Set the SMB1001 tier the company is working towards."""
    await _assert_company_compliance_access(user, company_id, write=True)
    before = await smb1001_repo.get_profile(company_id)
    profile = await smb1001_repo.set_target_tier(company_id, payload.target_tier)
    await audit_service.record(
        action="smb1001.target_tier.update",
        request=request,
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        before={"target_tier": (before or {}).get("target_tier")},
        after={"target_tier": profile.get("target_tier")},
    )
    return profile


@router.post("/companies/{company_id}/import-essential8", response_model=dict)
async def import_essential8(
    company_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Convert Essential 8 progress into SMB1001 statuses for controls not yet started."""
    await _assert_company_compliance_access(user, company_id, write=True)
    result = await smb1001_repo.import_essential8_progress(company_id, user_id=user.get("id"))
    await audit_service.record(
        action="smb1001.essential8_import",
        request=request,
        user_id=user.get("id"),
        entity_type="company",
        entity_id=company_id,
        metadata={"updated_count": result.get("updated_count", 0)},
    )
    return result


# ---------------------------------------------------------------------------
# Evidence files
# ---------------------------------------------------------------------------

# Content types served back as-is; anything else (HTML, SVG, scripts…) is sent
# as an opaque download so uploaded files cannot run in the portal origin.
_SAFE_DOWNLOAD_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "text/plain",
        "text/csv",
        "application/zip",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)


def _private_uploads_root() -> Path:
    main_module = import_module("app.main")

    return main_module._private_uploads_path


def _evidence_upload_dir() -> Path:
    return _private_uploads_root().joinpath(*_EVIDENCE_SUBDIR)


def _open_evidence_storage_file() -> tuple[Path, Path, BinaryIO]:
    storage_dir = _evidence_upload_dir()
    if storage_dir.exists() and storage_dir.is_symlink():
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to allocate evidence storage path")
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_root = storage_dir.resolve(strict=True)
    for _ in range(5):
        # The on-disk name contains no request-derived data; the sanitised
        # original name is stored as metadata only.
        storage_path = storage_root / f"{uuid4().hex}.evidence"
        try:
            return storage_root, storage_path, storage_path.open("xb")
        except FileExistsError:
            continue
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to allocate evidence storage path")


def _resolve_evidence_file(relative_path: str) -> Path | None:
    """Resolve a stored evidence path, refusing anything outside the SMB1001 evidence folder."""

    root = _evidence_upload_dir().resolve()
    candidate = _private_uploads_root().joinpath(*Path(relative_path).parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


@router.get("/companies/{company_id}/controls/{control_id}/evidence", response_model=list[dict])
async def list_control_evidence(
    company_id: int,
    control_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _assert_company_compliance_access(user, company_id)
    return (await smb1001_repo.list_evidence_map(company_id, control_id=control_id)).get(control_id, [])


@router.post(
    "/companies/{company_id}/controls/{control_id}/evidence",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
async def upload_control_evidence(
    company_id: int,
    control_id: int,
    request: Request,
    title: str = Form(...),
    description: str | None = Form(default=None),
    evidence_file: UploadFile = File(...),
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Upload a new evidence version (max 15 MB) for a control."""
    await _assert_company_compliance_access(user, company_id, write=True)
    control = await smb1001_repo.get_control(control_id)
    if not control:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")
    clean_title = title.strip()[:255]
    if not clean_title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Evidence title is required")
    safe_name = _sanitize_evidence_filename(evidence_file.filename)
    total_size = 0
    storage_root: Path | None = None
    storage_path: Path | None = None
    try:
        storage_root, storage_path, handle = _open_evidence_storage_file()
        with handle:
            while True:
                chunk = await evidence_file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > _MAX_EVIDENCE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Uploaded evidence file exceeds the 15 MB limit",
                    )
                handle.write(chunk)
    except Exception:
        if storage_root is not None and storage_path is not None and storage_root in storage_path.parents:
            storage_path.unlink(missing_ok=True)
        raise
    finally:
        await evidence_file.close()
    if total_size == 0:
        storage_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Evidence file is empty")

    record = await smb1001_repo.add_evidence(
        company_id=company_id,
        control_id=control_id,
        title=clean_title,
        description=(description or "").strip() or None,
        file_name=safe_name,
        content_type=(evidence_file.content_type or "")[:255] or None,
        file_path="/".join((*_EVIDENCE_SUBDIR, storage_path.name)),
        file_size_bytes=total_size,
        uploaded_by=user.get("id"),
    )
    await audit_service.record(
        action="smb1001.evidence.upload",
        request=request,
        user_id=user.get("id"),
        entity_type="smb1001_evidence",
        entity_id=record.get("id"),
        after={"title": clean_title, "file_name": safe_name, "version_number": record.get("version_number")},
        metadata={"company_id": company_id, "control_id": control_id, "control_code": control["code"]},
    )
    return record


@router.get("/companies/{company_id}/evidence/{evidence_id}/download")
async def download_evidence(
    company_id: int,
    evidence_id: int,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    """Download an evidence file; only users with access to the company's compliance can."""
    await _assert_company_compliance_access(user, company_id)
    evidence = await smb1001_repo.get_evidence(company_id, evidence_id)
    path = _resolve_evidence_file(str(evidence["file_path"])) if evidence else None
    if not evidence or path is None or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found")
    content_type = str(evidence.get("content_type") or "").split(";")[0].strip().lower()
    return FileResponse(
        path,
        media_type=content_type if content_type in _SAFE_DOWNLOAD_TYPES else "application/octet-stream",
        filename=str(evidence.get("file_name") or "evidence"),
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.delete("/companies/{company_id}/evidence/{evidence_id}", response_model=dict)
async def delete_evidence(
    company_id: int,
    evidence_id: int,
    request: Request,
    _: None = Depends(require_database),
    user: dict = Depends(get_current_user),
):
    await _assert_company_compliance_access(user, company_id, write=True)
    deleted = await smb1001_repo.delete_evidence(company_id, evidence_id, user_id=user.get("id"))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence not found")
    path = _resolve_evidence_file(str(deleted.get("file_path") or ""))
    if path is not None:
        path.unlink(missing_ok=True)
    await audit_service.record(
        action="smb1001.evidence.delete",
        request=request,
        user_id=user.get("id"),
        entity_type="smb1001_evidence",
        entity_id=evidence_id,
        before={"title": deleted.get("title"), "file_name": deleted.get("file_name"), "version_number": deleted.get("version_number")},
        metadata={"company_id": company_id, "control_id": deleted.get("control_id")},
    )
    return {"deleted": True, "id": evidence_id}


__all__ = ["router"]
