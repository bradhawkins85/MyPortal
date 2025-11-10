"""
Tests for BC7 service layer business logic.

Tests:
- Template schema resolution and content merging
- Status transition validation
- Version management and superseding
- Derived field computation
- Audit event logging
- Permission enforcement
- High-level workflow functions
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.services.bc_services import (
    ALLOWED_TRANSITIONS,
    validate_status_transition,
    resolve_template_and_merge_content,
    create_new_version,
    compute_highest_risk_rating,
    compute_unacknowledged_users,
    create_audit_event,
    check_plan_ownership,
    enforce_plan_access,
    can_user_approve_plan,
    submit_plan_for_review,
    approve_plan,
    archive_plan,
    _create_empty_content_from_schema,
    _merge_content_with_schema,
)
from app.schemas.bc5_models import BCPlanListStatus, BCUserRole, BCVersionStatus


# ============================================================================
# Status Transition Tests
# ============================================================================

def test_validate_status_transition_same_status():
    """Test that same status transition is allowed."""
    validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.DRAFT)
    # Should not raise


def test_validate_status_transition_draft_to_in_review():
    """Test valid transition from draft to in_review."""
    validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.IN_REVIEW)
    # Should not raise


def test_validate_status_transition_in_review_to_approved():
    """Test valid transition from in_review to approved."""
    validate_status_transition(BCPlanListStatus.IN_REVIEW, BCPlanListStatus.APPROVED)
    # Should not raise


def test_validate_status_transition_in_review_to_draft():
    """Test valid transition from in_review back to draft."""
    validate_status_transition(BCPlanListStatus.IN_REVIEW, BCPlanListStatus.DRAFT)
    # Should not raise


def test_validate_status_transition_approved_to_archived():
    """Test valid transition from approved to archived."""
    validate_status_transition(BCPlanListStatus.APPROVED, BCPlanListStatus.ARCHIVED)
    # Should not raise


def test_validate_status_transition_archived_to_draft():
    """Test valid transition from archived to draft (reactivation)."""
    validate_status_transition(BCPlanListStatus.ARCHIVED, BCPlanListStatus.DRAFT)
    # Should not raise


def test_validate_status_transition_invalid_draft_to_approved():
    """Test invalid transition from draft directly to approved."""
    with pytest.raises(HTTPException) as exc_info:
        validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.APPROVED)
    assert exc_info.value.status_code == 400
    assert "Invalid status transition" in exc_info.value.detail


def test_validate_status_transition_invalid_draft_to_archived():
    """Test invalid transition from draft directly to archived."""
    with pytest.raises(HTTPException) as exc_info:
        validate_status_transition(BCPlanListStatus.DRAFT, BCPlanListStatus.ARCHIVED)
    assert exc_info.value.status_code == 400
    assert "Invalid status transition" in exc_info.value.detail


# ============================================================================
# Template Schema Resolution Tests
# ============================================================================

def test_create_empty_content_from_schema():
    """Test creating empty content structure from template schema."""
    schema = {
        "sections": [
            {
                "section_id": "overview",
                "fields": [
                    {"field_id": "title", "default_value": "Untitled"},
                    {"field_id": "description", "default_value": None},
                ]
            },
            {
                "section_id": "recovery",
                "fields": [
                    {"field_id": "rto_minutes", "default_value": 0},
                ]
            }
        ]
    }
    
    result = _create_empty_content_from_schema(schema)
    
    assert "overview" in result
    assert result["overview"]["title"] == "Untitled"
    assert result["overview"]["description"] is None
    assert "recovery" in result
    assert result["recovery"]["rto_minutes"] == 0


def test_merge_content_with_schema_preserves_values():
    """Test that merging preserves existing content values."""
    schema = {
        "sections": [
            {
                "section_id": "overview",
                "fields": [
                    {"field_id": "title", "default_value": "Untitled"},
                    {"field_id": "description", "default_value": None},
                ]
            }
        ]
    }
    
    content = {
        "overview": {
            "title": "My Custom Title",
        }
    }
    
    result = _merge_content_with_schema(schema, content)
    
    assert result["overview"]["title"] == "My Custom Title"
    assert result["overview"]["description"] is None


def test_merge_content_with_schema_fills_missing_fields():
    """Test that merging fills in missing fields with defaults."""
    schema = {
        "sections": [
            {
                "section_id": "overview",
                "fields": [
                    {"field_id": "title", "default_value": "Default Title"},
                    {"field_id": "description", "default_value": "Default Desc"},
                ]
            }
        ]
    }
    
    content = {
        "overview": {
            "title": "Custom Title",
            # description is missing
        }
    }
    
    result = _merge_content_with_schema(schema, content)
    
    assert result["overview"]["title"] == "Custom Title"
    assert result["overview"]["description"] == "Default Desc"


@pytest.mark.asyncio
async def test_resolve_template_and_merge_content_template_not_found():
    """Test that resolving non-existent template raises error."""
    with patch("app.services.bc_services.bc_repo.get_template_by_id", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await resolve_template_and_merge_content(999, None)
        assert exc_info.value.status_code == 404
        assert "Template 999 not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_resolve_template_and_merge_content_no_plan_content():
    """Test resolving template without plan content creates empty structure."""
    template = {
        "id": 1,
        "schema_json": {
            "sections": [
                {
                    "section_id": "overview",
                    "fields": [
                        {"field_id": "title", "default_value": "New Plan"}
                    ]
                }
            ]
        }
    }
    
    with patch("app.services.bc_services.bc_repo.get_template_by_id", return_value=template):
        result = await resolve_template_and_merge_content(1, None)
        assert "overview" in result
        assert result["overview"]["title"] == "New Plan"


@pytest.mark.asyncio
async def test_resolve_template_and_merge_content_with_plan_content():
    """Test resolving template with existing plan content."""
    template = {
        "id": 1,
        "schema_json": {
            "sections": [
                {
                    "section_id": "overview",
                    "fields": [
                        {"field_id": "title", "default_value": "Default"},
                        {"field_id": "description", "default_value": None},
                    ]
                }
            ]
        }
    }
    
    plan_content = {
        "overview": {
            "title": "My Plan"
        }
    }
    
    with patch("app.services.bc_services.bc_repo.get_template_by_id", return_value=template):
        result = await resolve_template_and_merge_content(1, plan_content)
        assert result["overview"]["title"] == "My Plan"
        assert result["overview"]["description"] is None


# ============================================================================
# Version Management Tests
# ============================================================================

@pytest.mark.asyncio
async def test_create_new_version_plan_not_found():
    """Test creating version for non-existent plan raises error."""
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await create_new_version(999, {}, 1)
        assert exc_info.value.status_code == 404
        assert "Plan 999 not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_new_version_first_version():
    """Test creating the first version of a plan."""
    plan = {"id": 1, "status": "draft"}
    new_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "status": "active"
    }
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.get_next_version_number", return_value=1), \
         patch("app.services.bc_services.bc_repo.create_version", return_value=new_version) as mock_create, \
         patch("app.services.bc_services.bc_repo.update_plan", return_value=plan) as mock_update, \
         patch("app.services.bc_services.bc_repo.list_plan_versions", return_value=[]):
        
        result = await create_new_version(1, {"content": "test"}, 1, "Initial version")
        
        # Should create version 1
        mock_create.assert_called_once()
        args = mock_create.call_args[1]
        assert args["version_number"] == 1
        assert args["status"] == BCVersionStatus.ACTIVE.value
        
        # Should update plan's current_version_id
        mock_update.assert_called_once()


@pytest.mark.asyncio
async def test_create_new_version_increments_version_number():
    """Test that new versions increment the version number."""
    plan = {"id": 1, "status": "draft"}
    existing_versions = [
        {"id": 1, "version_number": 1, "status": "superseded"},
        {"id": 2, "version_number": 2, "status": "active"},
    ]
    new_version = {
        "id": 3,
        "plan_id": 1,
        "version_number": 3,
        "status": "active"
    }
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.get_next_version_number", return_value=3), \
         patch("app.services.bc_services.bc_repo.create_version", return_value=new_version) as mock_create, \
         patch("app.services.bc_services.bc_repo.list_plan_versions", return_value=existing_versions), \
         patch("app.services.bc_services.bc_repo.update_plan", return_value=plan):
        
        result = await create_new_version(1, {"content": "test"}, 1)
        
        # Should create version 3
        args = mock_create.call_args[1]
        assert args["version_number"] == 3


@pytest.mark.asyncio
async def test_create_new_version_without_superseding():
    """Test creating version without superseding previous versions."""
    plan = {"id": 1, "status": "draft"}
    existing_versions = [
        {"id": 1, "version_number": 1, "status": "active"},
    ]
    new_version = {
        "id": 2,
        "plan_id": 1,
        "version_number": 2,
        "status": "active"
    }
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.get_next_version_number", return_value=2), \
         patch("app.services.bc_services.bc_repo.create_version", return_value=new_version), \
         patch("app.services.bc_services.bc_repo.list_plan_versions", return_value=existing_versions), \
         patch("app.services.bc_services.bc_repo.update_plan", return_value=plan):
        
        await create_new_version(1, {"content": "test"}, 1, supersede_previous=False)
        
        # Test passes if no exception is raised


# ============================================================================
# Derived Field Computation Tests
# ============================================================================

@pytest.mark.asyncio
async def test_compute_highest_risk_rating_no_risks():
    """Test computing risk rating when no risks exist."""
    with patch("app.services.bc_services.bc_repo.list_risks_by_plan", return_value=[]):
        result = await compute_highest_risk_rating(1)
        assert result is None


@pytest.mark.asyncio
async def test_compute_highest_risk_rating_single_risk():
    """Test computing risk rating with single risk."""
    risks = [{"rating": "high"}]
    
    with patch("app.services.bc_services.bc_repo.list_risks_by_plan", return_value=risks):
        result = await compute_highest_risk_rating(1)
        assert result == "high"


@pytest.mark.asyncio
async def test_compute_highest_risk_rating_multiple_risks():
    """Test computing highest risk rating from multiple risks."""
    risks = [
        {"rating": "low"},
        {"rating": "critical"},
        {"rating": "medium"},
        {"rating": "high"},
    ]
    
    with patch("app.services.bc_services.bc_repo.list_risks_by_plan", return_value=risks):
        result = await compute_highest_risk_rating(1)
        assert result == "critical"


@pytest.mark.asyncio
async def test_compute_highest_risk_rating_case_insensitive():
    """Test that risk rating computation is case insensitive."""
    risks = [
        {"rating": "Low"},
        {"rating": "HIGH"},
    ]
    
    with patch("app.services.bc_services.bc_repo.list_risks_by_plan", return_value=risks):
        result = await compute_highest_risk_rating(1)
        assert result == "high"


@pytest.mark.asyncio
async def test_compute_unacknowledged_users_no_current_version():
    """Test computing unacknowledged users when plan has no current version."""
    plan = {"id": 1, "current_version_id": None}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        result = await compute_unacknowledged_users(1)
        assert result == []


@pytest.mark.asyncio
async def test_compute_unacknowledged_users_with_version():
    """Test computing unacknowledged users for specific version."""
    plan = {"id": 1, "current_version_id": 1}
    version = {"id": 1, "version_number": 2}
    acks = [
        {"user_id": 1, "ack_version_number": 2},
        {"user_id": 2, "ack_version_number": 1},  # Old version
    ]
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.get_version_by_id", return_value=version), \
         patch("app.services.bc_services.bc_repo.list_plan_acknowledgments", return_value=acks):
        
        result = await compute_unacknowledged_users(1)
        # Currently returns empty list as we don't have user list logic
        assert result == []


# ============================================================================
# Audit Event Tests
# ============================================================================

@pytest.mark.asyncio
async def test_create_audit_event():
    """Test creating an audit event."""
    with patch("app.services.bc_services.bc_repo.create_audit_entry") as mock_create:
        await create_audit_event(
            plan_id=1,
            action="updated",
            actor_user_id=5,
            details={"field": "status", "old": "draft", "new": "in_review"}
        )
        
        mock_create.assert_called_once_with(
            plan_id=1,
            action="updated",
            actor_user_id=5,
            details_json={"field": "status", "old": "draft", "new": "in_review"}
        )


# ============================================================================
# Permission Tests
# ============================================================================

@pytest.mark.asyncio
async def test_check_plan_ownership_is_owner():
    """Test checking plan ownership when user is owner."""
    plan = {"id": 1, "owner_user_id": 5}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        result = await check_plan_ownership(1, 5)
        assert result is True


@pytest.mark.asyncio
async def test_check_plan_ownership_not_owner():
    """Test checking plan ownership when user is not owner."""
    plan = {"id": 1, "owner_user_id": 5}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        result = await check_plan_ownership(1, 10)
        assert result is False


@pytest.mark.asyncio
async def test_check_plan_ownership_plan_not_found():
    """Test checking ownership when plan doesn't exist."""
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=None):
        result = await check_plan_ownership(999, 5)
        assert result is False


