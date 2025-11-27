"""Tests for disk-based audit logging functionality."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.logging import log_audit_event
from app.services.audit import _determine_event_type, log_action


class TestLogAuditEvent:
    """Tests for the log_audit_event function."""

    def test_log_audit_event_with_all_fields(self, caplog):
        """Test log_audit_event logs with all fields provided."""
        with patch("app.core.logging.logger") as mock_logger:
            mock_bind = MagicMock()
            mock_logger.bind.return_value = mock_bind

            log_audit_event(
                "API OPERATION",
                "create",
                user_id=123,
                user_email="test@example.com",
                entity_type="ticket",
                entity_id=456,
                ip_address="192.168.1.1",
                company_id=789,
            )

            mock_logger.bind.assert_called_once()
            bind_kwargs = mock_logger.bind.call_args[1]
            assert bind_kwargs["user_id"] == 123
            assert bind_kwargs["user_email"] == "test@example.com"
            assert bind_kwargs["entity_type"] == "ticket"
            assert bind_kwargs["entity_id"] == 456
            assert bind_kwargs["ip"] == "192.168.1.1"
            assert bind_kwargs["company_id"] == 789
            mock_bind.info.assert_called_once()
            call_message = mock_bind.info.call_args[0][0]
            assert "API OPERATION" in call_message
            assert "create" in call_message

    def test_log_audit_event_minimal_fields(self):
        """Test log_audit_event with minimal fields."""
        with patch("app.core.logging.logger") as mock_logger:
            log_audit_event("BCP ACTION", "delete")

            mock_logger.info.assert_called_once()
            call_message = mock_logger.info.call_args[0][0]
            assert "BCP ACTION" in call_message
            assert "delete" in call_message

    def test_log_audit_event_with_user_id_only(self):
        """Test log_audit_event with only user_id."""
        with patch("app.core.logging.logger") as mock_logger:
            mock_bind = MagicMock()
            mock_logger.bind.return_value = mock_bind

            log_audit_event(
                "API OPERATION",
                "update",
                user_id=42,
            )

            mock_logger.bind.assert_called_once()
            bind_kwargs = mock_logger.bind.call_args[1]
            assert bind_kwargs["user_id"] == 42
            mock_bind.info.assert_called_once()

    def test_log_audit_event_with_entity_info(self):
        """Test log_audit_event with entity type and id."""
        with patch("app.core.logging.logger") as mock_logger:
            mock_bind = MagicMock()
            mock_logger.bind.return_value = mock_bind

            log_audit_event(
                "BCP ACTION",
                "risk.create",
                entity_type="risk",
                entity_id=100,
            )

            mock_logger.bind.assert_called_once()
            bind_kwargs = mock_logger.bind.call_args[1]
            assert bind_kwargs["entity_type"] == "risk"
            assert bind_kwargs["entity_id"] == 100


class TestDetermineEventType:
    """Tests for the _determine_event_type function."""

    def test_bcp_action_prefix(self):
        """Test that BCP actions are correctly identified."""
        assert _determine_event_type("bcp.risk.create") == "BCP ACTION"
        assert _determine_event_type("bcp.objective.delete") == "BCP ACTION"
        assert _determine_event_type("bcp.plan.update") == "BCP ACTION"
        assert _determine_event_type("bcp.event_log.create") == "BCP ACTION"
        assert _determine_event_type("bcp.recovery_action.complete") == "BCP ACTION"

    def test_api_operation_default(self):
        """Test that non-BCP actions default to API OPERATION."""
        assert _determine_event_type("ticket.create") == "API OPERATION"
        assert _determine_event_type("user.update") == "API OPERATION"
        assert _determine_event_type("company.delete") == "API OPERATION"
        assert _determine_event_type("some_random_action") == "API OPERATION"


class TestLogAction:
    """Tests for the log_action function in audit service."""

    @pytest.mark.asyncio
    async def test_log_action_writes_to_database_and_disk(self):
        """Test that log_action writes to both database and disk."""
        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="bcp.risk.create",
                user_id=123,
                entity_type="risk",
                entity_id=456,
                metadata={"company_id": 789},
            )

            # Verify database write
            mock_repo.create_audit_log.assert_called_once()
            db_call_kwargs = mock_repo.create_audit_log.call_args[1]
            assert db_call_kwargs["user_id"] == 123
            assert db_call_kwargs["action"] == "bcp.risk.create"
            assert db_call_kwargs["entity_type"] == "risk"
            assert db_call_kwargs["entity_id"] == 456

            # Verify disk log write
            mock_disk_log.assert_called_once()
            disk_call_args = mock_disk_log.call_args
            assert disk_call_args[0][0] == "BCP ACTION"
            assert disk_call_args[0][1] == "bcp.risk.create"
            assert disk_call_args[1]["user_id"] == 123
            assert disk_call_args[1]["entity_type"] == "risk"
            assert disk_call_args[1]["entity_id"] == 456
            assert disk_call_args[1]["company_id"] == 789

    @pytest.mark.asyncio
    async def test_log_action_extracts_ip_from_request(self):
        """Test that log_action extracts IP address from request."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client.host = "10.0.0.1"

        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="ticket.create",
                user_id=1,
                request=mock_request,
            )

            # Verify IP address was extracted
            db_call_kwargs = mock_repo.create_audit_log.call_args[1]
            assert db_call_kwargs["ip_address"] == "10.0.0.1"

            disk_call_kwargs = mock_disk_log.call_args[1]
            assert disk_call_kwargs["ip_address"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_log_action_extracts_forwarded_ip(self):
        """Test that log_action prefers X-Forwarded-For header."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "203.0.113.50, 70.41.3.18"
        mock_request.client.host = "10.0.0.1"

        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="api.call",
                user_id=1,
                request=mock_request,
            )

            # Verify first IP from X-Forwarded-For was used
            db_call_kwargs = mock_repo.create_audit_log.call_args[1]
            assert db_call_kwargs["ip_address"] == "203.0.113.50"

    @pytest.mark.asyncio
    async def test_log_action_includes_api_key_in_disk_log(self):
        """Test that log_action includes API key in disk log."""
        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="ticket.update",
                user_id=None,
                api_key="test_api_key_123",
            )

            disk_call_kwargs = mock_disk_log.call_args[1]
            assert disk_call_kwargs["api_key"] == "test_api_key_123"

    @pytest.mark.asyncio
    async def test_log_action_handles_no_metadata(self):
        """Test that log_action works without metadata."""
        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="simple.action",
                user_id=1,
            )

            # Should not raise and should call both log functions
            mock_repo.create_audit_log.assert_called_once()
            mock_disk_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_action_api_operation_type(self):
        """Test that non-BCP actions are logged as API OPERATION."""
        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="company.update",
                user_id=1,
            )

            disk_call_args = mock_disk_log.call_args[0]
            assert disk_call_args[0] == "API OPERATION"

    @pytest.mark.asyncio
    async def test_log_action_bcp_action_type(self):
        """Test that BCP actions are logged as BCP ACTION."""
        with patch("app.services.audit.audit_repo") as mock_repo, \
             patch("app.services.audit.log_audit_event") as mock_disk_log:
            mock_repo.create_audit_log = AsyncMock()

            await log_action(
                action="bcp.objective.create",
                user_id=1,
            )

            disk_call_args = mock_disk_log.call_args[0]
            assert disk_call_args[0] == "BCP ACTION"
