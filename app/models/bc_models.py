"""
SQLAlchemy 2.0 async models for Business Continuity Planning (BCP) system.

These models support a template-driven, versioned BCP system with:
- Review and approval workflows
- Attachments and document management
- Audit trails and acknowledgments
- Contacts, processes, and risk management
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
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


class BCPlan(Base, TimestampMixin):
    """
    Main business continuity plan table.
    
    Supports multi-tenancy through optional org_id field.
    Plans go through draft -> in_review -> approved -> archived lifecycle.
    """

    __tablename__ = "bc_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="For multi-tenant support")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum("draft", "in_review", "approved", "archived", name="bc_plan_status_enum"),
        nullable=False,
        default="draft",
    )
    template_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("bc_template.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_version_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("bc_plan_version.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_at_utc: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    # Indexes for common queries
    __table_args__ = (
        Index("idx_bc_plan_org_status", "org_id", "status"),
        Index("idx_bc_plan_status_updated", "status", "updated_at"),
        Index("idx_bc_plan_template", "template_id"),
        Index("idx_bc_plan_owner", "owner_user_id"),
    )

    # Relationships (note: these require corresponding models to exist)
    # template: Mapped[Optional["BCTemplate"]] = relationship("BCTemplate", back_populates="plans")
    # current_version: Mapped[Optional["BCPlanVersion"]] = relationship("BCPlanVersion", foreign_keys=[current_version_id])
    # versions: Mapped[list["BCPlanVersion"]] = relationship("BCPlanVersion", back_populates="plan", foreign_keys="BCPlanVersion.plan_id")
    # contacts: Mapped[list["BCContact"]] = relationship("BCContact", back_populates="plan", cascade="all, delete-orphan")
    # processes: Mapped[list["BCProcess"]] = relationship("BCProcess", back_populates="plan", cascade="all, delete-orphan")
    # risks: Mapped[list["BCRisk"]] = relationship("BCRisk", back_populates="plan", cascade="all, delete-orphan")
    # attachments: Mapped[list["BCAttachment"]] = relationship("BCAttachment", back_populates="plan", cascade="all, delete-orphan")
    # reviews: Mapped[list["BCReview"]] = relationship("BCReview", back_populates="plan", cascade="all, delete-orphan")
    # acknowledgments: Mapped[list["BCAck"]] = relationship("BCAck", back_populates="plan", cascade="all, delete-orphan")
    # audit_logs: Mapped[list["BCAudit"]] = relationship("BCAudit", back_populates="plan", cascade="all, delete-orphan")
    # change_logs: Mapped[list["BCChangeLogMap"]] = relationship("BCChangeLogMap", back_populates="plan", cascade="all, delete-orphan")


class BCPlanVersion(Base):
    """
    Version history for business continuity plans.
    
    Each plan can have multiple versions. Only one version is 'active' at a time,
    others are 'superseded'. Content is stored as JSON for flexibility.
    """

    __tablename__ = "bc_plan_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum("active", "superseded", name="bc_plan_version_status_enum"),
        nullable=False,
        default="active",
    )
    authored_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    authored_at_utc: Mapped[datetime] = mapped_column(nullable=False, server_default="CURRENT_TIMESTAMP")
    summary_change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="Section data as JSON")
    docx_export_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pdf_export_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_bc_plan_version_plan", "plan_id"),
        Index("idx_bc_plan_version_plan_status", "plan_id", "status"),
        Index("idx_bc_plan_version_authored", "authored_by_user_id"),
        CheckConstraint("version_number > 0", name="ck_version_number_positive"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="versions", foreign_keys=[plan_id])


class BCTemplate(Base, TimestampMixin):
    """
    Template definitions for business continuity plans.
    
    Templates define the structure (sections and fields) that plans follow.
    Schema is stored as JSON for flexibility.
    """

    __tablename__ = "bc_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    schema_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Section and field definitions as JSON",
    )

    __table_args__ = (Index("idx_bc_template_default", "is_default"),)

    # Relationships
    # plans: Mapped[list["BCPlan"]] = relationship("BCPlan", back_populates="template")
    # sections: Mapped[list["BCSectionDefinition"]] = relationship("BCSectionDefinition", back_populates="template", cascade="all, delete-orphan")


class BCSectionDefinition(Base, TimestampMixin):
    """
    Optional section definitions for templates.
    
    If not used, section definitions can live entirely in bc_template.schema_json.
    This table allows more granular control and querying of sections.
    """

    __tablename__ = "bc_section_definition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_template.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False, comment="Unique section key within template")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="Field definitions for this section")

    __table_args__ = (
        Index("idx_bc_section_template", "template_id"),
        Index("idx_bc_section_template_order", "template_id", "order_index"),
    )

    # Relationships
    # template: Mapped["BCTemplate"] = relationship("BCTemplate", back_populates="sections")


class BCContact(Base, TimestampMixin):
    """
    Emergency contacts associated with a business continuity plan.
    """

    __tablename__ = "bc_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_bc_contact_plan", "plan_id"),)

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="contacts")


class BCProcess(Base, TimestampMixin):
    """
    Critical business processes tracked in continuity plans.
    
    Includes recovery objectives (RTO, RPO, MTPD) and impact ratings.
    """

    __tablename__ = "bc_process"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rto_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Recovery Time Objective in minutes",
    )
    rpo_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Recovery Point Objective in minutes",
    )
    mtpd_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum Tolerable Period of Disruption in minutes",
    )
    impact_rating: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="e.g., critical, high, medium, low",
    )
    dependencies_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Process dependencies as JSON",
    )

    __table_args__ = (
        Index("idx_bc_process_plan", "plan_id"),
        Index("idx_bc_process_impact", "impact_rating"),
        CheckConstraint("rto_minutes >= 0", name="ck_rto_non_negative"),
        CheckConstraint("rpo_minutes >= 0", name="ck_rpo_non_negative"),
        CheckConstraint("mtpd_minutes >= 0", name="ck_mtpd_non_negative"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="processes")


class BCRisk(Base, TimestampMixin):
    """
    Risk assessments for business continuity plans.
    
    Tracks threats, likelihood, impact, and mitigation strategies.
    """

    __tablename__ = "bc_risk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    threat: Mapped[str] = mapped_column(String(500), nullable=False)
    likelihood: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="e.g., rare, unlikely, possible, likely, almost_certain",
    )
    impact: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="e.g., insignificant, minor, moderate, major, catastrophic",
    )
    rating: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Overall risk rating, e.g., low, medium, high, critical",
    )
    mitigation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="Risk owner")

    __table_args__ = (
        Index("idx_bc_risk_plan", "plan_id"),
        Index("idx_bc_risk_rating", "rating"),
        Index("idx_bc_risk_owner", "owner_user_id"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="risks")


class BCAttachment(Base, TimestampMixin):
    """
    File attachments for business continuity plans.
    
    Stores metadata; actual files are stored in file system or object storage.
    """

    __tablename__ = "bc_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False, comment="Path in storage system")
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at_utc: Mapped[datetime] = mapped_column(nullable=False, server_default="CURRENT_TIMESTAMP")
    hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA256 hash for integrity verification",
    )

    __table_args__ = (
        Index("idx_bc_attachment_plan", "plan_id"),
        Index("idx_bc_attachment_uploaded_by", "uploaded_by_user_id"),
        CheckConstraint("size_bytes >= 0", name="ck_size_non_negative"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="attachments")


class BCReview(Base, TimestampMixin):
    """
    Review and approval workflow for business continuity plans.
    
    Tracks review requests, reviewers, decisions, and notes.
    """

    __tablename__ = "bc_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        SQLEnum("pending", "approved", "changes_requested", name="bc_review_status_enum"),
        nullable=False,
        default="pending",
    )
    requested_at_utc: Mapped[datetime] = mapped_column(nullable=False, server_default="CURRENT_TIMESTAMP")
    decided_at_utc: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_bc_review_plan", "plan_id"),
        Index("idx_bc_review_reviewer", "reviewer_user_id"),
        Index("idx_bc_review_status", "status"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="reviews")


class BCAck(Base):
    """
    User acknowledgments of business continuity plan reviews.
    
    Tracks when users have read/acknowledged specific plan versions.
    """

    __tablename__ = "bc_ack"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ack_at_utc: Mapped[datetime] = mapped_column(nullable=False, server_default="CURRENT_TIMESTAMP")
    ack_version_number: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Version number acknowledged",
    )

    __table_args__ = (
        Index("idx_bc_ack_plan", "plan_id"),
        Index("idx_bc_ack_user", "user_id"),
        Index("idx_bc_ack_plan_user", "plan_id", "user_id"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="acknowledgments")


class BCAudit(Base):
    """
    Audit trail for business continuity plan changes.
    
    Tracks all actions performed on plans for compliance and accountability.
    """

    __tablename__ = "bc_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="e.g., created, updated, approved, archived",
    )
    actor_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    details_json: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
        comment="Additional audit details as JSON",
    )
    at_utc: Mapped[datetime] = mapped_column(nullable=False, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        Index("idx_bc_audit_plan", "plan_id"),
        Index("idx_bc_audit_actor", "actor_user_id"),
        Index("idx_bc_audit_action", "action"),
        Index("idx_bc_audit_at", "at_utc"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="audit_logs")


class BCChangeLogMap(Base):
    """
    Links change log files to business continuity plans.
    
    Maps change GUIDs from the changes/ folder to specific plans.
    """

    __tablename__ = "bc_change_log_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("bc_plan.id", ondelete="CASCADE"),
        nullable=False,
    )
    change_guid: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        comment="GUID referencing change log file",
    )
    imported_at_utc: Mapped[datetime] = mapped_column(nullable=False, server_default="CURRENT_TIMESTAMP")

    __table_args__ = (
        Index("idx_bc_change_log_plan", "plan_id"),
        Index("idx_bc_change_log_guid", "change_guid"),
    )

    # Relationships
    # plan: Mapped["BCPlan"] = relationship("BCPlan", back_populates="change_logs")