@pytest.mark.asyncio
async def test_enforce_plan_access_super_admin():
    """Test that super admins always have access."""
    await enforce_plan_access(1, 5, BCUserRole.VIEWER, is_super_admin=True)
    # Should not raise


@pytest.mark.asyncio
async def test_enforce_plan_access_owner():
    """Test that plan owners always have access."""
    plan = {"id": 1, "owner_user_id": 5}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        await enforce_plan_access(1, 5, BCUserRole.VIEWER, is_super_admin=False)
        # Should not raise


@pytest.mark.asyncio
async def test_enforce_plan_access_not_owner_not_admin():
    """Test that non-owners without admin access are denied."""
    plan = {"id": 1, "owner_user_id": 10}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        with pytest.raises(HTTPException) as exc_info:
            await enforce_plan_access(1, 5, BCUserRole.VIEWER, is_super_admin=False)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_can_user_approve_plan_super_admin():
    """Test that super admins can approve any plan."""
    result = await can_user_approve_plan(1, 5, BCUserRole.VIEWER, is_super_admin=True)
    assert result is True


@pytest.mark.asyncio
async def test_can_user_approve_plan_approver_not_owner():
    """Test that approvers can approve plans they don't own."""
    plan = {"id": 1, "owner_user_id": 10}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        result = await can_user_approve_plan(1, 5, BCUserRole.APPROVER, is_super_admin=False)
        assert result is True


