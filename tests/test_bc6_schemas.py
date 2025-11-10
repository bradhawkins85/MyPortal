"""
Tests for BC6 Pydantic schema definitions.

Tests validation and constraints for:
- TemplateSchema, SectionSchema, FieldSchema
- PlanCreate, PlanUpdate, PlanDetail
- PlanVersionCreate, PlanVersionDetail
- ReviewCreate, ReviewDecision
- AttachmentMeta
- ExportRequest
- RTO/RPO validation (non-negative integers)
- Date/time validation (ISO 8601 UTC)
- Status transition validation
"""
import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

# Import schemas directly without going through app package to avoid DB initialization
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas.bc5_models import (
    # Template schemas
    TemplateSchema,
    SectionSchema,
    FieldSchema,
    # Plan schemas (aliases)
    PlanCreate,
    PlanUpdate,
    PlanDetail,
    # Version schemas (aliases)
    PlanVersionCreate,
    PlanVersionDetail,
    # Review schemas
    ReviewCreate,
    ReviewDecision,
    BCReviewDecision,
    # Attachment schemas
    AttachmentMeta,
    # Export schemas
    ExportRequest,
    # Process schemas with RTO/RPO
    BCProcessCreate,
    BCProcessUpdate,
    BCProcessDetail,
    # Status transition
    PlanStatusTransition,
    BCPlanListStatus,
)


class TestTemplateSchemas:
    """Test template-related schema validation."""
    
    def test_field_schema_valid(self):
        """Test valid field schema creation."""
        field = FieldSchema(
            field_id="test_field",
            label="Test Field",
            field_type="text",
            required=True,
            help_text="Test help text",
        )
        assert field.field_id == "test_field"
        assert field.label == "Test Field"
        assert field.required is True
    
    def test_field_schema_requires_field_id(self):
        """Test that field_id is required."""
        with pytest.raises(ValidationError) as exc_info:
            FieldSchema(
                label="Test Field",
                field_type="text",
            )
        assert "field_id" in str(exc_info.value)
    
    def test_field_schema_min_length_validation(self):
        """Test field_id minimum length validation."""
        with pytest.raises(ValidationError) as exc_info:
            FieldSchema(
                field_id="",  # Empty string
                label="Test Field",
                field_type="text",
            )
        assert "field_id" in str(exc_info.value)
    
    def test_section_schema_valid(self):
        """Test valid section schema creation."""
        section = SectionSchema(
            section_id="test_section",
            title="Test Section",
            description="Test description",
            order=1,
            fields=[],
        )
        assert section.section_id == "test_section"
        assert section.title == "Test Section"
        assert section.order == 1
    
    def test_section_schema_with_fields(self):
        """Test section schema with nested fields."""
        field1 = FieldSchema(
            field_id="field1",
            label="Field 1",
            field_type="text",
        )
        field2 = FieldSchema(
            field_id="field2",
            label="Field 2",
            field_type="integer",
        )
        section = SectionSchema(
            section_id="section1",
            title="Section 1",
            order=1,
            fields=[field1, field2],
        )
        assert len(section.fields) == 2
        assert section.fields[0].field_id == "field1"
    
    def test_section_schema_order_non_negative(self):
        """Test that section order must be non-negative."""
        with pytest.raises(ValidationError) as exc_info:
            SectionSchema(
                section_id="section1",
                title="Section 1",
                order=-1,  # Negative order
            )
        assert "order" in str(exc_info.value)
    
    def test_template_schema_valid(self):
        """Test valid template schema creation."""
        template = TemplateSchema(
            name="Test Template",
            version="1.0",
            description="Test description",
            is_default=False,
            sections=[],
        )
        assert template.name == "Test Template"
        assert template.version == "1.0"
        assert template.is_default is False
    
    def test_template_schema_with_sections(self):
        """Test template schema with nested sections."""
        section = SectionSchema(
            section_id="section1",
            title="Section 1",
            order=1,
        )
        template = TemplateSchema(
            name="Test Template",
            version="1.0",
            sections=[section],
        )
        assert len(template.sections) == 1
        assert template.sections[0].section_id == "section1"


