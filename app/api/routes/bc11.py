"""
BC11 Business Continuity API endpoints for supportive entities.

RESTful API for contacts, vendors, and processes within business continuity plans.
Implements CRUD operations with RBAC integration.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.bc_rbac import require_bc_editor, require_bc_viewer
from app.repositories import bc3 as bc_repo
from app.schemas.bc3_models import (
    BCContactCreate,
    BCContactResponse,
    BCContactUpdate,
    BCProcessCreate,
    BCProcessResponse,
    BCProcessUpdate,
    BCVendorCreate,
    BCVendorResponse,
    BCVendorUpdate,
)

router = APIRouter(prefix="/api/bc/plans", tags=["Business Continuity - Supportive Entities (BC11)"])


# ============================================================================
# BC Contact Endpoints
# ============================================================================

@router.get("/{plan_id}/contacts", response_model=list[BCContactResponse])
async def list_contacts(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCContactResponse]:
    """
    List all contacts for a business continuity plan.
    
    Returns emergency contacts and key personnel associated with the plan.
    Contacts can be imported from an address book or stored locally.
    
    Requires: BC Viewer role or higher
    """
    # Verify plan exists
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
    
    contacts = await bc_repo.list_contacts_by_plan(plan_id)
    return [BCContactResponse(**contact) for contact in contacts]


@router.post("/{plan_id}/contacts", response_model=BCContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    plan_id: int,
    contact_data: BCContactCreate,
    current_user: dict = Depends(require_bc_editor),
) -> BCContactResponse:
    """
    Create a new contact for a business continuity plan.
    
    Contacts include name, role, email, phone, and notes.
    Can be imported from an address book or created locally.
    
    Requires: BC Editor role or higher
    """
    # Verify plan exists
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
    
    # Ensure plan_id matches
    if contact_data.plan_id != plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contact plan_id must match URL plan_id"
        )
    
    contact = await bc_repo.create_contact(
        plan_id=contact_data.plan_id,
        name=contact_data.name,
        role=contact_data.role,
        phone=contact_data.phone,
        email=contact_data.email,
        notes=contact_data.notes,
    )
    
    return BCContactResponse(**contact)


@router.get("/{plan_id}/contacts/{contact_id}", response_model=BCContactResponse)
async def get_contact(
    plan_id: int,
    contact_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCContactResponse:
    """
    Get a specific contact by ID.
    
    Requires: BC Viewer role or higher
    """
    contact = await bc_repo.get_contact_by_id(contact_id)
    if not contact or contact["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found"
        )
    
    return BCContactResponse(**contact)


@router.put("/{plan_id}/contacts/{contact_id}", response_model=BCContactResponse)
async def update_contact(
    plan_id: int,
    contact_id: int,
    contact_data: BCContactUpdate,
    current_user: dict = Depends(require_bc_editor),
) -> BCContactResponse:
    """
    Update a contact's information.
    
    All fields are optional - only provided fields will be updated.
    
    Requires: BC Editor role or higher
    """
    # Verify contact exists and belongs to plan
    existing = await bc_repo.get_contact_by_id(contact_id)
    if not existing or existing["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found"
        )
    
    contact = await bc_repo.update_contact(
        contact_id=contact_id,
        name=contact_data.name,
        role=contact_data.role,
        phone=contact_data.phone,
        email=contact_data.email,
        notes=contact_data.notes,
    )
    
    return BCContactResponse(**contact)


@router.delete("/{plan_id}/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    plan_id: int,
    contact_id: int,
    current_user: dict = Depends(require_bc_editor),
) -> None:
    """
    Delete a contact.
    
    Requires: BC Editor role or higher
    """
    # Verify contact exists and belongs to plan
    existing = await bc_repo.get_contact_by_id(contact_id)
    if not existing or existing["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found"
        )
    
    await bc_repo.delete_contact(contact_id)


# ============================================================================
# BC Vendor Endpoints
# ============================================================================

@router.get("/{plan_id}/vendors", response_model=list[BCVendorResponse])
async def list_vendors(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCVendorResponse]:
    """
    List all vendors for a business continuity plan.
    
    Returns service providers, suppliers, and other external parties
    with SLA information and contact details.
    
    Requires: BC Viewer role or higher
    """
    # Verify plan exists
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
    
    vendors = await bc_repo.list_vendors_by_plan(plan_id)
    return [BCVendorResponse(**vendor) for vendor in vendors]


@router.post("/{plan_id}/vendors", response_model=BCVendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    plan_id: int,
    vendor_data: BCVendorCreate,
    current_user: dict = Depends(require_bc_editor),
) -> BCVendorResponse:
    """
    Create a new vendor for a business continuity plan.
    
    Vendors track service providers, suppliers, or other external parties.
    Include SLA notes, contract references, and criticality ratings.
    
    Requires: BC Editor role or higher
    """
    # Verify plan exists
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
    
    # Ensure plan_id matches
    if vendor_data.plan_id != plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vendor plan_id must match URL plan_id"
        )
    
    vendor = await bc_repo.create_vendor(
        plan_id=vendor_data.plan_id,
        name=vendor_data.name,
        vendor_type=vendor_data.vendor_type,
        contact_name=vendor_data.contact_name,
        contact_email=vendor_data.contact_email,
        contact_phone=vendor_data.contact_phone,
        sla_notes=vendor_data.sla_notes,
        contract_reference=vendor_data.contract_reference,
        criticality=vendor_data.criticality,
    )
    
    return BCVendorResponse(**vendor)


@router.get("/{plan_id}/vendors/{vendor_id}", response_model=BCVendorResponse)
async def get_vendor(
    plan_id: int,
    vendor_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCVendorResponse:
    """
    Get a specific vendor by ID.
    
    Requires: BC Viewer role or higher
    """
    vendor = await bc_repo.get_vendor_by_id(vendor_id)
    if not vendor or vendor["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor not found"
        )
    
    return BCVendorResponse(**vendor)


@router.put("/{plan_id}/vendors/{vendor_id}", response_model=BCVendorResponse)
async def update_vendor(
    plan_id: int,
    vendor_id: int,
    vendor_data: BCVendorUpdate,
    current_user: dict = Depends(require_bc_editor),
) -> BCVendorResponse:
    """
    Update a vendor's information.
    
    All fields are optional - only provided fields will be updated.
    
    Requires: BC Editor role or higher
    """
    # Verify vendor exists and belongs to plan
    existing = await bc_repo.get_vendor_by_id(vendor_id)
    if not existing or existing["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor not found"
        )
    
    vendor = await bc_repo.update_vendor(
        vendor_id=vendor_id,
        name=vendor_data.name,
        vendor_type=vendor_data.vendor_type,
        contact_name=vendor_data.contact_name,
        contact_email=vendor_data.contact_email,
        contact_phone=vendor_data.contact_phone,
        sla_notes=vendor_data.sla_notes,
        contract_reference=vendor_data.contract_reference,
        criticality=vendor_data.criticality,
    )
    
    return BCVendorResponse(**vendor)


@router.delete("/{plan_id}/vendors/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vendor(
    plan_id: int,
    vendor_id: int,
    current_user: dict = Depends(require_bc_editor),
) -> None:
    """
    Delete a vendor.
    
    Requires: BC Editor role or higher
    """
    # Verify vendor exists and belongs to plan
    existing = await bc_repo.get_vendor_by_id(vendor_id)
    if not existing or existing["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor not found"
        )
    
    await bc_repo.delete_vendor(vendor_id)


# ============================================================================
# BC Process Endpoints
# ============================================================================

@router.get("/{plan_id}/processes", response_model=list[BCProcessResponse])
async def list_processes(
    plan_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> list[BCProcessResponse]:
    """
    List all critical business processes for a plan.
    
    Returns processes with recovery objectives (RTO, RPO, MTPD) and dependencies.
    Dependencies represent systems, sites, vendors as JSON arrays with type and reference.
    
    Requires: BC Viewer role or higher
    """
    # Verify plan exists
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
    
    processes = await bc_repo.list_processes_by_plan(plan_id)
    return [BCProcessResponse(**process) for process in processes]


@router.post("/{plan_id}/processes", response_model=BCProcessResponse, status_code=status.HTTP_201_CREATED)
async def create_process(
    plan_id: int,
    process_data: BCProcessCreate,
    current_user: dict = Depends(require_bc_editor),
) -> BCProcessResponse:
    """
    Create a new critical business process for a plan.
    
    Processes track recovery objectives and dependencies.
    Dependencies should be structured as JSON arrays with type and reference:
    ```json
    {
      "systems": [{"type": "system", "id": 123, "name": "CRM System"}],
      "sites": [{"type": "site", "id": 45, "name": "Primary Data Center"}],
      "vendors": [{"type": "vendor", "id": 67, "name": "Cloud Provider"}]
    }
    ```
    
    Requires: BC Editor role or higher
    """
    # Verify plan exists
    plan = await bc_repo.get_plan_by_id(plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
    
    # Ensure plan_id matches
    if process_data.plan_id != plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Process plan_id must match URL plan_id"
        )
    
    process = await bc_repo.create_process(
        plan_id=process_data.plan_id,
        name=process_data.name,
        description=process_data.description,
        rto_minutes=process_data.rto_minutes,
        rpo_minutes=process_data.rpo_minutes,
        mtpd_minutes=process_data.mtpd_minutes,
        impact_rating=process_data.impact_rating,
        dependencies_json=process_data.dependencies_json,
    )
    
    return BCProcessResponse(**process)


@router.get("/{plan_id}/processes/{process_id}", response_model=BCProcessResponse)
async def get_process(
    plan_id: int,
    process_id: int,
    current_user: dict = Depends(require_bc_viewer),
) -> BCProcessResponse:
    """
    Get a specific process by ID.
    
    Requires: BC Viewer role or higher
    """
    process = await bc_repo.get_process_by_id(process_id)
    if not process or process["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found"
        )
    
    return BCProcessResponse(**process)


@router.put("/{plan_id}/processes/{process_id}", response_model=BCProcessResponse)
async def update_process(
    plan_id: int,
    process_id: int,
    process_data: BCProcessUpdate,
    current_user: dict = Depends(require_bc_editor),
) -> BCProcessResponse:
    """
    Update a process's information.
    
    All fields are optional - only provided fields will be updated.
    
    Requires: BC Editor role or higher
    """
    # Verify process exists and belongs to plan
    existing = await bc_repo.get_process_by_id(process_id)
    if not existing or existing["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found"
        )
    
    process = await bc_repo.update_process(
        process_id=process_id,
        name=process_data.name,
        description=process_data.description,
        rto_minutes=process_data.rto_minutes,
        rpo_minutes=process_data.rpo_minutes,
        mtpd_minutes=process_data.mtpd_minutes,
        impact_rating=process_data.impact_rating,
        dependencies_json=process_data.dependencies_json,
    )
    
    return BCProcessResponse(**process)


@router.delete("/{plan_id}/processes/{process_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_process(
    plan_id: int,
    process_id: int,
    current_user: dict = Depends(require_bc_editor),
) -> None:
    """
    Delete a process.
    
    Requires: BC Editor role or higher
    """
    # Verify process exists and belongs to plan
    existing = await bc_repo.get_process_by_id(process_id)
    if not existing or existing["plan_id"] != plan_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Process not found"
        )
    
    await bc_repo.delete_process(process_id)
