"""
SQLAlchemy 2.0 async models for Business Continuity Planning (BCP) BC02 implementation.

These models support comprehensive BCP functionality including:
- Core plan management with company_id for multi-tenancy
- Risk assessment and preparedness
- Business Impact Analysis (BIA)
- Incident response and management
- Recovery planning and execution

All models follow the SQLAlchemy 2.0 async pattern and include proper
foreign key relationships with CASCADE deletes for data integrity.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base, TimestampMixin


# ============================================================================
# Core Entities
# ============================================================================


class BcpPlan(Base, TimestampMixin):
    """
    Main business continuity plan table with company-level multi-tenancy.
    
    Each plan represents a comprehensive BCP for a company/organization.
    """

    __tablename__ = "bcp_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Multi-tenant: company this plan belongs to"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    executive_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    objectives: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Plan objectives and goals"
    )
    version: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="Plan version number"
    )
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Last review date"
    )
    next_review_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Next scheduled review date"
    )
    distribution_notes: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Notes about plan distribution"
    )

    __table_args__ = (
        Index("idx_bcp_plan_company", "company_id"),
        Index("idx_bcp_plan_next_review", "next_review_at"),
    )


class BcpDistributionEntry(Base, TimestampMixin):
    """
    Tracks physical/electronic copies of BCP distributed to stakeholders.
    """

    __tablename__ = "bcp_distribution_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    copy_number: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Sequential copy number"
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Recipient name"
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Storage location of copy"
    )

    __table_args__ = (
        Index("idx_bcp_distribution_plan", "plan_id"),
        Index("idx_bcp_distribution_copy", "plan_id", "copy_number"),
    )


# ============================================================================
# Risk & Preparedness
# ============================================================================


class BcpRisk(Base, TimestampMixin):
    """
    Risk assessment with likelihood and impact ratings.
    
    Severity is computed from likelihood × impact but stored denormalized
    for query performance.
    """

    __tablename__ = "bcp_risk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    likelihood: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Likelihood rating 1-4"
    )
    impact: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Impact rating 1-4"
    )
    rating: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Computed risk rating (likelihood × impact)"
    )
    severity: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Computed severity: Low, Medium, High, Extreme (denormalized)",
    )
    preventative_actions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Actions to prevent risk"
    )
    contingency_plans: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Plans if risk materializes"
    )

    __table_args__ = (
        Index("idx_bcp_risk_plan", "plan_id"),
        Index("idx_bcp_risk_severity", "severity"),
        CheckConstraint(
            "likelihood >= 1 AND likelihood <= 4 OR likelihood IS NULL",
            name="ck_likelihood_range",
        ),
        CheckConstraint(
            "impact >= 1 AND impact <= 4 OR impact IS NULL", name="ck_impact_range"
        ),
    )


class BcpInsurancePolicy(Base, TimestampMixin):
    """
    Insurance policies relevant to business continuity.
    """

    __tablename__ = "bcp_insurance_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Policy type (e.g., Property, Liability)"
    )
    coverage: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="What is covered"
    )
    exclusions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="What is excluded"
    )
    insurer: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Insurance company name"
    )
    contact: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Contact information"
    )
    last_review_date: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Last policy review date"
    )
    payment_terms: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Payment schedule and terms"
    )

    __table_args__ = (
        Index("idx_bcp_insurance_plan", "plan_id"),
        Index("idx_bcp_insurance_review", "last_review_date"),
    )


class BcpBackupItem(Base, TimestampMixin):
    """
    Data backup items and procedures.
    """

    __tablename__ = "bcp_backup_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    data_scope: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="What data is backed up"
    )
    frequency: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="Backup frequency (e.g., Daily, Weekly)"
    )
    medium: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="Backup medium (e.g., Cloud, Tape)"
    )
    owner: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Person/team responsible"
    )
    steps: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Backup procedure steps"
    )

    __table_args__ = (Index("idx_bcp_backup_plan", "plan_id"),)


# ============================================================================
# Business Impact Analysis (BIA)
# ============================================================================


class BcpCriticalActivity(Base, TimestampMixin):
    """
    Critical business activities requiring continuity planning.
    """

    __tablename__ = "bcp_critical_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(
        SQLEnum("High", "Medium", "Low", name="bcp_priority_enum"),
        nullable=True,
        comment="Activity priority",
    )
    supplier_dependency: Mapped[Optional[str]] = mapped_column(
        SQLEnum("None", "Sole", "Major", "Many", name="bcp_supplier_dependency_enum"),
        nullable=True,
        comment="Level of supplier dependency",
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_bcp_critical_activity_plan", "plan_id"),
        Index("idx_bcp_critical_activity_priority", "priority"),
    )


class BcpImpact(Base, TimestampMixin):
    """
    Impact assessment for critical activities.
    """

    __tablename__ = "bcp_impact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    critical_activity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_critical_activity.id", ondelete="CASCADE"),
        nullable=False,
    )
    losses_financial: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Financial impact description"
    )
    losses_staffing: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Staffing impact description"
    )
    losses_reputation: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Reputational impact description"
    )
    fines: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Potential fines and penalties"
    )
    legal_liability: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Legal liability description"
    )
    rto_hours: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Recovery Time Objective in hours"
    )

    __table_args__ = (
        Index("idx_bcp_impact_activity", "critical_activity_id"),
        Index("idx_bcp_impact_rto", "rto_hours"),
        CheckConstraint("rto_hours >= 0 OR rto_hours IS NULL", name="ck_rto_positive"),
    )


# ============================================================================
# Response (Incident)
# ============================================================================


class BcpIncident(Base, TimestampMixin):
    """
    Active or historical incidents triggering BCP response.
    """

    __tablename__ = "bcp_incident"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        nullable=False, comment="Incident start time"
    )
    status: Mapped[str] = mapped_column(
        SQLEnum("Active", "Closed", name="bcp_incident_status_enum"),
        nullable=False,
        default="Active",
    )
    source: Mapped[Optional[str]] = mapped_column(
        SQLEnum("Manual", "UptimeKuma", "Other", name="bcp_incident_source_enum"),
        nullable=True,
        comment="How incident was triggered",
    )

    __table_args__ = (
        Index("idx_bcp_incident_plan", "plan_id"),
        Index("idx_bcp_incident_status", "status"),
        Index("idx_bcp_incident_started", "started_at"),
    )


class BcpChecklistItem(Base, TimestampMixin):
    """
    Checklist items for incident response phases.
    """

    __tablename__ = "bcp_checklist_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    phase: Mapped[str] = mapped_column(
        SQLEnum("Immediate", "CrisisRecovery", name="bcp_phase_enum"),
        nullable=False,
        comment="Response phase",
    )
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    default_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Display order"
    )

    __table_args__ = (
        Index("idx_bcp_checklist_plan", "plan_id"),
        Index("idx_bcp_checklist_phase", "phase"),
        Index("idx_bcp_checklist_order", "plan_id", "phase", "default_order"),
    )


class BcpChecklistTick(Base, TimestampMixin):
    """
    Tracks completion of checklist items for specific incidents.
    """

    __tablename__ = "bcp_checklist_tick"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    checklist_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_checklist_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_incident.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Completion status"
    )
    done_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="When completed"
    )
    done_by: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="User ID who completed"
    )

    __table_args__ = (
        Index("idx_bcp_tick_incident", "incident_id"),
        Index("idx_bcp_tick_item", "checklist_item_id"),
        Index("idx_bcp_tick_plan", "plan_id"),
    )


class BcpEvacuationPlan(Base, TimestampMixin):
    """
    Evacuation procedures and meeting points.
    """

    __tablename__ = "bcp_evacuation_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    meeting_point: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="Primary meeting point location"
    )
    floorplan_file_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Reference to uploaded floorplan file"
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Additional evacuation notes"
    )

    __table_args__ = (Index("idx_bcp_evacuation_plan", "plan_id"),)


class BcpEmergencyKitItem(Base, TimestampMixin):
    """
    Items in emergency preparedness kit.
    """

    __tablename__ = "bcp_emergency_kit_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        SQLEnum("Document", "Equipment", name="bcp_kit_category_enum"),
        nullable=False,
        comment="Item category",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Last verification date"
    )

    __table_args__ = (
        Index("idx_bcp_kit_plan", "plan_id"),
        Index("idx_bcp_kit_category", "category"),
        Index("idx_bcp_kit_checked", "last_checked_at"),
    )


class BcpRole(Base, TimestampMixin):
    """
    Emergency response roles and responsibilities.
    """

    __tablename__ = "bcp_role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    responsibilities: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Role responsibilities"
    )

    __table_args__ = (Index("idx_bcp_role_plan", "plan_id"),)


class BcpRoleAssignment(Base, TimestampMixin):
    """
    Assigns users to emergency response roles.
    """

    __tablename__ = "bcp_role_assignment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_role.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="Assigned user")
    is_alternate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Is this an alternate/backup?"
    )
    contact_info: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="Emergency contact information"
    )

    __table_args__ = (
        Index("idx_bcp_role_assignment_role", "role_id"),
        Index("idx_bcp_role_assignment_user", "user_id"),
    )


class BcpContact(Base, TimestampMixin):
    """
    Emergency contacts (internal and external).
    """

    __tablename__ = "bcp_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        SQLEnum("Internal", "External", name="bcp_contact_kind_enum"),
        nullable=False,
        comment="Contact type",
    )
    person_or_org: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Name of person or organization"
    )
    phones: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="Phone numbers (comma-separated)"
    )
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    responsibility_or_agency: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="Role/responsibility or agency name"
    )

    __table_args__ = (
        Index("idx_bcp_contact_plan", "plan_id"),
        Index("idx_bcp_contact_kind", "kind"),
    )


class BcpEventLogEntry(Base, TimestampMixin):
    """
    Chronological log of events during an incident.
    """

    __tablename__ = "bcp_event_log_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("bcp_incident.id", ondelete="CASCADE"),
        nullable=True,
    )
    happened_at: Mapped[datetime] = mapped_column(
        nullable=False, comment="Event timestamp"
    )
    author_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="User who logged this event"
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    initials: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, comment="Author initials for quick reference"
    )

    __table_args__ = (
        Index("idx_bcp_event_plan", "plan_id"),
        Index("idx_bcp_event_incident", "incident_id"),
        Index("idx_bcp_event_time", "happened_at"),
    )


# ============================================================================
# Recovery
# ============================================================================


class BcpRecoveryAction(Base, TimestampMixin):
    """
    Recovery actions linked to critical activities.
    """

    __tablename__ = "bcp_recovery_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    critical_activity_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("bcp_critical_activity.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resources: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Required resources"
    )
    owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="User responsible for action"
    )
    rto_hours: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="Recovery time objective in hours"
    )
    due_date: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Target completion date"
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Actual completion date"
    )

    __table_args__ = (
        Index("idx_bcp_recovery_plan", "plan_id"),
        Index("idx_bcp_recovery_activity", "critical_activity_id"),
        Index("idx_bcp_recovery_owner", "owner_id"),
        Index("idx_bcp_recovery_due", "due_date"),
        CheckConstraint(
            "rto_hours >= 0 OR rto_hours IS NULL", name="ck_recovery_rto_positive"
        ),
    )


class BcpRecoveryContact(Base, TimestampMixin):
    """
    External contacts for recovery assistance.
    """

    __tablename__ = "bcp_recovery_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Organization name"
    )
    contact_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Contact person name"
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Contact person title"
    )
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    __table_args__ = (Index("idx_bcp_recovery_contact_plan", "plan_id"),)


class BcpInsuranceClaim(Base, TimestampMixin):
    """
    Insurance claims filed during/after incidents.
    """

    __tablename__ = "bcp_insurance_claim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    insurer: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_date: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, comment="Date claim was filed"
    )
    details: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Claim details"
    )
    follow_up_actions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Follow-up actions needed"
    )

    __table_args__ = (
        Index("idx_bcp_claim_plan", "plan_id"),
        Index("idx_bcp_claim_date", "claim_date"),
    )


class BcpMarketChange(Base, TimestampMixin):
    """
    Market changes and strategic impacts on business continuity.
    """

    __tablename__ = "bcp_market_change"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    change: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Description of market change"
    )
    impact: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Impact on business continuity"
    )
    options: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Response options"
    )

    __table_args__ = (Index("idx_bcp_market_plan", "plan_id"),)


class BcpTrainingItem(Base, TimestampMixin):
    """
    Training sessions and exercises for BCP preparedness.
    """

    __tablename__ = "bcp_training_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    training_date: Mapped[datetime] = mapped_column(
        nullable=False, comment="Training session date"
    )
    training_type: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="Type of training (e.g., Tabletop, Full-scale)"
    )
    comments: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Training notes and outcomes"
    )

    __table_args__ = (
        Index("idx_bcp_training_plan", "plan_id"),
        Index("idx_bcp_training_date", "training_date"),
    )


class BcpReviewItem(Base, TimestampMixin):
    """
    Plan review history and changes.
    """

    __tablename__ = "bcp_review_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bcp_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_date: Mapped[datetime] = mapped_column(
        nullable=False, comment="Date of review"
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Reason for review"
    )
    changes_made: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="Summary of changes made"
    )

    __table_args__ = (
        Index("idx_bcp_review_plan", "plan_id"),
        Index("idx_bcp_review_date", "review_date"),
    )