@pytest.mark.asyncio
async def test_can_user_approve_plan_owner_cannot_approve():
    """Test that plan owners cannot approve their own plans."""
    plan = {"id": 1, "owner_user_id": 5}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        result = await can_user_approve_plan(1, 5, BCUserRole.APPROVER, is_super_admin=False)
        assert result is False


@pytest.mark.asyncio
async def test_can_user_approve_plan_editor_cannot_approve():
    """Test that editors cannot approve plans."""
    plan = {"id": 1, "owner_user_id": 10}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan):
        result = await can_user_approve_plan(1, 5, BCUserRole.EDITOR, is_super_admin=False)
        assert result is False


# ============================================================================
# Workflow Function Tests
# ============================================================================

@pytest.mark.asyncio
async def test_submit_plan_for_review():
    """Test submitting a plan for review."""
    plan = {"id": 1, "status": "draft"}
    review = {"id": 1, "plan_id": 1, "reviewer_user_id": 10}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.update_plan", return_value=plan), \
         patch("app.services.bc_services.bc_repo.create_review", return_value=review) as mock_create_review, \
         patch("app.services.bc_services.create_audit_event") as mock_audit:
        
        result = await submit_plan_for_review(1, [10, 20], 5, "Please review")
        
        # Should create review for each reviewer
        assert mock_create_review.call_count == 2
        
        # Should create audit event
        mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_approve_plan():
    """Test approving a plan."""
    plan = {"id": 1, "status": "in_review"}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.update_review_decision"), \
         patch("app.services.bc_services.bc_repo.update_plan", return_value=plan) as mock_update, \
         patch("app.services.bc_services.create_audit_event") as mock_audit:
        
        result = await approve_plan(1, 1, 10, "Looks good")
        
        # Should update plan status to approved
        mock_update.assert_called_once()
        args = mock_update.call_args[1]
        assert args["status"] == BCPlanListStatus.APPROVED.value
        
        # Should create audit event
        mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_archive_plan():
    """Test archiving a plan."""
    plan = {"id": 1, "status": "approved"}
    
    with patch("app.services.bc_services.bc_repo.get_plan_by_id", return_value=plan), \
         patch("app.services.bc_services.bc_repo.update_plan", return_value=plan) as mock_update, \
         patch("app.services.bc_services.create_audit_event") as mock_audit:
        
        result = await archive_plan(1, 10, "No longer needed")
        
        # Should update plan status to archived
        mock_update.assert_called_once()
        args = mock_update.call_args[1]
        assert args["status"] == BCPlanListStatus.ARCHIVED.value
        
        # Should create audit event
        mock_audit.assert_called_once()
        audit_args = mock_audit.call_args[1]
        assert audit_args["details"]["reason"] == "No longer needed"
