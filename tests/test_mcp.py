"""Tests for the MCP (Model Context Protocol) WebSocket server."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest


# Test configuration
TEST_MCP_TOKEN = "test-mcp-token-12345"


# Set up MCP environment before importing app
os.environ["MCP_ENABLED"] = "true"
os.environ["MCP_TOKEN"] = TEST_MCP_TOKEN
os.environ["MCP_ALLOWED_MODELS"] = "users,tickets,change_log"
os.environ["MCP_READONLY"] = "true"
os.environ["MCP_RATE_LIMIT"] = "60"

# Clear settings cache before import
from app.core import config
config.get_settings.cache_clear()

# Now import app after MCP is enabled
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


def test_mcp_connection_with_valid_token_in_header(client):
    """Test that MCP connection succeeds with valid token in header."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        # Send a simple list request
        request = {
            "id": "test-1",
            "action": "list",
            "model": "users",
            "params": {"limit": 1}
        }
        websocket.send_json(request)
        
        # Should receive a response
        response = websocket.receive_json()
        assert response["id"] == "test-1"
        assert response["status"] in ("ok", "error")  # May error if no DB, but connection works


def test_mcp_connection_with_valid_token_in_query_param(client):
    """Test that MCP connection succeeds with valid token in query parameter."""
    with client.websocket_connect(f"/mcp/ws?token={TEST_MCP_TOKEN}") as websocket:
        # Send a simple list request
        request = {
            "id": "test-2",
            "action": "list",
            "model": "users",
            "params": {"limit": 1}
        }
        websocket.send_json(request)
        
        # Should receive a response
        response = websocket.receive_json()
        assert response["id"] == "test-2"
        assert response["status"] in ("ok", "error")


def test_mcp_connection_rejected_with_missing_token(client):
    """Test that MCP connection is rejected when token is missing."""
    from fastapi.websockets import WebSocketDisconnect
    
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/mcp/ws"):
            pass  # Connection should be rejected immediately


def test_mcp_connection_rejected_with_invalid_token(client):
    """Test that MCP connection is rejected with invalid token."""
    from fastapi.websockets import WebSocketDisconnect
    
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/mcp/ws",
            headers={"X-MCP-Token": "invalid-token"}
        ):
            pass  # Connection should be rejected


@patch("app.mcp_server.db.fetch_all")
def test_mcp_list_action_returns_filtered_data(mock_fetch_all, client):
    """Test that list action returns data with sensitive fields filtered."""
    # Mock database response with sensitive fields
    mock_fetch_all.return_value = [
        {
            "id": 1,
            "email": "user@example.com",
            "password_hash": "secret_hash",
            "first_name": "John",
            "totp_secret": "totp_secret_value",
        },
        {
            "id": 2,
            "email": "admin@example.com",
            "password_hash": "another_secret",
            "first_name": "Jane",
            "api_key": "api_key_value",
        },
    ]
    
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-3",
            "action": "list",
            "model": "users",
            "params": {"limit": 10}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-3"
        assert response["status"] == "ok"
        assert "data" in response
        
        # Verify sensitive fields are filtered
        for record in response["data"]:
            assert "password_hash" not in record
            assert "totp_secret" not in record
            assert "api_key" not in record
            # Regular fields should still be present
            assert "id" in record
            assert "email" in record
            assert "first_name" in record


@patch("app.mcp_server.db.fetch_one")
def test_mcp_get_action_returns_single_record(mock_fetch_one, client):
    """Test that get action returns a single record by ID."""
    mock_fetch_one.return_value = {
        "id": 1,
        "email": "user@example.com",
        "first_name": "John",
        "password_hash": "should_be_filtered",
    }
    
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-4",
            "action": "get",
            "model": "users",
            "params": {"id": 1}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-4"
        assert response["status"] == "ok"
        assert "data" in response
        assert response["data"]["id"] == 1
        assert response["data"]["email"] == "user@example.com"
        assert "password_hash" not in response["data"]


def test_mcp_write_operation_rejected_in_readonly_mode(client):
    """Test that write operations are rejected when MCP_READONLY is true."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        # Try to send a create/update/delete request
        request = {
            "id": "test-5",
            "action": "create",
            "model": "users",
            "params": {"email": "new@example.com"}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-5"
        assert response["status"] == "error"
        assert "read-only" in response["error"].lower()


def test_mcp_disallowed_model_rejected(client):
    """Test that requests for disallowed models are rejected."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-6",
            "action": "list",
            "model": "api_keys",  # Not in allowed list
            "params": {}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-6"
        assert response["status"] == "error"
        assert "not allowed" in response["error"].lower()


