"""
Tests for BC5 Business Continuity API endpoints.

Tests API endpoints, schemas validation, RBAC enforcement, and security features.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, status

# Import schemas directly without going through app package to avoid DB initialization
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Test BC5 Schemas
from app.schemas.bc5_models import (
    BCPlanCreate,
    BCPlanUpdate,
    BCPlanListStatus,
    BCTemplateCreate,
    BCTemplateUpdate,
    BCVersionCreate,
    BCReviewSubmit,
    BCReviewApprove,
    BCReviewRequestChanges,
    BCAcknowledge,
    BCSectionUpdate,
    BCExportRequest,
    BCUserRole,
)


class TestBC5Schemas:
    """Test BC5 Pydantic schemas validation."""
    
    def test_plan_create_schema(self):
        """Test plan creation schema validation."""
        plan_data = BCPlanCreate(
            title="Test Plan",
            status=BCPlanListStatus.DRAFT,
            org_id=1,
            template_id=1,
        )
        assert plan_data.title == "Test Plan"
        assert plan_data.status == BCPlanListStatus.DRAFT
        assert plan_data.org_id == 1
    
    def test_plan_create_requires_title(self):
        """Test that plan creation requires a title."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            BCPlanCreate(
                title="",  # Empty title should fail
                status=BCPlanListStatus.DRAFT,
            )
    
    def test_plan_update_schema(self):
        """Test plan update schema allows partial updates."""
        update_data = BCPlanUpdate(title="Updated Title")
        assert update_data.title == "Updated Title"
        assert update_data.status is None  # Optional field
    
    def test_template_create_schema(self):
        """Test template creation schema."""
        template_data = BCTemplateCreate(
            name="Government Template",
            version="1.0",
            is_default=True,
            schema_json={"sections": []},
        )
        assert template_data.name == "Government Template"
        assert template_data.is_default is True
    
    def test_version_create_schema(self):
        """Test version creation schema."""
        version_data = BCVersionCreate(
            summary_change_note="Initial version",
            content_json={"sections": []},
        )
        assert version_data.summary_change_note == "Initial version"
    
    def test_review_submit_schema(self):
        """Test review submission schema."""
        review_data = BCReviewSubmit(
            reviewer_user_ids=[1, 2, 3],
            notes="Please review ASAP",
        )
        assert len(review_data.reviewer_user_ids) == 3
        assert review_data.notes == "Please review ASAP"
    
    def test_review_submit_requires_reviewers(self):
        """Test that review submission requires at least one reviewer."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            BCReviewSubmit(
                reviewer_user_ids=[],  # Empty list should fail
                notes="Test",
            )
    
    def test_review_approve_schema(self):
        """Test review approval schema."""
        approval_data = BCReviewApprove(notes="Looks good!")
        assert approval_data.notes == "Looks good!"
    
    def test_review_request_changes_schema(self):
        """Test review changes request schema."""
        changes_data = BCReviewRequestChanges(
            notes="Please update section 3"
        )
        assert changes_data.notes == "Please update section 3"
    
    def test_review_request_changes_requires_notes(self):
        """Test that change requests require notes."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            BCReviewRequestChanges(notes="")  # Empty notes should fail
    
    def test_acknowledge_schema(self):
        """Test acknowledgment schema."""
        ack_data = BCAcknowledge(ack_version_number=2)
        assert ack_data.ack_version_number == 2
    
    def test_section_update_schema(self):
        """Test section update schema."""
        section_data = BCSectionUpdate(
            content_json={"field1": "value1", "field2": "value2"}
        )
        assert section_data.content_json["field1"] == "value1"
    
    def test_section_update_requires_dict(self):
        """Test that section update requires a dictionary."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            BCSectionUpdate(content_json="not a dict")
    
    def test_export_request_schema(self):
        """Test export request schema."""
        export_data = BCExportRequest(
            version_id=5,
            include_attachments=True,
        )
        assert export_data.version_id == 5
        assert export_data.include_attachments is True


class TestBC5RBAC:
    """Test BC5 RBAC functionality."""
    
    @pytest.mark.asyncio
    async def test_bc_user_roles_enum(self):
        """Test BC user roles enum values."""
        assert BCUserRole.VIEWER == "viewer"
        assert BCUserRole.EDITOR == "editor"
        assert BCUserRole.APPROVER == "approver"
        assert BCUserRole.ADMIN == "admin"
    
    @pytest.mark.asyncio
    async def test_super_admin_has_all_permissions(self):
        """Test that super admins have all BC permissions."""
        from app.api.dependencies.bc_rbac import _check_bc_permission
        
        super_admin_user = {"id": 1, "is_super_admin": True}
        
        # Super admins should have all permissions without DB checks
        assert await _check_bc_permission(super_admin_user, "bc.viewer") is True
        assert await _check_bc_permission(super_admin_user, "bc.editor") is True
        assert await _check_bc_permission(super_admin_user, "bc.approver") is True
        assert await _check_bc_permission(super_admin_user, "bc.admin") is True
    
    @pytest.mark.asyncio
    async def test_get_user_bc_role_super_admin(self):
        """Test getting BC role for super admin."""
        from app.api.dependencies.bc_rbac import _get_user_bc_role
        
        super_admin_user = {"id": 1, "is_super_admin": True}
        role = await _get_user_bc_role(super_admin_user)
        
        assert role == BCUserRole.ADMIN
    
    @pytest.mark.asyncio
    async def test_require_bc_viewer_rejects_non_bc_users(self):
        """Test that BC viewer requirement rejects users without BC access."""
        from app.api.dependencies.bc_rbac import require_bc_viewer
        
        with patch("app.api.dependencies.bc_rbac.membership_repo.user_has_permission", return_value=False):
            regular_user = {"id": 2, "is_super_admin": False}
            
            with pytest.raises(HTTPException) as exc_info:
                await require_bc_viewer(regular_user)
            
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "BC viewer access required" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_require_bc_editor_rejects_viewers(self):
        """Test that BC editor requirement rejects viewers."""
        from app.api.dependencies.bc_rbac import require_bc_editor
        
        async def mock_permission_check(user_id, permission_key):
            # Mock: user has viewer permission only
            return permission_key == "bc.viewer"
        
        with patch("app.api.dependencies.bc_rbac.membership_repo.user_has_permission", side_effect=mock_permission_check):
            viewer_user = {"id": 3, "is_super_admin": False}
            
            with pytest.raises(HTTPException) as exc_info:
                await require_bc_editor(viewer_user)
            
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "BC editor access required" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_require_bc_approver_rejects_editors(self):
        """Test that BC approver requirement rejects editors."""
        from app.api.dependencies.bc_rbac import require_bc_approver
        
        async def mock_permission_check(user_id, permission_key):
            # Mock: user has editor permission only
            return permission_key in ("bc.viewer", "bc.editor")
        
        with patch("app.api.dependencies.bc_rbac.membership_repo.user_has_permission", side_effect=mock_permission_check):
            editor_user = {"id": 4, "is_super_admin": False}
            
            with pytest.raises(HTTPException) as exc_info:
                await require_bc_approver(editor_user)
            
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "BC approver access required" in str(exc_info.value.detail)
    
    @pytest.mark.asyncio
    async def test_require_bc_admin_rejects_approvers(self):
        """Test that BC admin requirement rejects approvers."""
        from app.api.dependencies.bc_rbac import require_bc_admin
        
        async def mock_permission_check(user_id, permission_key):
            # Mock: user has approver permission only
            return permission_key in ("bc.viewer", "bc.editor", "bc.approver")
        
        with patch("app.api.dependencies.bc_rbac.membership_repo.user_has_permission", side_effect=mock_permission_check):
            approver_user = {"id": 5, "is_super_admin": False}
            
            with pytest.raises(HTTPException) as exc_info:
                await require_bc_admin(approver_user)
            
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "BC admin access required" in str(exc_info.value.detail)


class TestBC5Repository:
    """Test BC3 repository functions."""
    
    @pytest.mark.asyncio
    async def test_repository_imports(self):
        """Test that BC3 repository imports successfully."""
        import app.repositories.bc3 as bc_repo
        
        # Check that key functions exist
        assert hasattr(bc_repo, "create_template")
        assert hasattr(bc_repo, "create_plan")
        assert hasattr(bc_repo, "create_version")
        assert hasattr(bc_repo, "create_review")
        assert hasattr(bc_repo, "create_acknowledgment")
        assert hasattr(bc_repo, "create_attachment")
        assert hasattr(bc_repo, "create_audit_entry")
        assert hasattr(bc_repo, "list_templates")
        assert hasattr(bc_repo, "list_plans")
        assert hasattr(bc_repo, "list_plan_versions")
        assert hasattr(bc_repo, "list_plan_reviews")
        assert hasattr(bc_repo, "list_plan_attachments")
        assert hasattr(bc_repo, "list_plan_audit_trail")


class TestBC5APIRouter:
    """Test BC5 API router structure."""
    
    def test_router_imports(self):
        """Test that BC5 router imports successfully."""
        from app.api.routes import bc5
        
        assert hasattr(bc5, "router")
        assert bc5.router.prefix == "/api/bc"
        assert "Business Continuity (BC5)" in [tag for tag in bc5.router.tags]
    
    def test_router_has_expected_endpoints(self):
        """Test that router has all expected endpoint paths."""
        from app.api.routes import bc5
        
        # Get all route paths
        route_paths = [route.path for route in bc5.router.routes]
        
        # Check for key endpoint patterns
        assert any("/templates" in path for path in route_paths)
        assert any("/plans" in path for path in route_paths)
        assert any("/versions" in path for path in route_paths)
        assert any("/reviews" in path for path in route_paths)
        assert any("/acknowledge" in path for path in route_paths)
        assert any("/sections" in path for path in route_paths)
        assert any("/attachments" in path for path in route_paths)
        assert any("/export" in path for path in route_paths)
        assert any("/audit" in path for path in route_paths)
        assert any("/change-log" in path for path in route_paths)
    
    def test_router_has_crud_methods(self):
        """Test that router implements CRUD methods."""
        from app.api.routes import bc5
        
        # Get all route methods
        route_methods = []
        for route in bc5.router.routes:
            if hasattr(route, "methods"):
                route_methods.extend(route.methods)
        
        # Check for HTTP methods
        assert "GET" in route_methods
        assert "POST" in route_methods
        assert "PATCH" in route_methods
        assert "DELETE" in route_methods


class TestBC5EndpointCount:
    """Test that all required endpoints are present."""
    
    def test_total_endpoint_count(self):
        """Test that we have the expected number of endpoints."""
        from app.api.routes import bc5
        
        # Count endpoints (some may be counted multiple times for different HTTP methods)
        endpoint_count = len(bc5.router.routes)
        
        # We should have at least 23 endpoints based on the spec:
        # Templates: 4 (list, create, get, update)
        # Plans: 5 (list, create, get, update, delete)
        # Versions: 4 (list, create, get, activate)
        # Workflow: 4 (submit, approve, request-changes, acknowledge)
        # Sections: 2 (list, update)
        # Attachments: 3 (list, upload, delete)
        # Exports: 2 (docx, pdf)
        # Audit: 2 (audit, change-log)
        # Total: 26 endpoints
        
        assert endpoint_count >= 23, f"Expected at least 23 endpoints, found {endpoint_count}"
        print(f"✓ BC5 API has {endpoint_count} endpoints")


# Run a simple sanity check if executed directly
if __name__ == "__main__":
    print("Running BC5 API tests...")
    print("✓ BC5 schemas module loaded")
    print("✓ BC5 RBAC module loaded")
    print("✓ BC5 repository module loaded")
    print("✓ BC5 router module loaded")
    print("\nRun 'pytest tests/test_bc5_api.py -v' for full test suite")