class TestPlanSchemas:
    """Test plan schema aliases and validation."""
    
    def test_plan_create_alias(self):
        """Test that PlanCreate is properly aliased."""
        plan = PlanCreate(
            title="Test Plan",
        )
        assert plan.title == "Test Plan"
    
    def test_plan_update_alias(self):
        """Test that PlanUpdate is properly aliased."""
        update = PlanUpdate(title="Updated Title")
        assert update.title == "Updated Title"


class TestVersionSchemas:
    """Test version schema aliases."""
    
    def test_version_create_alias(self):
        """Test that PlanVersionCreate is properly aliased."""
        version = PlanVersionCreate(
            summary_change_note="Initial version",
        )
        assert version.summary_change_note == "Initial version"


class TestReviewSchemas:
    """Test review-related schema validation."""
    
    def test_review_create_valid(self):
        """Test valid review creation."""
        review = ReviewCreate(
            plan_id=1,
            reviewer_user_ids=[2, 3, 4],
            notes="Please review",
        )
        assert review.plan_id == 1
        assert len(review.reviewer_user_ids) == 3
    
    def test_review_create_requires_plan_id(self):
        """Test that plan_id is required."""
        with pytest.raises(ValidationError) as exc_info:
            ReviewCreate(
                reviewer_user_ids=[2, 3],
            )
        assert "plan_id" in str(exc_info.value)
    
    def test_review_create_requires_reviewers(self):
        """Test that at least one reviewer is required."""
        with pytest.raises(ValidationError) as exc_info:
            ReviewCreate(
                plan_id=1,
                reviewer_user_ids=[],  # Empty list
            )
        assert "reviewer_user_ids" in str(exc_info.value)
    
    def test_review_create_plan_id_positive(self):
        """Test that plan_id must be positive."""
        with pytest.raises(ValidationError) as exc_info:
            ReviewCreate(
                plan_id=0,  # Not positive
                reviewer_user_ids=[2],
            )
        assert "plan_id" in str(exc_info.value)
    
    def test_review_create_with_utc_due_date(self):
        """Test review creation with UTC due date."""
        due_date = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        review = ReviewCreate(
            plan_id=1,
            reviewer_user_ids=[2],
            due_date=due_date,
        )
        assert review.due_date == due_date
        assert review.due_date.tzinfo is not None
    
    def test_review_create_rejects_naive_datetime(self):
        """Test that naive datetime (without timezone) is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ReviewCreate(
                plan_id=1,
                reviewer_user_ids=[2],
                due_date=datetime(2024, 12, 31, 23, 59, 59),  # No timezone
            )
        assert "timezone" in str(exc_info.value).lower()
    
    def test_review_decision_valid(self):
        """Test valid review decision creation."""
        decided_at = datetime.now(timezone.utc)
        decision = ReviewDecision(
            review_id=1,
            decision=BCReviewDecision.APPROVED,
            notes="Looks good",
            decided_at=decided_at,
        )
        assert decision.review_id == 1
        assert decision.decision == BCReviewDecision.APPROVED
        assert decision.decided_at.tzinfo is not None
    
    def test_review_decision_requires_notes(self):
        """Test that notes are required."""
        with pytest.raises(ValidationError) as exc_info:
            ReviewDecision(
                review_id=1,
                decision=BCReviewDecision.APPROVED,
                notes="",  # Empty notes
            )
        assert "notes" in str(exc_info.value)
    
    def test_review_decision_rejects_naive_datetime(self):
        """Test that naive datetime is rejected for decided_at."""
        with pytest.raises(ValidationError) as exc_info:
            ReviewDecision(
                review_id=1,
                decision=BCReviewDecision.APPROVED,
                notes="Approved",
                decided_at=datetime(2024, 1, 15, 10, 30),  # No timezone
            )
        assert "timezone" in str(exc_info.value).lower()


class TestAttachmentSchemas:
    """Test attachment metadata schema validation."""
    
    def test_attachment_meta_valid(self):
        """Test valid attachment metadata creation."""
        attachment = AttachmentMeta(
            plan_id=1,
            file_name="test.pdf",
            content_type="application/pdf",
            size_bytes=1024,
        )
        assert attachment.plan_id == 1
        assert attachment.file_name == "test.pdf"
        assert attachment.size_bytes == 1024
    
    def test_attachment_meta_size_non_negative(self):
        """Test that size_bytes must be non-negative."""
        with pytest.raises(ValidationError) as exc_info:
            AttachmentMeta(
                plan_id=1,
                file_name="test.pdf",
                size_bytes=-100,  # Negative size
            )
        assert "size_bytes" in str(exc_info.value)
    
    def test_attachment_meta_with_tags(self):
        """Test attachment with tags."""
        attachment = AttachmentMeta(
            plan_id=1,
            file_name="contacts.pdf",
            tags=["emergency", "contacts"],
        )
        assert len(attachment.tags) == 2
        assert "emergency" in attachment.tags
    
    def test_attachment_meta_with_utc_timestamp(self):
        """Test attachment with UTC upload timestamp."""
        uploaded_at = datetime.now(timezone.utc)
        attachment = AttachmentMeta(
            plan_id=1,
            file_name="test.pdf",
            uploaded_at_utc=uploaded_at,
        )
        assert attachment.uploaded_at_utc == uploaded_at
        assert attachment.uploaded_at_utc.tzinfo is not None
    
    def test_attachment_meta_rejects_naive_datetime(self):
        """Test that naive datetime is rejected for uploaded_at_utc."""
        with pytest.raises(ValidationError) as exc_info:
            AttachmentMeta(
                plan_id=1,
                file_name="test.pdf",
                uploaded_at_utc=datetime(2024, 1, 15, 10, 30),  # No timezone
            )
        assert "timezone" in str(exc_info.value).lower()


class TestExportSchemas:
    """Test export request schema."""
    
    def test_export_request_alias(self):
        """Test that ExportRequest is properly aliased."""
        request = ExportRequest(
            version_id=1,
            include_attachments=True,
        )
        assert request.version_id == 1
        assert request.include_attachments is True


class TestProcessSchemasWithRTORPO:
    """Test process schemas with RTO/RPO validation."""
    
    def test_process_create_valid(self):
        """Test valid process creation with RTO/RPO."""
        process = BCProcessCreate(
            plan_id=1,
            name="Email Service",
            rto_minutes=240,
            rpo_minutes=60,
            mtpd_minutes=480,
            impact_rating="critical",
        )
        assert process.rto_minutes == 240
        assert process.rpo_minutes == 60
        assert process.mtpd_minutes == 480
    
    def test_process_create_rto_non_negative(self):
        """Test that RTO must be non-negative."""
        with pytest.raises(ValidationError) as exc_info:
            BCProcessCreate(
                plan_id=1,
                name="Test Process",
                rto_minutes=-10,  # Negative RTO
            )
        error_str = str(exc_info.value).lower()
        assert "rto_minutes" in error_str
        # Check for validation error related to minimum value
        assert ("greater than or equal" in error_str or "non-negative" in error_str)
    
    def test_process_create_rpo_non_negative(self):
        """Test that RPO must be non-negative."""
        with pytest.raises(ValidationError) as exc_info:
            BCProcessCreate(
                plan_id=1,
                name="Test Process",
                rpo_minutes=-5,  # Negative RPO
            )
        error_str = str(exc_info.value).lower()
        assert "rpo_minutes" in error_str
        # Check for validation error related to minimum value
        assert ("greater than or equal" in error_str or "non-negative" in error_str)
    
    def test_process_create_mtpd_non_negative(self):
        """Test that MTPD must be non-negative."""
        with pytest.raises(ValidationError) as exc_info:
            BCProcessCreate(
                plan_id=1,
                name="Test Process",
                mtpd_minutes=-20,  # Negative MTPD
            )
        error_str = str(exc_info.value).lower()
        assert "mtpd_minutes" in error_str
        # Check for validation error related to minimum value
        assert ("greater than or equal" in error_str or "non-negative" in error_str)
    
    def test_process_create_zero_values_allowed(self):
        """Test that zero values are allowed for RTO/RPO/MTPD."""
        process = BCProcessCreate(
            plan_id=1,
            name="Test Process",
            rto_minutes=0,
            rpo_minutes=0,
            mtpd_minutes=0,
        )
        assert process.rto_minutes == 0
        assert process.rpo_minutes == 0
        assert process.mtpd_minutes == 0
    
    def test_process_update_rto_rpo_validation(self):
        """Test RTO/RPO validation in update schema."""
        update = BCProcessUpdate(
            rto_minutes=120,
            rpo_minutes=30,
        )
        assert update.rto_minutes == 120
        assert update.rpo_minutes == 30
    
    def test_process_update_rejects_negative_rto(self):
        """Test that update rejects negative RTO."""
        with pytest.raises(ValidationError) as exc_info:
            BCProcessUpdate(
                rto_minutes=-10,
            )
        assert "rto_minutes" in str(exc_info.value)
    
    def test_process_update_rejects_negative_rpo(self):
        """Test that update rejects negative RPO."""
        with pytest.raises(ValidationError) as exc_info:
            BCProcessUpdate(
                rpo_minutes=-5,
            )
        assert "rpo_minutes" in str(exc_info.value)
    
    def test_process_detail_with_utc_timestamps(self):
        """Test process detail with UTC timestamps."""
        created_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        updated_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        
        process = BCProcessDetail(
            id=1,
            plan_id=1,
            name="Test Process",
            rto_minutes=240,
            rpo_minutes=60,
            created_at=created_at,
            updated_at=updated_at,
        )
        assert process.created_at.tzinfo is not None
        assert process.updated_at.tzinfo is not None
    
    def test_process_detail_rejects_naive_datetime(self):
        """Test that naive datetime is rejected for timestamps."""
        with pytest.raises(ValidationError) as exc_info:
            BCProcessDetail(
                id=1,
                plan_id=1,
                name="Test Process",
                created_at=datetime(2024, 1, 1),  # No timezone
                updated_at=datetime(2024, 1, 15),
            )
        assert "timezone" in str(exc_info.value).lower()


class TestStatusTransitionValidation:
    """Test plan status transition validation."""
    
    def test_valid_transition_draft_to_in_review(self):
        """Test valid transition from draft to in_review."""
        transition = PlanStatusTransition(
            current_status=BCPlanListStatus.DRAFT,
            new_status=BCPlanListStatus.IN_REVIEW,
        )
        assert transition.new_status == BCPlanListStatus.IN_REVIEW
    
    def test_valid_transition_in_review_to_approved(self):
        """Test valid transition from in_review to approved."""
        transition = PlanStatusTransition(
            current_status=BCPlanListStatus.IN_REVIEW,
            new_status=BCPlanListStatus.APPROVED,
        )
        assert transition.new_status == BCPlanListStatus.APPROVED
    
    def test_valid_transition_in_review_to_draft(self):
        """Test valid transition from in_review back to draft."""
        transition = PlanStatusTransition(
            current_status=BCPlanListStatus.IN_REVIEW,
            new_status=BCPlanListStatus.DRAFT,
        )
        assert transition.new_status == BCPlanListStatus.DRAFT
    
    def test_valid_transition_approved_to_archived(self):
        """Test valid transition from approved to archived."""
        transition = PlanStatusTransition(
            current_status=BCPlanListStatus.APPROVED,
            new_status=BCPlanListStatus.ARCHIVED,
        )
        assert transition.new_status == BCPlanListStatus.ARCHIVED
    
    def test_valid_transition_archived_to_draft(self):
        """Test valid transition from archived to draft (reactivation)."""
        transition = PlanStatusTransition(
            current_status=BCPlanListStatus.ARCHIVED,
            new_status=BCPlanListStatus.DRAFT,
        )
        assert transition.new_status == BCPlanListStatus.DRAFT
    
    def test_same_status_allowed(self):
        """Test that transitioning to the same status is allowed."""
        transition = PlanStatusTransition(
            current_status=BCPlanListStatus.DRAFT,
            new_status=BCPlanListStatus.DRAFT,
        )
        assert transition.new_status == BCPlanListStatus.DRAFT
    
    def test_invalid_transition_draft_to_approved(self):
        """Test invalid transition from draft directly to approved."""
        with pytest.raises(ValidationError) as exc_info:
            PlanStatusTransition(
                current_status=BCPlanListStatus.DRAFT,
                new_status=BCPlanListStatus.APPROVED,
            )
        error_msg = str(exc_info.value)
        assert "Invalid status transition" in error_msg
        assert "draft" in error_msg
        assert "approved" in error_msg
    
    def test_invalid_transition_draft_to_archived(self):
        """Test invalid transition from draft to archived."""
        with pytest.raises(ValidationError) as exc_info:
            PlanStatusTransition(
                current_status=BCPlanListStatus.DRAFT,
                new_status=BCPlanListStatus.ARCHIVED,
            )
        assert "Invalid status transition" in str(exc_info.value)
    
    def test_invalid_transition_approved_to_draft(self):
        """Test invalid transition from approved to draft."""
        with pytest.raises(ValidationError) as exc_info:
            PlanStatusTransition(
                current_status=BCPlanListStatus.APPROVED,
                new_status=BCPlanListStatus.DRAFT,
            )
        assert "Invalid status transition" in str(exc_info.value)
    
    def test_invalid_transition_approved_to_in_review(self):
        """Test invalid transition from approved to in_review."""
        with pytest.raises(ValidationError) as exc_info:
            PlanStatusTransition(
                current_status=BCPlanListStatus.APPROVED,
                new_status=BCPlanListStatus.IN_REVIEW,
            )
        assert "Invalid status transition" in str(exc_info.value)


class TestDateTimeValidation:
    """Test ISO 8601 UTC datetime validation across schemas."""
    
    def test_utc_datetime_formats(self):
        """Test various UTC datetime formats."""
        # Test with different UTC representations
        utc_times = [
            datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone(timedelta(0))),
        ]
        
        for utc_time in utc_times:
            review = ReviewCreate(
                plan_id=1,
                reviewer_user_ids=[2],
                due_date=utc_time,
            )
            assert review.due_date.tzinfo is not None
    
    def test_non_utc_timezone_accepted(self):
        """Test that non-UTC timezones are accepted (will be converted)."""
        # Create datetime with non-UTC timezone
        est = timezone(timedelta(hours=-5))
        time_est = datetime(2024, 1, 15, 10, 30, 0, tzinfo=est)
        
        review = ReviewCreate(
            plan_id=1,
            reviewer_user_ids=[2],
            due_date=time_est,
        )
        assert review.due_date.tzinfo is not None


class TestSchemaIntegration:
    """Test integration between related schemas."""
    
    def test_template_with_complete_structure(self):
        """Test creating a complete template with sections and fields."""
        field1 = FieldSchema(
            field_id="rto_minutes",
            label="Recovery Time Objective (minutes)",
            field_type="integer",
            required=True,
            validation_rules={"min": 0},
        )
        
        field2 = FieldSchema(
            field_id="rpo_minutes",
            label="Recovery Point Objective (minutes)",
            field_type="integer",
            required=True,
            validation_rules={"min": 0},
        )
        
        section = SectionSchema(
            section_id="recovery_objectives",
            title="Recovery Objectives",
            description="Define RTO and RPO",
            order=1,
            fields=[field1, field2],
        )
        
        template = TemplateSchema(
            name="Recovery Plan Template",
            version="2.0",
            description="Template for recovery planning",
            sections=[section],
        )
        
        assert len(template.sections) == 1
        assert len(template.sections[0].fields) == 2
        assert template.sections[0].fields[0].validation_rules["min"] == 0
    
    def test_process_with_all_recovery_objectives(self):
        """Test process with all recovery objectives set."""
        process = BCProcessCreate(
            plan_id=1,
            name="Critical Database",
            description="Primary customer database",
            rto_minutes=120,  # 2 hours
            rpo_minutes=15,   # 15 minutes
            mtpd_minutes=240, # 4 hours
            impact_rating="critical",
            dependencies_json={"services": ["storage", "network"]},
        )
        
        assert process.rto_minutes == 120
        assert process.rpo_minutes == 15
        assert process.mtpd_minutes == 240
        assert "services" in process.dependencies_json
