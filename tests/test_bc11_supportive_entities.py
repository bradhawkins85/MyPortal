"""
Tests for BC11 supportive entities: contacts, vendors, and processes.

Tests cover repository functions, Pydantic schemas, and API endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ============================================================================
# BC Contact Schema Tests
# ============================================================================

def test_bc_contact_create_valid():
    """Test creating a valid BC Contact."""
    contact = BCContactCreate(
        plan_id=1,
        name="John Smith",
        role="Emergency Coordinator",
        phone="+1-555-0123",
        email="john.smith@example.com",
        notes="Primary contact for incident response",
    )
    assert contact.plan_id == 1
    assert contact.name == "John Smith"
    assert contact.role == "Emergency Coordinator"
    assert contact.phone == "+1-555-0123"
    assert contact.email == "john.smith@example.com"
    assert contact.notes == "Primary contact for incident response"


def test_bc_contact_create_minimal():
    """Test creating BC Contact with only required fields."""
    contact = BCContactCreate(
        plan_id=1,
        name="Jane Doe",
    )
    assert contact.plan_id == 1
    assert contact.name == "Jane Doe"
    assert contact.role is None
    assert contact.phone is None
    assert contact.email is None
    assert contact.notes is None


def test_bc_contact_create_invalid_name():
    """Test BC Contact validation rejects empty name."""
    with pytest.raises(ValidationError):
        BCContactCreate(
            plan_id=1,
            name="",
        )


def test_bc_contact_update_partial():
    """Test updating BC Contact with partial fields."""
    update = BCContactUpdate(
        phone="+1-555-9999",
        email="updated@example.com",
    )
    assert update.phone == "+1-555-9999"
    assert update.email == "updated@example.com"
    assert update.name is None
    assert update.role is None


# ============================================================================
# BC Vendor Schema Tests
# ============================================================================

def test_bc_vendor_create_valid():
    """Test creating a valid BC Vendor."""
    vendor = BCVendorCreate(
        plan_id=1,
        name="Cloud Services Inc",
        vendor_type="Cloud Provider",
        contact_name="Support Team",
        contact_email="support@cloudservices.com",
        contact_phone="+1-800-CLOUD",
        sla_notes="99.9% uptime, 4-hour response time for critical issues",
        contract_reference="CONTRACT-2024-001",
        criticality="critical",
    )
    assert vendor.plan_id == 1
    assert vendor.name == "Cloud Services Inc"
    assert vendor.vendor_type == "Cloud Provider"
    assert vendor.sla_notes == "99.9% uptime, 4-hour response time for critical issues"
    assert vendor.criticality == "critical"


def test_bc_vendor_create_minimal():
    """Test creating BC Vendor with only required fields."""
    vendor = BCVendorCreate(
        plan_id=1,
        name="Simple Vendor",
    )
    assert vendor.plan_id == 1
    assert vendor.name == "Simple Vendor"
    assert vendor.vendor_type is None
    assert vendor.sla_notes is None


def test_bc_vendor_create_invalid_name():
    """Test BC Vendor validation rejects empty name."""
    with pytest.raises(ValidationError):
        BCVendorCreate(
            plan_id=1,
            name="",
        )


def test_bc_vendor_update_sla():
    """Test updating BC Vendor SLA notes."""
    update = BCVendorUpdate(
        sla_notes="Updated SLA: 99.95% uptime, 2-hour critical response",
        criticality="high",
    )
    assert update.sla_notes == "Updated SLA: 99.95% uptime, 2-hour critical response"
    assert update.criticality == "high"
    assert update.name is None


# ============================================================================
# BC Process Schema Tests
# ============================================================================

def test_bc_process_create_valid():
    """Test creating a valid BC Process."""
    process = BCProcessCreate(
        plan_id=1,
        name="Customer Order Processing",
        description="Critical process for handling customer orders",
        rto_minutes=60,  # 1 hour RTO
        rpo_minutes=15,  # 15 minute RPO
        mtpd_minutes=240,  # 4 hour MTPD
        impact_rating="critical",
        dependencies_json={
            "systems": [
                {"type": "system", "id": 1, "name": "CRM System"},
                {"type": "system", "id": 2, "name": "Payment Gateway"},
            ],
            "vendors": [
                {"type": "vendor", "id": 3, "name": "Cloud Provider"},
            ],
            "sites": [
                {"type": "site", "id": 4, "name": "Primary Data Center"},
            ],
        },
    )
    assert process.plan_id == 1
    assert process.name == "Customer Order Processing"
    assert process.rto_minutes == 60
    assert process.rpo_minutes == 15
    assert process.mtpd_minutes == 240
    assert process.impact_rating == "critical"
    assert process.dependencies_json is not None
    assert "systems" in process.dependencies_json
    assert len(process.dependencies_json["systems"]) == 2


def test_bc_process_create_minimal():
    """Test creating BC Process with only required fields."""
    process = BCProcessCreate(
        plan_id=1,
        name="Minimal Process",
    )
    assert process.plan_id == 1
    assert process.name == "Minimal Process"
    assert process.rto_minutes is None
    assert process.rpo_minutes is None
    assert process.dependencies_json is None


def test_bc_process_create_invalid_rto():
    """Test BC Process validation rejects negative RTO."""
    with pytest.raises(ValidationError):
        BCProcessCreate(
            plan_id=1,
            name="Invalid Process",
            rto_minutes=-10,
        )


def test_bc_process_update_recovery_objectives():
    """Test updating BC Process recovery objectives."""
    update = BCProcessUpdate(
        rto_minutes=30,
        rpo_minutes=5,
        impact_rating="high",
    )
    assert update.rto_minutes == 30
    assert update.rpo_minutes == 5
    assert update.impact_rating == "high"
    assert update.name is None


def test_bc_process_dependencies_structure():
    """Test BC Process dependencies JSON structure."""
    dependencies = {
        "systems": [
            {"type": "system", "id": 123, "name": "ERP System", "criticality": "high"},
        ],
        "sites": [
            {"type": "site", "id": 456, "name": "Backup Site", "location": "Sydney"},
        ],
        "vendors": [
            {"type": "vendor", "id": 789, "name": "ISP", "sla": "4-hour response"},
        ],
    }
    
    process = BCProcessCreate(
        plan_id=1,
        name="Test Process",
        dependencies_json=dependencies,
    )
    
    assert process.dependencies_json == dependencies
    assert process.dependencies_json["systems"][0]["type"] == "system"
    assert process.dependencies_json["sites"][0]["location"] == "Sydney"
    assert process.dependencies_json["vendors"][0]["sla"] == "4-hour response"


# ============================================================================
# Repository Function Tests
# ============================================================================

@pytest.mark.anyio
async def test_create_contact(monkeypatch):
    """Test creating a contact via repository."""
    contact_data = {
        "id": 1,
        "plan_id": 1,
        "name": "Test Contact",
        "role": "Manager",
        "phone": "+1-555-0000",
        "email": "test@example.com",
        "notes": "Test notes",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    async def mock_execute(query: str, params: tuple) -> int:
        return contact_data["id"]
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return contact_data
    
    from app.repositories import bc3 as bc_repo
    
    monkeypatch.setattr("app.repositories.bc3.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.bc3.db.fetch_one", mock_fetch_one)
    
    result = await bc_repo.create_contact(
        plan_id=1,
        name="Test Contact",
        role="Manager",
        phone="+1-555-0000",
        email="test@example.com",
        notes="Test notes",
    )
    
    assert result["id"] == 1
    assert result["name"] == "Test Contact"
    assert result["role"] == "Manager"


@pytest.mark.anyio
async def test_list_contacts_by_plan(monkeypatch):
    """Test listing contacts for a plan."""
    contacts = [
        {
            "id": 1,
            "plan_id": 1,
            "name": "Contact 1",
            "role": "Manager",
            "phone": None,
            "email": "contact1@example.com",
            "notes": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "id": 2,
            "plan_id": 1,
            "name": "Contact 2",
            "role": "Coordinator",
            "phone": "+1-555-0001",
            "email": "contact2@example.com",
            "notes": "Backup contact",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    ]
    
    async def mock_fetch_all(query: str, params: tuple) -> list[dict[str, Any]]:
        return contacts
    
    from app.repositories import bc3 as bc_repo
    
    monkeypatch.setattr("app.repositories.bc3.db.fetch_all", mock_fetch_all)
    
    result = await bc_repo.list_contacts_by_plan(1)
    
    assert len(result) == 2
    assert result[0]["name"] == "Contact 1"
    assert result[1]["name"] == "Contact 2"


@pytest.mark.anyio
async def test_create_vendor(monkeypatch):
    """Test creating a vendor via repository."""
    vendor_data = {
        "id": 1,
        "plan_id": 1,
        "name": "Test Vendor",
        "vendor_type": "IT Service Provider",
        "contact_name": "Vendor Contact",
        "contact_email": "vendor@example.com",
        "contact_phone": "+1-800-VENDOR",
        "sla_notes": "24/7 support",
        "contract_reference": "CONT-001",
        "criticality": "high",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    async def mock_execute(query: str, params: tuple) -> int:
        return vendor_data["id"]
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return vendor_data
    
    from app.repositories import bc3 as bc_repo
    
    monkeypatch.setattr("app.repositories.bc3.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.bc3.db.fetch_one", mock_fetch_one)
    
    result = await bc_repo.create_vendor(
        plan_id=1,
        name="Test Vendor",
        vendor_type="IT Service Provider",
        sla_notes="24/7 support",
        criticality="high",
    )
    
    assert result["id"] == 1
    assert result["name"] == "Test Vendor"
    assert result["sla_notes"] == "24/7 support"


@pytest.mark.anyio
async def test_create_process_with_dependencies(monkeypatch):
    """Test creating a process with dependencies via repository."""
    dependencies = {
        "systems": [{"type": "system", "id": 1, "name": "CRM"}],
        "vendors": [{"type": "vendor", "id": 2, "name": "AWS"}],
    }
    
    process_data = {
        "id": 1,
        "plan_id": 1,
        "name": "Order Processing",
        "description": "Critical order handling process",
        "rto_minutes": 60,
        "rpo_minutes": 15,
        "mtpd_minutes": 240,
        "impact_rating": "critical",
        "dependencies_json": dependencies,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    async def mock_execute(query: str, params: tuple) -> int:
        return process_data["id"]
    
    async def mock_fetch_one(query: str, params: tuple) -> dict[str, Any]:
        return process_data
    
    from app.repositories import bc3 as bc_repo
    
    monkeypatch.setattr("app.repositories.bc3.db.execute", mock_execute)
    monkeypatch.setattr("app.repositories.bc3.db.fetch_one", mock_fetch_one)
    
    result = await bc_repo.create_process(
        plan_id=1,
        name="Order Processing",
        description="Critical order handling process",
        rto_minutes=60,
        rpo_minutes=15,
        mtpd_minutes=240,
        impact_rating="critical",
        dependencies_json=dependencies,
    )
    
    assert result["id"] == 1
    assert result["name"] == "Order Processing"
    assert result["rto_minutes"] == 60
    assert result["dependencies_json"] is not None
    assert "systems" in result["dependencies_json"]
