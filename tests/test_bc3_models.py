"""
Tests for BC3 Business Continuity Planning data models.

Tests cover SQLAlchemy 2.0 async models, Pydantic schemas, and basic validations.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.bc3_models import (
    BCAckCreate,
    BCAckResponse,
    BCAttachmentCreate,
    BCAttachmentResponse,
    BCAuditCreate,
    BCAuditResponse,
    BCChangeLogMapCreate,
    BCChangeLogMapResponse,
    BCContactCreate,
    BCContactResponse,
    BCPlanCreate,
    BCPlanResponse,
    BCPlanStatus,
    BCPlanUpdate,
    BCPlanVersionCreate,
    BCPlanVersionResponse,
    BCPlanVersionStatus,
    BCProcessCreate,
    BCProcessResponse,
    BCReviewCreate,
    BCReviewResponse,
    BCReviewStatus,
    BCRiskCreate,
    BCRiskResponse,
    BCSectionDefinitionCreate,
    BCSectionDefinitionResponse,
    BCTemplateCreate,
    BCTemplateResponse,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# BC Plan Schema Tests
def test_bc_plan_create_valid():
    """Test creating a valid BC Plan."""
    plan = BCPlanCreate(
        title="Disaster Recovery Plan",
        status=BCPlanStatus.DRAFT,
        owner_user_id=1,
    )
    assert plan.title == "Disaster Recovery Plan"
    assert plan.status == BCPlanStatus.DRAFT
    assert plan.owner_user_id == 1
    assert plan.org_id is None


def test_bc_plan_create_with_org():
    """Test creating a BC Plan with organization ID."""
    plan = BCPlanCreate(
        org_id=42,
        title="Multi-tenant Plan",
        status=BCPlanStatus.DRAFT,
        owner_user_id=1,
    )
    assert plan.org_id == 42
    assert plan.title == "Multi-tenant Plan"


def test_bc_plan_create_invalid_title():
    """Test BC Plan validation rejects empty title."""
    with pytest.raises(ValidationError):
        BCPlanCreate(
            title="",
            status=BCPlanStatus.DRAFT,
            owner_user_id=1,
        )


def test_bc_plan_update_partial():
    """Test partial update of BC Plan."""
    update = BCPlanUpdate(title="Updated Title")
    assert update.title == "Updated Title"
    assert update.status is None
    assert update.owner_user_id is None


def test_bc_plan_response():
    """Test BC Plan response schema."""
    now = datetime.now(timezone.utc)
    response = BCPlanResponse(
        id=1,
        title="Test Plan",
        status=BCPlanStatus.APPROVED,
        owner_user_id=1,
        created_at=now,
        updated_at=now,
        approved_at_utc=now,
    )
    assert response.id == 1
    assert response.status == BCPlanStatus.APPROVED
    assert response.approved_at_utc == now


# BC Plan Version Schema Tests
def test_bc_plan_version_create_valid():
    """Test creating a valid BC Plan Version."""
    version = BCPlanVersionCreate(
        plan_id=1,
        version_number=1,
        authored_by_user_id=1,
        content_json={"section1": {"field1": "value1"}},
    )
    assert version.plan_id == 1
    assert version.version_number == 1
    assert version.status == BCPlanVersionStatus.ACTIVE
    assert version.content_json == {"section1": {"field1": "value1"}}


def test_bc_plan_version_invalid_number():
    """Test BC Plan Version validation rejects non-positive version numbers."""
    with pytest.raises(ValidationError):
        BCPlanVersionCreate(
            plan_id=1,
            version_number=0,
            authored_by_user_id=1,
        )


def test_bc_plan_version_response():
    """Test BC Plan Version response schema."""
    now = datetime.now(timezone.utc)
    response = BCPlanVersionResponse(
        id=1,
        plan_id=1,
        version_number=2,
        status=BCPlanVersionStatus.SUPERSEDED,
        authored_by_user_id=1,
        authored_at_utc=now,
        docx_export_hash="abc123",
    )
    assert response.version_number == 2
    assert response.status == BCPlanVersionStatus.SUPERSEDED
    assert response.docx_export_hash == "abc123"


# BC Template Schema Tests
def test_bc_template_create_valid():
    """Test creating a valid BC Template."""
    template = BCTemplateCreate(
        name="Government BCP Template",
        version="1.0",
        is_default=True,
        schema_json={"sections": []},
    )
    assert template.name == "Government BCP Template"
    assert template.version == "1.0"
    assert template.is_default is True


def test_bc_template_response():
    """Test BC Template response schema."""
    now = datetime.now(timezone.utc)
    response = BCTemplateResponse(
        id=1,
        name="Test Template",
        version="2.0",
        is_default=False,
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.version == "2.0"


# BC Section Definition Schema Tests
def test_bc_section_definition_create_valid():
    """Test creating a valid BC Section Definition."""
    section = BCSectionDefinitionCreate(
        template_id=1,
        key="overview",
        title="Plan Overview",
        order_index=1,
        schema_json={"fields": []},
    )
    assert section.template_id == 1
    assert section.key == "overview"
    assert section.order_index == 1


def test_bc_section_definition_response():
    """Test BC Section Definition response schema."""
    now = datetime.now(timezone.utc)
    response = BCSectionDefinitionResponse(
        id=1,
        template_id=1,
        key="risks",
        title="Risk Assessment",
        order_index=3,
        created_at=now,
        updated_at=now,
    )
    assert response.key == "risks"
    assert response.title == "Risk Assessment"


# BC Contact Schema Tests
def test_bc_contact_create_valid():
    """Test creating a valid BC Contact."""
    contact = BCContactCreate(
        plan_id=1,
        name="John Doe",
        role="Emergency Coordinator",
        phone="+1234567890",
        email="john@example.com",
        notes="Primary contact",
    )
    assert contact.name == "John Doe"
    assert contact.role == "Emergency Coordinator"
    assert contact.phone == "+1234567890"


def test_bc_contact_minimal():
    """Test creating BC Contact with minimal required fields."""
    contact = BCContactCreate(
        plan_id=1,
        name="Jane Smith",
    )
    assert contact.name == "Jane Smith"
    assert contact.role is None
    assert contact.email is None


def test_bc_contact_response():
    """Test BC Contact response schema."""
    now = datetime.now(timezone.utc)
    response = BCContactResponse(
        id=1,
        plan_id=1,
        name="Test Contact",
        role="Manager",
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.role == "Manager"


# BC Process Schema Tests
def test_bc_process_create_valid():
    """Test creating a valid BC Process."""
    process = BCProcessCreate(
        plan_id=1,
        name="Email Service",
        description="Critical email infrastructure",
        rto_minutes=240,
        rpo_minutes=60,
        mtpd_minutes=480,
        impact_rating="critical",
    )
    assert process.name == "Email Service"
    assert process.rto_minutes == 240
    assert process.rpo_minutes == 60
    assert process.mtpd_minutes == 480


def test_bc_process_invalid_negative_rto():
    """Test BC Process validation rejects negative RTO."""
    with pytest.raises(ValidationError):
        BCProcessCreate(
            plan_id=1,
            name="Test Process",
            rto_minutes=-10,
        )


def test_bc_process_response():
    """Test BC Process response schema."""
    now = datetime.now(timezone.utc)
    response = BCProcessResponse(
        id=1,
        plan_id=1,
        name="Database Service",
        rto_minutes=60,
        impact_rating="high",
        created_at=now,
        updated_at=now,
    )
    assert response.name == "Database Service"
    assert response.impact_rating == "high"


# BC Risk Schema Tests
def test_bc_risk_create_valid():
    """Test creating a valid BC Risk."""
    risk = BCRiskCreate(
        plan_id=1,
        threat="Natural disaster",
        likelihood="unlikely",
        impact="catastrophic",
        rating="high",
        mitigation="Offsite backup facility",
        owner_user_id=1,
    )
    assert risk.threat == "Natural disaster"
    assert risk.likelihood == "unlikely"
    assert risk.impact == "catastrophic"
    assert risk.rating == "high"


def test_bc_risk_minimal():
    """Test creating BC Risk with minimal fields."""
    risk = BCRiskCreate(
        plan_id=1,
        threat="Data breach",
    )
    assert risk.threat == "Data breach"
    assert risk.likelihood is None
    assert risk.owner_user_id is None


def test_bc_risk_response():
    """Test BC Risk response schema."""
    now = datetime.now(timezone.utc)
    response = BCRiskResponse(
        id=1,
        plan_id=1,
        threat="Cyber attack",
        rating="critical",
        created_at=now,
        updated_at=now,
    )
    assert response.threat == "Cyber attack"
    assert response.rating == "critical"


# BC Attachment Schema Tests
def test_bc_attachment_create_valid():
    """Test creating a valid BC Attachment."""
    attachment = BCAttachmentCreate(
        plan_id=1,
        file_name="floor_plan.pdf",
        storage_path="/uploads/plans/1/floor_plan.pdf",
        content_type="application/pdf",
        size_bytes=1024000,
        uploaded_by_user_id=1,
    )
    assert attachment.file_name == "floor_plan.pdf"
    assert attachment.size_bytes == 1024000
    assert attachment.content_type == "application/pdf"


def test_bc_attachment_invalid_negative_size():
    """Test BC Attachment validation rejects negative size."""
    with pytest.raises(ValidationError):
        BCAttachmentCreate(
            plan_id=1,
            file_name="test.pdf",
            storage_path="/path",
            uploaded_by_user_id=1,
            size_bytes=-100,
        )


def test_bc_attachment_response():
    """Test BC Attachment response schema."""
    now = datetime.now(timezone.utc)
    response = BCAttachmentResponse(
        id=1,
        plan_id=1,
        file_name="document.docx",
        storage_path="/uploads/doc.docx",
        uploaded_by_user_id=1,
        uploaded_at_utc=now,
        created_at=now,
        updated_at=now,
        hash="abc123def456",
    )
    assert response.file_name == "document.docx"
    assert response.hash == "abc123def456"


# BC Review Schema Tests
def test_bc_review_create_valid():
    """Test creating a valid BC Review."""
    review = BCReviewCreate(
        plan_id=1,
        requested_by_user_id=1,
        reviewer_user_id=2,
        status=BCReviewStatus.PENDING,
        notes="Please review ASAP",
    )
    assert review.plan_id == 1
    assert review.reviewer_user_id == 2
    assert review.status == BCReviewStatus.PENDING


def test_bc_review_status_enum():
    """Test BC Review status enum values."""
    assert BCReviewStatus.PENDING == "pending"
    assert BCReviewStatus.APPROVED == "approved"
    assert BCReviewStatus.CHANGES_REQUESTED == "changes_requested"


def test_bc_review_response():
    """Test BC Review response schema."""
    now = datetime.now(timezone.utc)
    response = BCReviewResponse(
        id=1,
        plan_id=1,
        requested_by_user_id=1,
        reviewer_user_id=2,
        status=BCReviewStatus.APPROVED,
        requested_at_utc=now,
        decided_at_utc=now,
        created_at=now,
        updated_at=now,
    )
    assert response.status == BCReviewStatus.APPROVED
    assert response.decided_at_utc == now


# BC Acknowledgment Schema Tests
def test_bc_ack_create_valid():
    """Test creating a valid BC Acknowledgment."""
    ack = BCAckCreate(
        plan_id=1,
        user_id=1,
        ack_version_number=3,
    )
    assert ack.plan_id == 1
    assert ack.user_id == 1
    assert ack.ack_version_number == 3


def test_bc_ack_response():
    """Test BC Acknowledgment response schema."""
    now = datetime.now(timezone.utc)
    response = BCAckResponse(
        id=1,
        plan_id=1,
        user_id=1,
        ack_at_utc=now,
        ack_version_number=2,
    )
    assert response.ack_at_utc == now
    assert response.ack_version_number == 2


# BC Audit Schema Tests
def test_bc_audit_create_valid():
    """Test creating a valid BC Audit entry."""
    audit = BCAuditCreate(
        plan_id=1,
        action="updated",
        actor_user_id=1,
        details_json={"field": "status", "old_value": "draft", "new_value": "approved"},
    )
    assert audit.action == "updated"
    assert audit.details_json["field"] == "status"


def test_bc_audit_response():
    """Test BC Audit response schema."""
    now = datetime.now(timezone.utc)
    response = BCAuditResponse(
        id=1,
        plan_id=1,
        action="created",
        actor_user_id=1,
        at_utc=now,
    )
    assert response.action == "created"
    assert response.at_utc == now


# BC Change Log Map Schema Tests
def test_bc_change_log_map_create_valid():
    """Test creating a valid BC Change Log Map."""
    change_log = BCChangeLogMapCreate(
        plan_id=1,
        change_guid="550e8400-e29b-41d4-a716-446655440000",
    )
    assert change_log.plan_id == 1
    assert change_log.change_guid == "550e8400-e29b-41d4-a716-446655440000"


def test_bc_change_log_map_invalid_guid():
    """Test BC Change Log Map validation rejects invalid GUID length."""
    with pytest.raises(ValidationError):
        BCChangeLogMapCreate(
            plan_id=1,
            change_guid="short-guid",
        )


def test_bc_change_log_map_response():
    """Test BC Change Log Map response schema."""
    now = datetime.now(timezone.utc)
    response = BCChangeLogMapResponse(
        id=1,
        plan_id=1,
        change_guid="550e8400-e29b-41d4-a716-446655440000",
        imported_at_utc=now,
    )
    assert response.change_guid == "550e8400-e29b-41d4-a716-446655440000"
    assert response.imported_at_utc == now


# SQLAlchemy Model Tests (import validation)
def test_sqlalchemy_models_importable():
    """Test that SQLAlchemy models can be imported without errors."""
    try:
        from app.models.bc_models import (
            BCAck,
            BCAttachment,
            BCAudit,
            BCChangeLogMap,
            BCContact,
            BCPlan,
            BCPlanVersion,
            BCProcess,
            BCReview,
            BCRisk,
            BCSectionDefinition,
            BCTemplate,
        )

        # Verify basic model attributes exist
        assert hasattr(BCPlan, "__tablename__")
        assert hasattr(BCPlanVersion, "__tablename__")
        assert hasattr(BCTemplate, "__tablename__")
        assert hasattr(BCContact, "__tablename__")
        assert hasattr(BCProcess, "__tablename__")
        assert hasattr(BCRisk, "__tablename__")
        assert hasattr(BCAttachment, "__tablename__")
        assert hasattr(BCReview, "__tablename__")
        assert hasattr(BCAck, "__tablename__")
        assert hasattr(BCAudit, "__tablename__")
        assert hasattr(BCChangeLogMap, "__tablename__")
        assert hasattr(BCSectionDefinition, "__tablename__")

        # Verify table names
        assert BCPlan.__tablename__ == "bc_plan"
        assert BCPlanVersion.__tablename__ == "bc_plan_version"
        assert BCTemplate.__tablename__ == "bc_template"
        assert BCContact.__tablename__ == "bc_contact"
        assert BCProcess.__tablename__ == "bc_process"
        assert BCRisk.__tablename__ == "bc_risk"
        assert BCAttachment.__tablename__ == "bc_attachment"
        assert BCReview.__tablename__ == "bc_review"
        assert BCAck.__tablename__ == "bc_ack"
        assert BCAudit.__tablename__ == "bc_audit"
        assert BCChangeLogMap.__tablename__ == "bc_change_log_map"
        assert BCSectionDefinition.__tablename__ == "bc_section_definition"

    except ImportError as e:
        pytest.fail(f"Failed to import SQLAlchemy models: {e}")


def test_sqlalchemy_base_importable():
    """Test that SQLAlchemy Base can be imported."""
    try:
        from app.models import Base, TimestampMixin

        assert Base is not None
        assert TimestampMixin is not None
    except ImportError as e:
        pytest.fail(f"Failed to import SQLAlchemy Base: {e}")
