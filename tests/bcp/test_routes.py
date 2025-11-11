"""
Test BCP routes for permissions and CRUD operations.

Tests verify:
- Permission checks (bcp:view, bcp:edit, bcp:incident:run)
- CRUD happy paths for key entities
- Proper error handling and validation
- Super admin bypass
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient


class TestPermissionChecks:
    """Test BCP permission requirements."""
    
    @pytest.mark.asyncio
    async def test_bcp_overview_requires_view_permission(self):
        """Test that /bcp/ requires bcp:view permission."""
        from fastapi import Request
        from app.api.routes.bcp import _require_bcp_view
        
        # Mock request
        request = MagicMock(spec=Request)
        request.state.active_company_id = 1
        
        # Mock session data
        mock_session = MagicMock()
        mock_session.user_id = 1
        
        # Mock user without permission
        with patch("app.repositories.users.get_user_by_id") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "is_super_admin": False,
            }
            
            with patch("app.repositories.company_memberships.user_has_permission") as mock_has_perm:
                mock_has_perm.return_value = False
                
                # Should raise 403
                with pytest.raises(HTTPException) as exc_info:
                    await _require_bcp_view(request, mock_session)
                
                assert exc_info.value.status_code == 403
                assert "view permission" in exc_info.value.detail.lower()
    
    @pytest.mark.asyncio
    async def test_bcp_edit_routes_require_edit_permission(self):
        """Test that edit routes require bcp:edit permission."""
        from fastapi import Request
        from app.api.routes.bcp import _require_bcp_edit
        
        # Mock request
        request = MagicMock(spec=Request)
        request.state.active_company_id = 1
        
        # Mock session data
        mock_session = MagicMock()
        mock_session.user_id = 1
        
        # Mock user without permission
        with patch("app.repositories.users.get_user_by_id") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "is_super_admin": False,
            }
            
            with patch("app.repositories.company_memberships.user_has_permission") as mock_has_perm:
                mock_has_perm.return_value = False
                
                # Should raise 403
                with pytest.raises(HTTPException) as exc_info:
                    await _require_bcp_edit(request, mock_session)
                
                assert exc_info.value.status_code == 403
                assert "edit permission" in exc_info.value.detail.lower()
    
    @pytest.mark.asyncio
    async def test_incident_routes_require_incident_run_permission(self):
        """Test that incident routes require bcp:incident:run permission."""
        from fastapi import Request
        from app.api.routes.bcp import _require_bcp_incident_run
        
        # Mock request
        request = MagicMock(spec=Request)
        request.state.active_company_id = 1
        
        # Mock session data
        mock_session = MagicMock()
        mock_session.user_id = 1
        
        # Mock user without permission
        with patch("app.repositories.users.get_user_by_id") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "is_super_admin": False,
            }
            
            with patch("app.repositories.company_memberships.user_has_permission") as mock_has_perm:
                mock_has_perm.return_value = False
                
                # Should raise 403
                with pytest.raises(HTTPException) as exc_info:
                    await _require_bcp_incident_run(request, mock_session)
                
                assert exc_info.value.status_code == 403
                assert "incident:run permission" in exc_info.value.detail.lower()
    
    @pytest.mark.asyncio
    async def test_super_admin_bypasses_permission_checks(self):
        """Test that super admin can access all BCP routes."""
        from fastapi import Request
        from app.api.routes.bcp import _require_bcp_view, _require_bcp_edit, _require_bcp_incident_run
        
        # Mock request
        request = MagicMock(spec=Request)
        request.state.active_company_id = 1
        
        # Mock session data
        mock_session = MagicMock()
        mock_session.user_id = 1
        
        # Mock super admin user
        with patch("app.repositories.users.get_user_by_id") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "is_super_admin": True,
            }
            
            # Should not raise exceptions
            user, company_id = await _require_bcp_view(request, mock_session)
            assert user["is_super_admin"] is True
            assert company_id == 1
            
            user, company_id = await _require_bcp_edit(request, mock_session)
            assert user["is_super_admin"] is True
            
            user, company_id = await _require_bcp_incident_run(request, mock_session)
            assert user["is_super_admin"] is True
    
    @pytest.mark.asyncio
    async def test_user_with_view_permission_can_access(self):
        """Test that user with bcp:view can access view routes."""
        from fastapi import Request
        from app.api.routes.bcp import _require_bcp_view
        
        # Mock request
        request = MagicMock(spec=Request)
        request.state.active_company_id = 1
        
        # Mock session data
        mock_session = MagicMock()
        mock_session.user_id = 1
        
        # Mock user with permission
        with patch("app.repositories.users.get_user_by_id") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "is_super_admin": False,
            }
            
            with patch("app.repositories.company_memberships.user_has_permission") as mock_has_perm:
                mock_has_perm.return_value = True
                
                # Should succeed
                user, company_id = await _require_bcp_view(request, mock_session)
                assert user["id"] == 1
                assert company_id == 1


class TestRiskCRUD:
    """Test CRUD operations for risks."""
    
    @pytest.mark.asyncio
    async def test_create_risk_happy_path(self):
        """Test creating a risk with valid data."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            with patch("app.repositories.bcp.get_risk_by_id") as mock_get:
                mock_get.return_value = {
                    "id": 1,
                    "plan_id": 1,
                    "description": "Test risk",
                    "likelihood": 2,
                    "impact": 3,
                    "rating": 6,
                    "severity": "Moderate",
                }
                
                result = await bcp_repo.create_risk(
                    plan_id=1,
                    description="Test risk",
                    likelihood=2,
                    impact=3,
                    rating=6,
                    severity="Moderate",
                )
                
                assert result["id"] == 1
                assert result["description"] == "Test risk"
                assert result["likelihood"] == 2
                assert result["impact"] == 3
                assert result["rating"] == 6
                assert result["severity"] == "Moderate"
    
    @pytest.mark.asyncio
    async def test_update_risk_happy_path(self):
        """Test updating a risk."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.rowcount = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.update_risk(
                risk_id=1,
                description="Updated risk",
                likelihood=3,
                impact=4,
                rating=12,
                severity="High",
            )
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_delete_risk_happy_path(self):
        """Test deleting a risk."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.rowcount = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.delete_risk(risk_id=1)
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_list_risks_happy_path(self):
        """Test listing risks for a plan."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[
                {
                    "id": 1,
                    "plan_id": 1,
                    "description": "Risk 1",
                    "likelihood": 2,
                    "impact": 3,
                    "rating": 6,
                    "severity": "Moderate",
                },
                {
                    "id": 2,
                    "plan_id": 1,
                    "description": "Risk 2",
                    "likelihood": 4,
                    "impact": 4,
                    "rating": 16,
                    "severity": "Severe",
                },
            ])
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.list_risks(plan_id=1)
            
            assert len(result) == 2
            assert result[0]["description"] == "Risk 1"
            assert result[1]["description"] == "Risk 2"


class TestCriticalActivityCRUD:
    """Test CRUD operations for critical activities."""
    
    @pytest.mark.asyncio
    async def test_create_critical_activity_happy_path(self):
        """Test creating a critical activity."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            with patch("app.repositories.bcp.get_critical_activity_by_id") as mock_get:
                mock_get.return_value = {
                    "id": 1,
                    "plan_id": 1,
                    "name": "Test Activity",
                    "description": "Test description",
                    "priority": "High",
                }
                
                result = await bcp_repo.create_critical_activity(
                    plan_id=1,
                    name="Test Activity",
                    description="Test description",
                    priority="High",
                )
                
                assert result["id"] == 1
                assert result["name"] == "Test Activity"
    
    @pytest.mark.asyncio
    async def test_list_critical_activities_with_sorting(self):
        """Test listing critical activities with sorting options."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[
                {
                    "id": 1,
                    "plan_id": 1,
                    "name": "Activity A",
                    "priority": "High",
                },
                {
                    "id": 2,
                    "plan_id": 1,
                    "name": "Activity B",
                    "priority": "Medium",
                },
            ])
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.list_critical_activities(plan_id=1, sort_by="name")
            
            assert len(result) == 2


class TestIncidentCRUD:
    """Test CRUD operations for incidents."""
    
    @pytest.mark.asyncio
    async def test_create_incident_happy_path(self):
        """Test creating an incident."""
        from app.repositories import bcp as bcp_repo
        from datetime import datetime
        
        now = datetime.utcnow()
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            with patch("app.repositories.bcp.get_incident_by_id") as mock_get:
                mock_get.return_value = {
                    "id": 1,
                    "plan_id": 1,
                    "started_at": now,
                    "status": "Active",
                    "source": "Manual",
                }
                
                result = await bcp_repo.create_incident(
                    plan_id=1,
                    started_at=now,
                    source="Manual",
                )
                
                assert result["id"] == 1
                assert result["status"] == "Active"
    
    @pytest.mark.asyncio
    async def test_close_incident_happy_path(self):
        """Test closing an incident."""
        from app.repositories import bcp as bcp_repo
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.rowcount = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.close_incident(incident_id=1)
            
            assert result is True
    
    @pytest.mark.asyncio
    async def test_get_active_incident(self):
        """Test getting active incident for a plan."""
        from app.repositories import bcp as bcp_repo
        from datetime import datetime
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchone = AsyncMock(return_value={
                "id": 1,
                "plan_id": 1,
                "started_at": datetime.utcnow(),
                "status": "Active",
                "source": "Manual",
            })
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            result = await bcp_repo.get_active_incident(plan_id=1)
            
            assert result is not None
            assert result["status"] == "Active"


class TestValidationErrors:
    """Test validation and error handling."""
    
    @pytest.mark.asyncio
    async def test_risk_invalid_likelihood_rejected(self):
        """Test that invalid likelihood values are rejected."""
        from app.schemas.bcp_risk import RiskCreate
        
        # Test likelihood > 4
        with pytest.raises(Exception):  # Pydantic validation error
            RiskCreate(
                description="Test",
                likelihood=5,
                impact=2,
            )
        
        # Test likelihood < 1
        with pytest.raises(Exception):
            RiskCreate(
                description="Test",
                likelihood=0,
                impact=2,
            )
    
    @pytest.mark.asyncio
    async def test_risk_invalid_impact_rejected(self):
        """Test that invalid impact values are rejected."""
        from app.schemas.bcp_risk import RiskCreate
        
        # Test impact > 4
        with pytest.raises(Exception):  # Pydantic validation error
            RiskCreate(
                description="Test",
                likelihood=2,
                impact=5,
            )
        
        # Test impact < 1
        with pytest.raises(Exception):
            RiskCreate(
                description="Test",
                likelihood=2,
                impact=0,
            )
    
    @pytest.mark.asyncio
    async def test_missing_required_fields_rejected(self):
        """Test that missing required fields are rejected."""
        from app.schemas.bcp_risk import RiskCreate
        
        # Missing description
        with pytest.raises(Exception):
            RiskCreate(
                likelihood=2,
                impact=3,
            )
        
        # Missing likelihood
        with pytest.raises(Exception):
            RiskCreate(
                description="Test",
                impact=3,
            )
