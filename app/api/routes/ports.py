from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.api.dependencies.database import require_database
from app.core.config import get_templates_config
from app.repositories import port_documents as port_documents_repo
from app.repositories import port_pricing as port_pricing_repo
from app.repositories import ports as ports_repo
from app.schemas.port_documents import PortDocumentResponse
from app.schemas.port_pricing import (
    PortPricingCreate,
    PortPricingResponse,
    PricingReviewAction,
)
from app.schemas.ports import PortCreate, PortResponse, PortUpdate
from app.services.file_storage import delete_stored_file, store_port_document
from app.services.notifications import emit_notification

router = APIRouter(prefix="/ports", tags=["Ports"])

_templates_config = get_templates_config()
_uploads_root = _templates_config.static_path / "uploads"


@router.get("", response_model=list[PortResponse])
async def list_ports(
    search: str | None = Query(default=None, description="Search by name, code, or country"),
    country: str | None = Query(default=None, description="Filter ports by country"),
    active_only: bool = Query(default=True, description="Include only active ports"),
    order_by: str | None = Query(default="name"),
    direction: str | None = Query(default="asc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    rows = await ports_repo.list_ports(
        search=search,
        country=country,
        active_only=active_only,
        order_by=order_by,
        direction=direction,
        limit=limit,
        offset=offset,
    )
    return rows


@router.post("", response_model=PortResponse, status_code=status.HTTP_201_CREATED)
async def create_port(
    payload: PortCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    existing = await ports_repo.get_port_by_code(payload.code)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Port code already in use")
    created = await ports_repo.create_port(**payload.model_dump())
    await emit_notification(
        event_type="port.created",
        message=f"Port {created['name']} ({created['code']}) created",
        metadata={"port_id": created["id"]},
    )
    return created


@router.get("/{port_id}", response_model=PortResponse)
async def get_port(
    port_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    return port


@router.patch("/{port_id}", response_model=PortResponse)
async def update_port(
    port_id: int,
    payload: PortUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    data = payload.model_dump(exclude_unset=True)
    new_code = data.get("code")
    if new_code and new_code != port["code"]:
        existing = await ports_repo.get_port_by_code(new_code)
        if existing and existing["id"] != port_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Port code already in use")
    updated = await ports_repo.update_port(port_id, **data)
    await emit_notification(
        event_type="port.updated",
        message=f"Port {updated['name']} ({updated['code']}) updated",
        metadata={"port_id": port_id},
    )
    return updated


@router.delete("/{port_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_port(
    port_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    await ports_repo.delete_port(port_id)
    await emit_notification(
        event_type="port.deleted",
        message=f"Port {port['name']} ({port['code']}) deleted",
        metadata={"port_id": port_id},
    )
    return None


@router.get("/{port_id}/documents", response_model=list[PortDocumentResponse])
async def list_port_documents(
    port_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    documents = await port_documents_repo.list_documents(port_id)
    return documents


@router.post(
    "/{port_id}/documents",
    response_model=PortDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_port_document(
    port_id: int,
    file: UploadFile = File(...),
    description: str | None = Form(default=None, max_length=255),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")

    relative_path, size, original_name = await store_port_document(
        port_id=port_id,
        upload=file,
        uploads_root=_uploads_root,
    )
    document = await port_documents_repo.create_document(
        port_id=port_id,
        file_name=original_name,
        storage_path=relative_path,
        content_type=file.content_type,
        file_size=size,
        description=description,
        uploaded_by=current_user.get("id"),
    )
    await emit_notification(
        event_type="port.document_uploaded",
        message=f"Document {original_name} uploaded for {port['name']}",
        metadata={"port_id": port_id, "document_id": document["id"], "path": relative_path},
    )
    return document


@router.delete(
    "/{port_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_port_document(
    port_id: int,
    document_id: int,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    document = await port_documents_repo.get_document(document_id)
    if not document or document["port_id"] != port_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await port_documents_repo.delete_document(document_id)
    try:
        delete_stored_file(document["storage_path"], _uploads_root)
    except HTTPException:
        # If the path is invalid we still consider the DB delete authoritative.
        pass
    await emit_notification(
        event_type="port.document_deleted",
        message=f"Document {document['file_name']} removed from {port['name']}",
        metadata={"port_id": port_id, "document_id": document_id},
    )
    return None


@router.get("/{port_id}/pricing", response_model=list[PortPricingResponse])
async def list_pricing_versions(
    port_id: int,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_database),
    __: dict = Depends(get_current_user),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    valid_statuses = {"draft", "pending_review", "approved", "rejected"}
    if status_filter and status_filter not in valid_statuses:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status filter")
    records = await port_pricing_repo.list_pricing_versions(
        port_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return records


@router.post(
    "/{port_id}/pricing",
    response_model=PortPricingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pricing_version(
    port_id: int,
    payload: PortPricingCreate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    port = await ports_repo.get_port_by_id(port_id)
    if not port:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Port not found")
    data = payload.model_dump()
    data["currency"] = data.get("currency", "USD").upper()
    data.update(
        {
            "port_id": port_id,
            "status": "draft",
            "submitted_by": None,
            "approved_by": None,
            "submitted_at": None,
            "approved_at": None,
            "rejection_reason": None,
        }
    )
    created = await port_pricing_repo.create_pricing_version(**data)
    await emit_notification(
        event_type="port.pricing.created",
        message=f"Pricing version {created['version_label']} drafted for {port['name']}",
        metadata={"port_id": port_id, "pricing_id": created["id"]},
    )
    return created


@router.post(
    "/{port_id}/pricing/{pricing_id}/submit",
    response_model=PortPricingResponse,
)
async def submit_pricing_version(
    port_id: int,
    pricing_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    pricing = await port_pricing_repo.get_pricing_version(pricing_id)
    if not pricing or pricing["port_id"] != port_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pricing version not found")
    if pricing["status"] not in {"draft", "rejected"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only draft or rejected pricing can be submitted")
    updated = await port_pricing_repo.update_status(
        pricing_id,
        status="pending_review",
        submitted_by=current_user.get("id"),
        timestamp=datetime.now(timezone.utc),
    )
    await emit_notification(
        event_type="port.pricing.submitted",
        message=f"Pricing version {pricing['version_label']} submitted for review",
        metadata={"port_id": port_id, "pricing_id": pricing_id},
    )
    return updated


@router.post(
    "/{port_id}/pricing/{pricing_id}/approve",
    response_model=PortPricingResponse,
)
async def approve_pricing_version(
    port_id: int,
    pricing_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    pricing = await port_pricing_repo.get_pricing_version(pricing_id)
    if not pricing or pricing["port_id"] != port_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pricing version not found")
    if pricing["status"] != "pending_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only pending pricing can be approved")
    updated = await port_pricing_repo.update_status(
        pricing_id,
        status="approved",
        approved_by=current_user.get("id"),
        timestamp=datetime.now(timezone.utc),
    )
    await emit_notification(
        event_type="port.pricing.approved",
        message=f"Pricing version {pricing['version_label']} approved",
        metadata={"port_id": port_id, "pricing_id": pricing_id},
        user_id=pricing.get("submitted_by"),
    )
    return updated


@router.post(
    "/{port_id}/pricing/{pricing_id}/reject",
    response_model=PortPricingResponse,
)
async def reject_pricing_version(
    port_id: int,
    pricing_id: int,
    payload: PricingReviewAction,
    _: None = Depends(require_database),
    current_user: dict = Depends(require_super_admin),
):
    pricing = await port_pricing_repo.get_pricing_version(pricing_id)
    if not pricing or pricing["port_id"] != port_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pricing version not found")
    if pricing["status"] != "pending_review":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only pending pricing can be rejected")
    updated = await port_pricing_repo.update_status(
        pricing_id,
        status="rejected",
        rejection_reason=payload.rejection_reason,
        submitted_by=pricing.get("submitted_by"),
    )
    await emit_notification(
        event_type="port.pricing.rejected",
        message=f"Pricing version {pricing['version_label']} rejected",
        metadata={"port_id": port_id, "pricing_id": pricing_id, "reason": payload.rejection_reason},
        user_id=pricing.get("submitted_by"),
    )
    return updated
