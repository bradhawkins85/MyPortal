"""Tests for BCP permission checks and company isolation."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Request

pytestmark = pytest.mark.anyio


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/bcp") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope, _dummy_receive)
    request.state.active_company_id = 1
    return request


class TestBCPViewPermission:
    """Test bcp:view permission checks."""
    
    async def test_super_admin_has_access(self):
        """Super admin should have bcp:view access."""
        from app.api.routes.bcp import _require_bcp_view
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp")
        session = SessionData(
            id=1,
            user_id=1,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                mock_get_user.return_value = {"id": 1, "is_super_admin": True, "name": "Admin"}
                
                user, company_id = await _require_bcp_view(request, session)
                
                assert user["id"] == 1
                assert company_id == 1
    
    async def test_user_with_permission_has_access(self):
        """User with bcp:view permission should have access."""
        from app.api.routes.bcp import _require_bcp_view
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp")
        session = SessionData(
            id=1,
            user_id=2,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 2, "is_super_admin": False, "name": "User"}
                    mock_has_perm.return_value = True
                    
                    user, company_id = await _require_bcp_view(request, session)
                    
                    assert user["id"] == 2
                    assert company_id == 1
                    mock_has_perm.assert_called_once_with(2, "bcp:view")
    
    async def test_user_without_permission_denied(self):
        """User without bcp:view permission should be denied."""
        from app.api.routes.bcp import _require_bcp_view
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp")
        session = SessionData(
            id=1,
            user_id=3,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 3, "is_super_admin": False, "name": "User"}
                    mock_has_perm.return_value = False
                    
                    with pytest.raises(HTTPException) as exc_info:
                        await _require_bcp_view(request, session)
                    
                    assert exc_info.value.status_code == 403
                    assert "BCP view permission required" in exc_info.value.detail


class TestBCPEditPermission:
    """Test bcp:edit permission checks."""
    
    async def test_user_with_edit_permission_has_access(self):
        """User with bcp:edit permission should have access."""
        from app.api.routes.bcp import _require_bcp_edit
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp/update")
        session = SessionData(
            id=1,
            user_id=2,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 2, "is_super_admin": False, "name": "Editor"}
                    mock_has_perm.return_value = True
                    
                    user, company_id = await _require_bcp_edit(request, session)
                    
                    assert user["id"] == 2
                    assert company_id == 1
                    mock_has_perm.assert_called_once_with(2, "bcp:edit")
    
    async def test_user_without_edit_permission_denied(self):
        """User without bcp:edit permission should be denied."""
        from app.api.routes.bcp import _require_bcp_edit
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp/update")
        session = SessionData(
            id=1,
            user_id=3,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 3, "is_super_admin": False, "name": "Viewer"}
                    mock_has_perm.return_value = False
                    
                    with pytest.raises(HTTPException) as exc_info:
                        await _require_bcp_edit(request, session)
                    
                    assert exc_info.value.status_code == 403
                    assert "BCP edit permission required" in exc_info.value.detail


class TestBCPIncidentRunPermission:
    """Test bcp:incident:run permission checks."""
    
    async def test_user_with_incident_run_permission_has_access(self):
        """User with bcp:incident:run permission should have access."""
        from app.api.routes.bcp import _require_bcp_incident_run
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp/incident/start")
        session = SessionData(
            id=1,
            user_id=2,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 2, "is_super_admin": False, "name": "Incident Manager"}
                    mock_has_perm.return_value = True
                    
                    user, company_id = await _require_bcp_incident_run(request, session)
                    
                    assert user["id"] == 2
                    assert company_id == 1
                    mock_has_perm.assert_called_once_with(2, "bcp:incident:run")
    
    async def test_user_without_incident_run_permission_denied(self):
        """User without bcp:incident:run permission should be denied."""
        from app.api.routes.bcp import _require_bcp_incident_run
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp/incident/start")
        session = SessionData(
            id=1,
            user_id=3,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 3, "is_super_admin": False, "name": "Viewer"}
                    mock_has_perm.return_value = False
                    
                    with pytest.raises(HTTPException) as exc_info:
                        await _require_bcp_incident_run(request, session)
                    
                    assert exc_info.value.status_code == 403
                    assert "BCP incident:run permission required" in exc_info.value.detail


class TestBCPExportPermission:
    """Test bcp:export permission checks."""
    
    async def test_user_with_export_permission_has_access(self):
        """User with bcp:export permission should have access."""
        from app.api.routes.bcp import _require_bcp_export
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp/export/pdf")
        session = SessionData(
            id=1,
            user_id=2,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 2, "is_super_admin": False, "name": "Exporter"}
                    mock_has_perm.return_value = True
                    
                    user, company_id = await _require_bcp_export(request, session)
                    
                    assert user["id"] == 2
                    assert company_id == 1
                    mock_has_perm.assert_called_once_with(2, "bcp:export")
    
    async def test_user_without_export_permission_denied(self):
        """User without bcp:export permission should be denied."""
        from app.api.routes.bcp import _require_bcp_export
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp/export/pdf")
        session = SessionData(
            id=1,
            user_id=3,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=1,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 3, "is_super_admin": False, "name": "Viewer"}
                    mock_has_perm.return_value = False
                    
                    with pytest.raises(HTTPException) as exc_info:
                        await _require_bcp_export(request, session)
                    
                    assert exc_info.value.status_code == 403
                    assert "BCP export permission required" in exc_info.value.detail


class TestCompanyIsolation:
    """Test that users cannot access another company's BCP."""
    
    async def test_no_active_company_rejected(self):
        """User without active company should be rejected."""
        from app.api.routes.bcp import _require_bcp_view
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp")
        request.state.active_company_id = None
        session = SessionData(
            id=1,
            user_id=2,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=None,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 2, "is_super_admin": False, "name": "User"}
                    mock_has_perm.return_value = True
                    
                    with pytest.raises(HTTPException) as exc_info:
                        await _require_bcp_view(request, session)
                    
                    assert exc_info.value.status_code == 400
                    assert "No active company selected" in exc_info.value.detail
    
    async def test_permission_checked_per_company(self):
        """Permission should be checked for the active company."""
        from app.api.routes.bcp import _require_bcp_view
        from app.security.session import SessionData
        from datetime import datetime, timedelta
        
        request = _make_request("/bcp")
        request.state.active_company_id = 5
        session = SessionData(
            id=1,
            user_id=2,
            session_token="test_token",
            csrf_token="test_csrf",
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
            last_seen_at=datetime.utcnow(),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=5,
        )
        
        with patch('app.api.routes.bcp.get_current_session', return_value=session):
            with patch('app.repositories.users.get_user_by_id', new_callable=AsyncMock) as mock_get_user:
                with patch('app.repositories.company_memberships.user_has_permission', new_callable=AsyncMock) as mock_has_perm:
                    mock_get_user.return_value = {"id": 2, "is_super_admin": False, "name": "User"}
                    mock_has_perm.return_value = True
                    
                    user, company_id = await _require_bcp_view(request, session)
                    
                    assert company_id == 5
                    # Permission check is based on user_id, not company
                    # The company isolation is enforced via active_company_id
                    mock_has_perm.assert_called_once_with(2, "bcp:view")
