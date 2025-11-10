"""
Test BC18: Plan creation from default template with automatic version initialization.

These tests verify that users can create BC plans from the default template
with editable sections matching the template structure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.bc5 import create_plan
from app.schemas.bc5_models import BCPlanCreate, BCPlanListStatus


@pytest.mark.anyio
async def test_create_plan_from_template_creates_initial_version():
    """Test that creating a plan from a template automatically creates version 1."""
    from app.services.bcp_template import get_default_government_bcp_template
    
    mock_user = {"id": 1, "email": "editor@example.com", "is_super_admin": False}
    plan_data = BCPlanCreate(
        title="Test BCP from Template",
        status=BCPlanListStatus.DRAFT,
        org_id=1,
        template_id=1,
    )
    
    # Get the default template schema
    template_schema = get_default_government_bcp_template()
    
    mock_template = {
        "id": 1,
        "name": "Government BCP Template",
        "version": "1.0",
        "is_default": True,
        "schema_json": template_schema.model_dump(),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    mock_plan = {
        "id": 1,
        "title": "Test BCP from Template",
        "owner_user_id": 1,
        "status": "draft",
        "org_id": 1,
        "template_id": 1,
        "current_version_id": None,
        "approved_at_utc": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "status": "active",
        "authored_by_user_id": 1,
        "authored_at_utc": datetime.now(timezone.utc),
        "summary_change_note": "Initial version created from template",
        "content_json": {},
    }
    
    mock_plan_with_version = mock_plan.copy()
    mock_plan_with_version["current_version_id"] = 1
    
    with patch("app.api.routes.bc5.bc_repo.get_template_by_id", return_value=mock_template):
        with patch("app.api.routes.bc5.bc_repo.create_plan", return_value=mock_plan):
            with patch("app.api.routes.bc5.bc_repo.create_version", return_value=mock_version):
                with patch("app.api.routes.bc5.bc_repo.update_plan", return_value=mock_plan_with_version):
                    with patch("app.api.routes.bc5.bc_repo.get_plan_by_id", return_value=mock_plan_with_version):
                        with patch("app.api.routes.bc5.bc_repo.create_audit_entry"):
                            with patch("app.api.routes.bc5._enrich_user_name", new=AsyncMock()):
                                with patch("app.api.routes.bc5._enrich_template", new=AsyncMock()):
                                    with patch("app.api.routes.bc5._enrich_current_version", new=AsyncMock()):
                                        result = await create_plan(plan_data=plan_data, current_user=mock_user)
                                        
                                        # Verify plan was created
                                        assert result.title == "Test BCP from Template"
                                        assert result.id == 1
                                        assert result.current_version_id == 1


@pytest.mark.anyio
async def test_create_plan_from_template_initializes_sections():
    """Test that initial version content has sections matching the template."""
    from app.services.bcp_template import get_default_government_bcp_template
    from app.services.bc_services import _create_empty_content_from_schema
    
    # Get the default template schema
    template_schema = get_default_government_bcp_template()
    template_dict = template_schema.model_dump()
    
    # Create empty content from schema
    content = _create_empty_content_from_schema(template_dict)
    
    # Verify sections are initialized
    assert isinstance(content, dict)
    
    # Check that major sections exist
    expected_sections = [
        "plan_overview",
        "governance_roles",
        "business_impact_analysis",
        "risk_assessment",
        "recovery_strategies",
        "incident_response",
        "communications_plan",
        "it_systems_recovery",
        "testing_exercises",
        "maintenance_review",
        "appendices",
        "revision_history",
    ]
    
    for section_id in expected_sections:
        assert section_id in content, f"Section {section_id} not found in content"
        assert isinstance(content[section_id], dict)


@pytest.mark.anyio
async def test_create_plan_from_template_includes_required_fields():
    """Test that initial version includes all required fields from template."""
    from app.services.bcp_template import get_default_government_bcp_template
    from app.services.bc_services import _create_empty_content_from_schema
    
    # Get the default template schema
    template_schema = get_default_government_bcp_template()
    template_dict = template_schema.model_dump()
    
    # Create empty content from schema
    content = _create_empty_content_from_schema(template_dict)
    
    # Check plan_overview section has expected fields
    plan_overview = content.get("plan_overview", {})
    assert "purpose" in plan_overview
    assert "scope" in plan_overview
    assert "objectives" in plan_overview
    assert "assumptions" in plan_overview


@pytest.mark.anyio
async def test_create_plan_without_template_no_version():
    """Test that creating a plan without a template doesn't create a version."""
    mock_user = {"id": 1, "email": "editor@example.com", "is_super_admin": False}
    plan_data = BCPlanCreate(
        title="Test Plan without Template",
        status=BCPlanListStatus.DRAFT,
        org_id=1,
        template_id=None,  # No template
    )
    
    mock_plan = {
        "id": 1,
        "title": "Test Plan without Template",
        "owner_user_id": 1,
        "status": "draft",
        "org_id": 1,
        "template_id": None,
        "current_version_id": None,
        "approved_at_utc": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    with patch("app.api.routes.bc5.bc_repo.create_plan", return_value=mock_plan):
        with patch("app.api.routes.bc5.bc_repo.create_audit_entry"):
            with patch("app.api.routes.bc5._enrich_user_name", new=AsyncMock()):
                with patch("app.api.routes.bc5._enrich_template", new=AsyncMock()):
                    with patch("app.api.routes.bc5._enrich_current_version", new=AsyncMock()):
                        result = await create_plan(plan_data=plan_data, current_user=mock_user)
                        
                        # Verify plan was created but no version
                        assert result.title == "Test Plan without Template"
                        assert result.current_version_id is None


@pytest.mark.anyio
async def test_create_plan_with_invalid_template_raises_error():
    """Test that creating a plan with non-existent template raises 404."""
    mock_user = {"id": 1, "email": "editor@example.com", "is_super_admin": False}
    plan_data = BCPlanCreate(
        title="Test Plan",
        status=BCPlanListStatus.DRAFT,
        org_id=1,
        template_id=999,  # Non-existent template
    )
    
    with patch("app.api.routes.bc5.bc_repo.get_template_by_id", return_value=None):
        with pytest.raises(HTTPException) as exc_info:
            await create_plan(plan_data=plan_data, current_user=mock_user)
        
        assert exc_info.value.status_code == 404
        assert "Template not found" in str(exc_info.value.detail)


@pytest.mark.anyio
async def test_create_empty_content_from_schema_handles_empty_schema():
    """Test that _create_empty_content_from_schema handles empty schema gracefully."""
    from app.services.bc_services import _create_empty_content_from_schema
    
    # Empty schema
    empty_schema = {}
    content = _create_empty_content_from_schema(empty_schema)
    assert content == {}
    
    # Schema with no sections
    schema_no_sections = {"metadata": {"name": "Test"}}
    content = _create_empty_content_from_schema(schema_no_sections)
    assert content == {}


@pytest.mark.anyio
async def test_create_empty_content_handles_field_defaults():
    """Test that _create_empty_content_from_schema uses default values when provided."""
    from app.services.bc_services import _create_empty_content_from_schema
    
    schema = {
        "sections": [
            {
                "section_id": "test_section",
                "fields": [
                    {
                        "field_id": "field_with_default",
                        "default_value": "Default Value",
                    },
                    {
                        "field_id": "field_without_default",
                    },
                ]
            }
        ]
    }
    
    content = _create_empty_content_from_schema(schema)
    
    assert content["test_section"]["field_with_default"] == "Default Value"
    assert content["test_section"]["field_without_default"] is None
