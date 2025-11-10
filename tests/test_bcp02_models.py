"""
Tests for BCP BC02 Business Continuity Planning data models and schemas.

Tests cover SQLAlchemy 2.0 async models, Pydantic schemas, and validations.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.bcp_models import (
    BcpBackupItemCreate,
    BcpBackupItemResponse,
    BcpBackupItemUpdate,
    BcpChecklistItemCreate,
    BcpChecklistItemResponse,
    BcpChecklistTickCreate,
    BcpChecklistTickResponse,
    BcpContactCreate,
    BcpContactKind,
    BcpContactResponse,
    BcpCriticalActivityCreate,
    BcpCriticalActivityResponse,
    BcpDistributionEntryCreate,
    BcpDistributionEntryResponse,
    BcpEmergencyKitItemCreate,
    BcpEmergencyKitItemResponse,
    BcpEventLogEntryCreate,
    BcpEventLogEntryResponse,
    BcpEvacuationPlanCreate,
    BcpEvacuationPlanResponse,
    BcpImpactCreate,
    BcpImpactResponse,
    BcpIncidentCreate,
    BcpIncidentResponse,
    BcpIncidentStatus,
    BcpInsuranceClaimCreate,
    BcpInsuranceClaimResponse,
    BcpInsurancePolicyCreate,
    BcpInsurancePolicyResponse,
    BcpKitCategory,
    BcpMarketChangeCreate,
    BcpMarketChangeResponse,
    BcpPhase,
    BcpPlanCreate,
    BcpPlanResponse,
    BcpPlanUpdate,
    BcpPriority,
    BcpRecoveryActionCreate,
    BcpRecoveryActionResponse,
    BcpRecoveryContactCreate,
    BcpRecoveryContactResponse,
    BcpReviewItemCreate,
    BcpReviewItemResponse,
    BcpRiskCreate,
    BcpRiskResponse,
    BcpRiskUpdate,
    BcpRoleAssignmentCreate,
    BcpRoleAssignmentResponse,
    BcpRoleCreate,
    BcpRoleResponse,
    BcpSupplierDependency,
    BcpTrainingItemCreate,
    BcpTrainingItemResponse,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ============================================================================
# Core Entity Tests
# ============================================================================


def test_bcp_plan_create_valid():
    """Test creating a valid BCP Plan."""
    plan = BcpPlanCreate(
        company_id=1,
        title="Emergency Response Plan",
        executive_summary="This plan outlines emergency procedures.",
        objectives="Ensure business continuity during disruptions.",
        version="1.0",
    )
    assert plan.company_id == 1
    assert plan.title == "Emergency Response Plan"
    assert plan.version == "1.0"


def test_bcp_plan_create_invalid_company_id():
    """Test BCP Plan validation rejects invalid company_id."""
    with pytest.raises(ValidationError):
        BcpPlanCreate(
            company_id=0,  # Invalid: must be > 0
            title="Test Plan",
        )


def test_bcp_plan_create_invalid_title():
    """Test BCP Plan validation rejects empty title."""
    with pytest.raises(ValidationError):
        BcpPlanCreate(
            company_id=1,
            title="",  # Invalid: min_length=1
        )


def test_bcp_plan_update_partial():
    """Test partial update of BCP Plan."""
    update = BcpPlanUpdate(
        title="Updated Title",
        version="2.0",
    )
    assert update.title == "Updated Title"
    assert update.version == "2.0"
    assert update.objectives is None


def test_bcp_plan_response():
    """Test BCP Plan response schema."""
    now = datetime.now(timezone.utc)
    response = BcpPlanResponse(
        id=1,
        company_id=1,
        title="Test Plan",
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.company_id == 1


def test_bcp_distribution_entry_create_valid():
    """Test creating a valid Distribution Entry."""
    entry = BcpDistributionEntryCreate(
        plan_id=1,
        copy_number=1,
        name="John Doe",
        location="Office Safe",
    )
    assert entry.plan_id == 1
    assert entry.copy_number == 1
    assert entry.name == "John Doe"


def test_bcp_distribution_entry_invalid_copy_number():
    """Test Distribution Entry validation rejects invalid copy number."""
    with pytest.raises(ValidationError):
        BcpDistributionEntryCreate(
            plan_id=1,
            copy_number=0,  # Invalid: must be > 0
            name="John Doe",
        )


# ============================================================================
# Risk & Preparedness Tests
# ============================================================================


def test_bcp_risk_create_valid():
    """Test creating a valid Risk."""
    risk = BcpRiskCreate(
        plan_id=1,
        description="Natural disaster risk",
        likelihood=3,
        impact=4,
        rating=12,
        severity="High",
        preventative_actions="Regular drills",
        contingency_plans="Activate backup site",
    )
    assert risk.plan_id == 1
    assert risk.likelihood == 3
    assert risk.impact == 4
    assert risk.rating == 12
    assert risk.severity == "High"


def test_bcp_risk_likelihood_validation():
    """Test Risk likelihood validation (1-4 range)."""
    with pytest.raises(ValidationError):
        BcpRiskCreate(
            plan_id=1,
            description="Test risk",
            likelihood=5,  # Invalid: must be 1-4
            impact=2,
        )


def test_bcp_risk_impact_validation():
    """Test Risk impact validation (1-4 range)."""
    with pytest.raises(ValidationError):
        BcpRiskCreate(
            plan_id=1,
            description="Test risk",
            likelihood=2,
            impact=0,  # Invalid: must be 1-4
        )


def test_bcp_risk_update_partial():
    """Test partial Risk update."""
    update = BcpRiskUpdate(
        severity="Extreme",
        preventative_actions="Enhanced security measures",
    )
    assert update.severity == "Extreme"
    assert update.preventative_actions == "Enhanced security measures"
    assert update.description is None


def test_bcp_insurance_policy_create_valid():
    """Test creating a valid Insurance Policy."""
    policy = BcpInsurancePolicyCreate(
        plan_id=1,
        type="Property Insurance",
        coverage="Fire, flood, earthquake damage",
        insurer="ABC Insurance Co.",
        contact="policy@abc.com",
        payment_terms="Annual",
    )
    assert policy.plan_id == 1
    assert policy.type == "Property Insurance"
    assert policy.insurer == "ABC Insurance Co."


def test_bcp_backup_item_create_valid():
    """Test creating a valid Backup Item."""
    backup = BcpBackupItemCreate(
        plan_id=1,
        data_scope="Customer database",
        frequency="Daily",
        medium="Cloud storage",
        owner="IT Team",
        steps="1. Run backup script\n2. Verify backup integrity",
    )
    assert backup.plan_id == 1
    assert backup.data_scope == "Customer database"
    assert backup.frequency == "Daily"


# ============================================================================
# Business Impact Analysis Tests
# ============================================================================


def test_bcp_critical_activity_create_valid():
    """Test creating a valid Critical Activity."""
    activity = BcpCriticalActivityCreate(
        plan_id=1,
        name="Email Services",
        description="Corporate email system",
        priority=BcpPriority.HIGH,
        supplier_dependency=BcpSupplierDependency.MAJOR,
        notes="Critical for daily operations",
    )
    assert activity.plan_id == 1
    assert activity.name == "Email Services"
    assert activity.priority == BcpPriority.HIGH
    assert activity.supplier_dependency == BcpSupplierDependency.MAJOR


def test_bcp_critical_activity_priority_enum():
    """Test Critical Activity priority enum values."""
    assert BcpPriority.HIGH.value == "High"
    assert BcpPriority.MEDIUM.value == "Medium"
    assert BcpPriority.LOW.value == "Low"


def test_bcp_impact_create_valid():
    """Test creating a valid Impact."""
    impact = BcpImpactCreate(
        critical_activity_id=1,
        losses_financial="$10,000 per hour",
        losses_staffing="50% productivity loss",
        losses_reputation="Customer confidence impact",
        rto_hours=4,
    )
    assert impact.critical_activity_id == 1
    assert impact.rto_hours == 4
    assert impact.losses_financial == "$10,000 per hour"


def test_bcp_impact_rto_validation():
    """Test Impact RTO validation (non-negative)."""
    with pytest.raises(ValidationError):
        BcpImpactCreate(
            critical_activity_id=1,
            rto_hours=-1,  # Invalid: must be >= 0
        )


# ============================================================================
# Incident Response Tests
# ============================================================================


def test_bcp_incident_create_valid():
    """Test creating a valid Incident."""
    now = datetime.now(timezone.utc)
    incident = BcpIncidentCreate(
        plan_id=1,
        started_at=now,
        status=BcpIncidentStatus.ACTIVE,
    )
    assert incident.plan_id == 1
    assert incident.status == BcpIncidentStatus.ACTIVE
    assert incident.started_at == now


def test_bcp_incident_status_enum():
    """Test Incident status enum values."""
    assert BcpIncidentStatus.ACTIVE.value == "Active"
    assert BcpIncidentStatus.CLOSED.value == "Closed"


def test_bcp_checklist_item_create_valid():
    """Test creating a valid Checklist Item."""
    item = BcpChecklistItemCreate(
        plan_id=1,
        phase=BcpPhase.IMMEDIATE,
        label="Notify emergency contacts",
        default_order=1,
    )
    assert item.plan_id == 1
    assert item.phase == BcpPhase.IMMEDIATE
    assert item.label == "Notify emergency contacts"


def test_bcp_checklist_tick_create_valid():
    """Test creating a valid Checklist Tick."""
    now = datetime.now(timezone.utc)
    tick = BcpChecklistTickCreate(
        plan_id=1,
        checklist_item_id=1,
        incident_id=1,
        is_done=True,
        done_at=now,
        done_by=1,
    )
    assert tick.is_done is True
    assert tick.done_at == now
    assert tick.done_by == 1


def test_bcp_evacuation_plan_create_valid():
    """Test creating a valid Evacuation Plan."""
    evac = BcpEvacuationPlanCreate(
        plan_id=1,
        meeting_point="Parking lot A",
        notes="Assemble by department",
    )
    assert evac.plan_id == 1
    assert evac.meeting_point == "Parking lot A"


def test_bcp_emergency_kit_item_create_valid():
    """Test creating a valid Emergency Kit Item."""
    now = datetime.now(timezone.utc)
    kit_item = BcpEmergencyKitItemCreate(
        plan_id=1,
        category=BcpKitCategory.EQUIPMENT,
        name="First Aid Kit",
        notes="Check monthly",
        last_checked_at=now,
    )
    assert kit_item.category == BcpKitCategory.EQUIPMENT
    assert kit_item.name == "First Aid Kit"


def test_bcp_role_create_valid():
    """Test creating a valid Role."""
    role = BcpRoleCreate(
        plan_id=1,
        title="Incident Commander",
        responsibilities="Overall coordination of response",
    )
    assert role.title == "Incident Commander"
    assert role.responsibilities == "Overall coordination of response"


def test_bcp_role_assignment_create_valid():
    """Test creating a valid Role Assignment."""
    assignment = BcpRoleAssignmentCreate(
        role_id=1,
        user_id=1,
        is_alternate=False,
        contact_info="555-1234",
    )
    assert assignment.role_id == 1
    assert assignment.user_id == 1
    assert assignment.is_alternate is False


def test_bcp_contact_create_valid():
    """Test creating a valid Contact."""
    contact = BcpContactCreate(
        plan_id=1,
        kind=BcpContactKind.EXTERNAL,
        person_or_org="Fire Department",
        phones="911, 555-0100",
        email="fire@city.gov",
        responsibility_or_agency="Emergency Services",
    )
    assert contact.kind == BcpContactKind.EXTERNAL
    assert contact.person_or_org == "Fire Department"


def test_bcp_event_log_entry_create_valid():
    """Test creating a valid Event Log Entry."""
    now = datetime.now(timezone.utc)
    entry = BcpEventLogEntryCreate(
        plan_id=1,
        incident_id=1,
        happened_at=now,
        author_id=1,
        notes="Incident response initiated",
        initials="JD",
    )
    assert entry.notes == "Incident response initiated"
    assert entry.initials == "JD"


# ============================================================================
# Recovery Tests
# ============================================================================


def test_bcp_recovery_action_create_valid():
    """Test creating a valid Recovery Action."""
    now = datetime.now(timezone.utc)
    action = BcpRecoveryActionCreate(
        plan_id=1,
        critical_activity_id=1,
        action="Restore email service from backup",
        resources="Backup server, IT staff",
        owner_id=1,
        rto_hours=4,
        due_date=now,
    )
    assert action.action == "Restore email service from backup"
    assert action.rto_hours == 4


def test_bcp_recovery_action_rto_validation():
    """Test Recovery Action RTO validation (non-negative)."""
    with pytest.raises(ValidationError):
        BcpRecoveryActionCreate(
            plan_id=1,
            action="Test action",
            rto_hours=-5,  # Invalid: must be >= 0
        )


def test_bcp_recovery_contact_create_valid():
    """Test creating a valid Recovery Contact."""
    contact = BcpRecoveryContactCreate(
        plan_id=1,
        org_name="Disaster Recovery Inc.",
        contact_name="Jane Smith",
        title="Recovery Specialist",
        phone="555-7890",
    )
    assert contact.org_name == "Disaster Recovery Inc."
    assert contact.contact_name == "Jane Smith"


def test_bcp_insurance_claim_create_valid():
    """Test creating a valid Insurance Claim."""
    now = datetime.now(timezone.utc)
    claim = BcpInsuranceClaimCreate(
        plan_id=1,
        insurer="ABC Insurance Co.",
        claim_date=now,
        details="Water damage from flood",
        follow_up_actions="Submit photos, schedule adjuster visit",
    )
    assert claim.insurer == "ABC Insurance Co."
    assert claim.details == "Water damage from flood"


def test_bcp_market_change_create_valid():
    """Test creating a valid Market Change."""
    change = BcpMarketChangeCreate(
        plan_id=1,
        change="New competitor entered market",
        impact="Potential loss of market share",
        options="Enhance service offerings, competitive pricing",
    )
    assert change.change == "New competitor entered market"
    assert change.impact == "Potential loss of market share"


def test_bcp_training_item_create_valid():
    """Test creating a valid Training Item."""
    now = datetime.now(timezone.utc)
    training = BcpTrainingItemCreate(
        plan_id=1,
        training_date=now,
        training_type="Tabletop Exercise",
        comments="All staff participated successfully",
    )
    assert training.training_type == "Tabletop Exercise"
    assert training.comments == "All staff participated successfully"


def test_bcp_review_item_create_valid():
    """Test creating a valid Review Item."""
    now = datetime.now(timezone.utc)
    review = BcpReviewItemCreate(
        plan_id=1,
        review_date=now,
        reason="Annual review",
        changes_made="Updated contact list, revised RTO targets",
    )
    assert review.reason == "Annual review"
    assert review.changes_made == "Updated contact list, revised RTO targets"


# ============================================================================
# Schema Response Tests
# ============================================================================


def test_bcp_plan_response_from_attributes():
    """Test BCP Plan response schema with from_attributes."""
    now = datetime.now(timezone.utc)
    response = BcpPlanResponse(
        id=1,
        company_id=1,
        title="Test Plan",
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.company_id == 1
    assert response.title == "Test Plan"


def test_bcp_risk_response():
    """Test BCP Risk response schema."""
    now = datetime.now(timezone.utc)
    response = BcpRiskResponse(
        id=1,
        plan_id=1,
        description="Test risk",
        likelihood=2,
        impact=3,
        rating=6,
        severity="Medium",
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.rating == 6
    assert response.severity == "Medium"


def test_bcp_critical_activity_response():
    """Test BCP Critical Activity response schema."""
    now = datetime.now(timezone.utc)
    response = BcpCriticalActivityResponse(
        id=1,
        plan_id=1,
        name="Test Activity",
        priority=BcpPriority.HIGH,
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.priority == BcpPriority.HIGH


def test_bcp_incident_response():
    """Test BCP Incident response schema."""
    now = datetime.now(timezone.utc)
    response = BcpIncidentResponse(
        id=1,
        plan_id=1,
        started_at=now,
        status=BcpIncidentStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    assert response.id == 1
    assert response.status == BcpIncidentStatus.ACTIVE