def test_mcp_rate_limiting_enforced(client):
    """Test that rate limiting is enforced per connection."""
    # Set a very low rate limit for this test
    with patch("app.mcp_server.settings") as mock_settings:
        mock_settings.mcp_enabled = True
        mock_settings.mcp_token = TEST_MCP_TOKEN
        mock_settings.mcp_allowed_models = "users,tickets,change_log"
        mock_settings.mcp_readonly = True
        mock_settings.mcp_rate_limit = 2  # Only allow 2 requests
        
        with client.websocket_connect(
            "/mcp/ws",
            headers={"X-MCP-Token": TEST_MCP_TOKEN}
        ) as websocket:
            # First request should succeed
            request1 = {
                "id": "test-7-1",
                "action": "list",
                "model": "users",
                "params": {"limit": 1}
            }
            websocket.send_json(request1)
            response1 = websocket.receive_json()
            # May succeed or fail depending on DB, but should get a response
            assert response1["id"] == "test-7-1"
            
            # Second request should succeed
            request2 = {
                "id": "test-7-2",
                "action": "list",
                "model": "users",
                "params": {"limit": 1}
            }
            websocket.send_json(request2)
            response2 = websocket.receive_json()
            assert response2["id"] == "test-7-2"
            
            # Third request should hit rate limit
            request3 = {
                "id": "test-7-3",
                "action": "list",
                "model": "users",
                "params": {"limit": 1}
            }
            websocket.send_json(request3)
            response3 = websocket.receive_json()
            assert response3["status"] == "error"
            assert "rate limit" in response3["error"].lower()


def test_mcp_invalid_json_message_handling(client):
    """Test that invalid JSON messages are handled gracefully."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        # Send invalid JSON
        websocket.send_text("not valid json {{{")
        
        response = websocket.receive_json()
        assert response["status"] == "error"
        assert "json" in response["error"].lower()


def test_mcp_unsupported_action_rejected(client):
    """Test that unsupported actions are rejected."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-8",
            "action": "unsupported_action",
            "model": "users",
            "params": {}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-8"
        assert response["status"] == "error"
        assert "unsupported" in response["error"].lower()


@patch("app.mcp_server.db.fetch_all")
def test_mcp_query_action_works_like_list(mock_fetch_all, client):
    """Test that query action works similarly to list."""
    mock_fetch_all.return_value = [
        {"id": 1, "subject": "Test ticket", "status": "open"}
    ]
    
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-9",
            "action": "query",
            "model": "tickets",
            "params": {"filters": {"status": "open"}, "limit": 10}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-9"
        assert response["status"] == "ok"
        assert "data" in response


def test_mcp_disabled_by_default():
    """Test that MCP server is disabled by default in settings."""
    # Clear env to get defaults
    with patch.dict(os.environ, {}, clear=True):
        # Set required settings
        os.environ["SESSION_SECRET"] = "test"
        os.environ["TOTP_ENCRYPTION_KEY"] = "A" * 64
        
        from app.core import config
        config.get_settings.cache_clear()
        settings = config.get_settings()
        
        assert settings.mcp_enabled is False
        assert settings.mcp_token is None
        assert settings.mcp_readonly is True
        assert settings.mcp_rate_limit == 60


@patch("app.mcp_server.db.fetch_all")
def test_mcp_list_with_pagination_params(mock_fetch_all, client):
    """Test that pagination parameters are properly handled."""
    mock_fetch_all.return_value = [
        {"id": 11, "email": "user11@example.com"},
        {"id": 12, "email": "user12@example.com"},
    ]
    
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-10",
            "action": "list",
            "model": "users",
            "params": {"limit": 2, "offset": 10}
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-10"
        assert response["status"] == "ok"
        assert response["limit"] == 2
        assert response["offset"] == 10
        assert len(response["data"]) <= 2


def test_mcp_get_without_id_param_fails(client):
    """Test that get action without id parameter fails."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-11",
            "action": "get",
            "model": "users",
            "params": {}  # Missing 'id'
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-11"
        assert response["status"] == "error"
        assert "id" in response["error"].lower()


@pytest.mark.parametrize("malicious_field", [
    "id; DROP TABLE users; --",
    "status OR 1=1 --",
    "' OR '1'='1",
    "id=1; DELETE FROM users WHERE 1=1; --",
    "1 UNION SELECT * FROM passwords --",
    "column`; INSERT INTO users --",
    "field\"; DROP TABLE --",
    "col\nDROP TABLE users",
    "status--",
    "id/**/OR/**/1=1",
])
def test_mcp_sql_injection_via_filter_field_blocked(malicious_field, client):
    """Test that SQL injection attempts via filter field names are blocked."""
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-sqli-field",
            "action": "list",
            "model": "users",
            "params": {
                "filters": {malicious_field: "test_value"}
            }
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-sqli-field"
        assert response["status"] == "error"
        assert "invalid filter field" in response["error"].lower()


@patch("app.mcp_server.db.fetch_all")
def test_mcp_valid_filter_field_allowed(mock_fetch_all, client):
    """Test that valid filter field names are allowed."""
    mock_fetch_all.return_value = [
        {"id": 1, "email": "user@example.com", "status": "active"}
    ]
    
    with client.websocket_connect(
        "/mcp/ws",
        headers={"X-MCP-Token": TEST_MCP_TOKEN}
    ) as websocket:
        request = {
            "id": "test-valid-field",
            "action": "list",
            "model": "users",
            "params": {
                "filters": {"status": "active", "company_id": "1"}
            }
        }
        websocket.send_json(request)
        
        response = websocket.receive_json()
        assert response["id"] == "test-valid-field"
        assert response["status"] == "ok"
        assert "data" in response
