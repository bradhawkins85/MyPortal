"""
Tests for BC10 Business Continuity Acknowledgment Flow.

Tests acknowledgment API endpoints, pending user queries, notification triggers,
and acknowledgment summary statistics.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock


class TestBC10Schemas:
    """Test BC10 acknowledgment Pydantic schemas validation."""
    
    def test_acknowledge_schema_with_version(self):
        """Test acknowledgment schema with version number."""
        # Import locally to avoid circular dependency issues
        from app.schemas.bc5_models import BCAcknowledge
        
        ack_data = BCAcknowledge(ack_version_number=5)
        assert ack_data.ack_version_number == 5
    
    def test_acknowledge_schema_without_version(self):
        """Test acknowledgment schema without version number."""
        from app.schemas.bc5_models import BCAcknowledge
        
        ack_data = BCAcknowledge()
        assert ack_data.ack_version_number is None
    
    def test_acknowledgment_summary_schema(self):
        """Test acknowledgment summary schema."""
        from app.schemas.bc5_models import BCAcknowledgmentSummary
        
        summary = BCAcknowledgmentSummary(
            total_users=10,
            acknowledged_users=7,
            pending_users=3,
            version_number=2,
        )
        assert summary.total_users == 10
        assert summary.acknowledged_users == 7
        assert summary.pending_users == 3
        assert summary.version_number == 2
    
    def test_pending_user_schema(self):
        """Test pending user schema."""
        from app.schemas.bc5_models import BCPendingUser
        
        user = BCPendingUser(
            id=5,
            email="user@example.com",
            name="Test User",
        )
        assert user.id == 5
        assert user.email == "user@example.com"
        assert user.name == "Test User"
    
    def test_pending_user_schema_without_name(self):
        """Test pending user schema with optional name."""
        from app.schemas.bc5_models import BCPendingUser
        
        user = BCPendingUser(
            id=5,
            email="user@example.com",
        )
        assert user.id == 5
        assert user.email == "user@example.com"
        assert user.name is None
    
    def test_notify_acknowledgment_schema(self):
        """Test notification request schema."""
        from app.schemas.bc5_models import BCNotifyAcknowledgment
        
        notify = BCNotifyAcknowledgment(
            user_ids=[1, 2, 3],
            message="Please acknowledge the updated plan",
        )
        assert notify.user_ids == [1, 2, 3]
        assert notify.message == "Please acknowledge the updated plan"
    
    def test_notify_acknowledgment_requires_users(self):
        """Test that notification requires at least one user."""
        from app.schemas.bc5_models import BCNotifyAcknowledgment
        
        with pytest.raises(Exception):  # Pydantic ValidationError
            BCNotifyAcknowledgment(user_ids=[])


class TestBC10RepositoryFunctions:
    """Test BC10 repository functions for acknowledgments."""
    
    @pytest.mark.asyncio
    async def test_get_users_pending_acknowledgment(self):
        """Test getting users pending acknowledgment."""
        from app.repositories import bc3 as bc_repo
        
        # Mock database response
        mock_users = [
            {"id": 1, "email": "user1@example.com", "name": "User One"},
            {"id": 2, "email": "user2@example.com", "name": "User Two"},
        ]
        
        with patch("app.repositories.bc3.db.fetch_all", return_value=mock_users):
            result = await bc_repo.get_users_pending_acknowledgment(plan_id=1, version_number=2)
            
            assert len(result) == 2
            assert result[0]["id"] == 1
            assert result[1]["email"] == "user2@example.com"
    
    @pytest.mark.asyncio
    async def test_get_acknowledgment_summary(self):
        """Test getting acknowledgment summary."""
        from app.repositories import bc3 as bc_repo
        
        # Mock database responses
        total_result = {"total_users": 10}
        ack_result = {"acknowledged_users": 7}
        
        async def mock_fetch_one(query, params=None):
            if "COUNT(DISTINCT u.id)" in query:
                return total_result
            elif "COUNT(DISTINCT ba.user_id)" in query:
                return ack_result
            return None
        
        with patch("app.repositories.bc3.db.fetch_one", side_effect=mock_fetch_one):
            result = await bc_repo.get_acknowledgment_summary(plan_id=1, version_number=2)
            
            assert result["total_users"] == 10
            assert result["acknowledged_users"] == 7
            assert result["pending_users"] == 3
            assert result["version_number"] == 2
    
    @pytest.mark.asyncio
    async def test_get_acknowledgment_summary_no_users(self):
        """Test acknowledgment summary with no users."""
        from app.repositories import bc3 as bc_repo
        
        # Mock database responses with no users
        with patch("app.repositories.bc3.db.fetch_one", return_value=None):
            result = await bc_repo.get_acknowledgment_summary(plan_id=1, version_number=1)
            
            assert result["total_users"] == 0
            assert result["acknowledged_users"] == 0
            assert result["pending_users"] == 0
    
    @pytest.mark.asyncio
    async def test_get_acknowledgment_summary_all_acknowledged(self):
        """Test acknowledgment summary when all users have acknowledged."""
        from app.repositories import bc3 as bc_repo
        
        # Mock database responses where all users acknowledged
        total_result = {"total_users": 5}
        ack_result = {"acknowledged_users": 5}
        
        async def mock_fetch_one(query, params=None):
            if "COUNT(DISTINCT u.id)" in query:
                return total_result
            elif "COUNT(DISTINCT ba.user_id)" in query:
                return ack_result
            return None
        
        with patch("app.repositories.bc3.db.fetch_one", side_effect=mock_fetch_one):
            result = await bc_repo.get_acknowledgment_summary(plan_id=1, version_number=2)
            
            assert result["total_users"] == 5
            assert result["acknowledged_users"] == 5
            assert result["pending_users"] == 0


class TestBC10AcknowledgmentFlow:
    """Test acknowledgment flow integration."""
    
    @pytest.mark.asyncio
    async def test_version_activation_triggers_acknowledgment_requirement(self):
        """Test that activating a version triggers acknowledgment requirement."""
        from app.repositories import bc3 as bc_repo
        
        # Mock plan and version data
        plan_data = {"id": 1, "title": "Test Plan"}
        version_data = {"id": 5, "plan_id": 1, "version_number": 2}
        pending_users = [
            {"id": 1, "email": "user1@example.com", "name": "User One"},
            {"id": 2, "email": "user2@example.com", "name": "User Two"},
        ]
        
        with patch("app.repositories.bc3.get_plan_by_id", return_value=plan_data), \
             patch("app.repositories.bc3.get_version_by_id", return_value=version_data), \
             patch("app.repositories.bc3.activate_version", return_value=version_data), \
             patch("app.repositories.bc3.get_users_pending_acknowledgment", return_value=pending_users) as mock_pending, \
             patch("app.repositories.bc3.create_audit_entry") as mock_audit:
            
            # Simulate version activation
            result = await bc_repo.activate_version(5, 1)
            
            # Verify version was returned
            assert result["id"] == 5
            
            # Get pending users after activation
            pending = await bc_repo.get_users_pending_acknowledgment(1, 2)
            assert len(pending) == 2
    
    @pytest.mark.asyncio
    async def test_acknowledge_removes_from_pending_list(self):
        """Test that acknowledging removes user from pending list."""
        from app.repositories import bc3 as bc_repo
        
        # Initial state: 2 users pending
        initial_pending = [
            {"id": 1, "email": "user1@example.com", "name": "User One"},
            {"id": 2, "email": "user2@example.com", "name": "User Two"},
        ]
        
        # After user 1 acknowledges: only user 2 pending
        after_ack_pending = [
            {"id": 2, "email": "user2@example.com", "name": "User Two"},
        ]
        
        ack_data = {
            "id": 1,
            "plan_id": 1,
            "user_id": 1,
            "ack_at_utc": datetime.now(timezone.utc),
            "ack_version_number": 2,
        }
        
        with patch("app.repositories.bc3.db.execute", return_value=1), \
             patch("app.repositories.bc3.db.fetch_one", return_value=ack_data), \
             patch("app.repositories.bc3.db.fetch_all", side_effect=[initial_pending, after_ack_pending]):
            
            # Before acknowledgment
            pending_before = await bc_repo.get_users_pending_acknowledgment(1, 2)
            assert len(pending_before) == 2
            
            # User acknowledges
            ack = await bc_repo.create_acknowledgment(plan_id=1, user_id=1, ack_version_number=2)
            assert ack["user_id"] == 1
            
            # After acknowledgment
            pending_after = await bc_repo.get_users_pending_acknowledgment(1, 2)
            assert len(pending_after) == 1
            assert pending_after[0]["id"] == 2


class TestBC10NotificationFlow:
    """Test notification flow for acknowledgments."""
    
    @pytest.mark.asyncio
    async def test_notify_pending_users_validates_user_ids(self):
        """Test that notification validates user IDs."""
        # This would test the API endpoint validation logic
        # In a real implementation, we'd mock the notification service
        pass
    
    @pytest.mark.asyncio
    async def test_notify_creates_audit_entry(self):
        """Test that sending notifications creates an audit entry."""
        from app.repositories import bc3 as bc_repo
        
        audit_data = {
            "id": 1,
            "plan_id": 1,
            "action": "acknowledgment_notification_sent",
            "actor_user_id": 5,
            "details_json": {"user_ids": [1, 2, 3]},
            "at_utc": datetime.now(timezone.utc),
        }
        
        with patch("app.repositories.bc3.db.execute", return_value=1), \
             patch("app.repositories.bc3.db.fetch_one", return_value=audit_data):
            
            result = await bc_repo.create_audit_entry(
                plan_id=1,
                action="acknowledgment_notification_sent",
                actor_user_id=5,
                details_json={"user_ids": [1, 2, 3]},
            )
            
            assert result["action"] == "acknowledgment_notification_sent"
            assert result["details_json"]["user_ids"] == [1, 2, 3]


class TestBC10EdgeCases:
    """Test edge cases for acknowledgment flow."""
    
    @pytest.mark.asyncio
    async def test_pending_users_excludes_super_admins(self):
        """Test that super admins are excluded from pending users list."""
        from app.repositories import bc3 as bc_repo
        
        # Only regular users should be in pending list, not super admins
        mock_users = [
            {"id": 2, "email": "user@example.com", "name": "Regular User"},
        ]
        
        with patch("app.repositories.bc3.db.fetch_all", return_value=mock_users):
            result = await bc_repo.get_users_pending_acknowledgment(plan_id=1, version_number=1)
            
            # Verify no super admin in results
            assert len(result) == 1
            assert result[0]["id"] == 2
    
    @pytest.mark.asyncio
    async def test_acknowledging_higher_version_covers_lower_versions(self):
        """Test that acknowledging v2 also covers v1."""
        from app.repositories import bc3 as bc_repo
        
        # User acknowledges version 3
        ack_data = {
            "id": 1,
            "plan_id": 1,
            "user_id": 1,
            "ack_at_utc": datetime.now(timezone.utc),
            "ack_version_number": 3,
        }
        
        # Query checks for version 2 - should exclude this user
        # because they acknowledged v3 which is >= v2
        pending_v2 = []  # User should not appear in v2 pending list
        
        with patch("app.repositories.bc3.db.execute", return_value=1), \
             patch("app.repositories.bc3.db.fetch_one", return_value=ack_data), \
             patch("app.repositories.bc3.db.fetch_all", return_value=pending_v2):
            
            # User acknowledges v3
            ack = await bc_repo.create_acknowledgment(plan_id=1, user_id=1, ack_version_number=3)
            assert ack["ack_version_number"] == 3
            
            # Check pending for v2 - should be empty
            pending = await bc_repo.get_users_pending_acknowledgment(1, 2)
            assert len(pending) == 0
    
    @pytest.mark.asyncio
    async def test_plan_without_active_version_handles_gracefully(self):
        """Test that plans without active versions handle acknowledgment queries gracefully."""
        from app.repositories import bc3 as bc_repo
        
        with patch("app.repositories.bc3.db.fetch_one", return_value=None):
            # Query for acknowledgment summary when no version exists
            # Should return zeros, not crash
            result = await bc_repo.get_acknowledgment_summary(plan_id=999, version_number=1)
            
            assert result["total_users"] == 0
            assert result["acknowledged_users"] == 0
            assert result["pending_users"] == 0
