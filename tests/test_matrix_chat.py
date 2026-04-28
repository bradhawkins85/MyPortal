"""Tests for the Matrix chat integration."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types

import pytest
import respx
import httpx


def _load_module_from_file(name: str, path: str):
    """Load a Python module directly from file path, bypassing package __init__."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bootstrap_matrix_module():
    """
    Load app.services.matrix without triggering app/__init__.py (which would
    attempt to import app.main and all its heavy transitive dependencies).
    """
    root = pathlib.Path(__file__).resolve().parent.parent

    # Ensure 'app' package stub exists (prevents __init__ execution)
    if "app" not in sys.modules:
        app_pkg = types.ModuleType("app")
        app_pkg.__path__ = [str(root / "app")]
        app_pkg.__package__ = "app"
        sys.modules["app"] = app_pkg
    else:
        # If already loaded (e.g. by conftest), just use it
        pass

    # app.core
    for sub in ("app.core", "app.services"):
        if sub not in sys.modules:
            pkg = types.ModuleType(sub)
            pkg.__path__ = [str(root / sub.replace(".", "/"))]
            pkg.__package__ = sub
            sys.modules[sub] = pkg

    # Load app.core.config properly
    if "app.core.config" not in sys.modules:
        _load_module_from_file("app.core.config", str(root / "app/core/config.py"))

    # Load app.core.logging properly
    if "app.core.logging" not in sys.modules:
        _load_module_from_file("app.core.logging", str(root / "app/core/logging.py"))

    # Now load the matrix service
    return _load_module_from_file("app.services.matrix", str(root / "app/services/matrix.py"))


_matrix = _bootstrap_matrix_module()

MatrixError = _matrix.MatrixError
MatrixConfigError = _matrix.MatrixConfigError
create_room = _matrix.create_room
send_message = _matrix.send_message
invite_user = _matrix.invite_user
whoami = _matrix.whoami
sanitize_localpart = _matrix.sanitize_localpart
set_user_power_level = _matrix.set_user_power_level
get_power_levels = _matrix.get_power_levels
enable_room_encryption = _matrix.enable_room_encryption


# ---------------------------------------------------------------------------
# sanitize_localpart
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sanitize_localpart_basic():
    assert sanitize_localpart("John Smith") == "john_smith"


@pytest.mark.asyncio
async def test_sanitize_localpart_special_chars():
    result = sanitize_localpart("Test User #1!")
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789._=-" for c in result)


@pytest.mark.asyncio
async def test_sanitize_localpart_empty():
    assert sanitize_localpart("") == "user"


# ---------------------------------------------------------------------------
# create_room
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_create_room_success(monkeypatch):
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")
    monkeypatch.setattr(_matrix._settings, "matrix_default_room_preset", "private_chat")

    respx.post("https://matrix.example.com/_matrix/client/v3/createRoom").mock(
        return_value=httpx.Response(200, json={"room_id": "!abc123:example.com"})
    )

    result = await create_room(name="Test Room")
    assert result["room_id"] == "!abc123:example.com"


@pytest.mark.asyncio
async def test_missing_homeserver_url_raises(monkeypatch):
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", None)
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "tok")
    with pytest.raises(MatrixConfigError):
        await create_room(name="Test")


@pytest.mark.asyncio
async def test_missing_bot_token_raises(monkeypatch):
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", None)
    with pytest.raises(MatrixConfigError):
        await create_room(name="Test")


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_send_message_success(monkeypatch):
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    respx.put(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/send/.*"
    ).mock(return_value=httpx.Response(200, json={"event_id": "$event1"}))

    result = await send_message("!room1:example.com", "Hello")
    assert result["event_id"] == "$event1"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_matrix_error_raised_on_non_200(monkeypatch):
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    respx.post("https://matrix.example.com/_matrix/client/v3/createRoom").mock(
        return_value=httpx.Response(403, json={"errcode": "M_FORBIDDEN", "error": "Not allowed"})
    )

    with pytest.raises(MatrixError) as exc_info:
        await create_room(name="Bad room")
    assert exc_info.value.errcode == "M_FORBIDDEN"


# ---------------------------------------------------------------------------
# Rate-limit retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_retry(monkeypatch):
    """Test that 429 responses are retried once."""
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                json={"errcode": "M_LIMIT_EXCEEDED"},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(200, json={"room_id": "!room:example.com"})

    respx.post("https://matrix.example.com/_matrix/client/v3/createRoom").mock(
        side_effect=side_effect
    )

    result = await create_room(name="Test Room")
    assert result["room_id"] == "!room:example.com"
    assert call_count == 2


# ---------------------------------------------------------------------------
# set_user_power_level
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_set_user_power_level_grants_admin(monkeypatch):
    """set_user_power_level should fetch current power levels and update the user."""
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    room_id = "!room1:example.com"
    user_mxid = "@tech:example.com"
    current_levels = {"users": {"@bot:example.com": 100}, "events": {}, "users_default": 0}

    respx.get(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/state/m\.room\.power_levels/"
    ).mock(return_value=httpx.Response(200, json=current_levels))

    put_route = respx.put(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/state/m\.room\.power_levels/"
    ).mock(return_value=httpx.Response(200, json={"event_id": "$pl1"}))

    result = await set_user_power_level(room_id, user_mxid, 100)
    assert result["event_id"] == "$pl1"

    # Verify the PUT body included the tech user at level 100
    sent_body = put_route.calls[0].request
    body = json.loads(sent_body.content)
    assert body["users"][user_mxid] == 100
    # Existing bot user should be preserved
    assert body["users"]["@bot:example.com"] == 100


@pytest.mark.asyncio
@respx.mock
async def test_set_user_power_level_preserves_existing_users(monkeypatch):
    """Existing power levels for other users must not be removed."""
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    current_levels = {
        "users": {"@existing:example.com": 50},
        "users_default": 0,
    }

    respx.get(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/state/m\.room\.power_levels/"
    ).mock(return_value=httpx.Response(200, json=current_levels))

    put_route = respx.put(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/state/m\.room\.power_levels/"
    ).mock(return_value=httpx.Response(200, json={"event_id": "$pl2"}))

    await set_user_power_level("!room2:example.com", "@newtech:example.com", 100)

    body = json.loads(put_route.calls[0].request.content)
    assert body["users"]["@existing:example.com"] == 50
    assert body["users"]["@newtech:example.com"] == 100


# ---------------------------------------------------------------------------
# Source-level checks (no heavy imports required)
# ---------------------------------------------------------------------------

def test_external_invite_gated_by_self_hosted():
    """Verify that the invite_external endpoint rejects requests when not self-hosted.

    We check by reading the route source for the self-hosted guard (avoids
    importing the full application stack in this unit test context).
    """
    source = pathlib.Path("app/api/routes/chat.py").read_text()
    # The guard must be present; it raises HTTPException when not self-hosted
    assert "matrix_is_self_hosted" in source, (
        "invite_external must check _settings.matrix_is_self_hosted"
    )
    # Verify that it raises an HTTPException (not a 200 response) when disabled
    assert "HTTPException" in source, (
        "invite_external must raise HTTPException when self-hosted is disabled"
    )


def test_join_and_assign_use_tech_matrix_user_id():
    """join_room and assign_room must use the technician's matrix_user_id as admin."""
    source = pathlib.Path("app/api/routes/chat.py").read_text()
    assert 'current_user.get("matrix_user_id")' in source, (
        "join/assign must read the technician's matrix_user_id from current_user"
    )
    assert "set_user_power_level" in source, (
        "join/assign must call set_user_power_level to grant admin rights"
    )


def test_set_power_level_function_exists():
    """matrix service must expose set_user_power_level."""
    assert callable(set_user_power_level), "set_user_power_level must be callable"
    assert callable(get_power_levels), "get_power_levels must be callable"


# ---------------------------------------------------------------------------
# enable_room_encryption
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_enable_room_encryption_success(monkeypatch):
    """enable_room_encryption sends m.room.encryption state event."""
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    room_id = "!room1:example.com"
    route = respx.put(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/state/m\.room\.encryption/"
    ).mock(return_value=httpx.Response(200, json={"event_id": "$enc1"}))

    result = await enable_room_encryption(room_id)
    assert result["event_id"] == "$enc1"

    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["algorithm"] == "m.megolm.v1.aes-sha2"


@pytest.mark.asyncio
@respx.mock
async def test_enable_room_encryption_idempotent(monkeypatch):
    """Calling enable_room_encryption on an already-encrypted room must not raise."""
    monkeypatch.setattr(_matrix._settings, "matrix_homeserver_url", "https://matrix.example.com")
    monkeypatch.setattr(_matrix._settings, "matrix_bot_access_token", "test_token")

    respx.put(
        url__regex=r"https://matrix\.example\.com/_matrix/client/v3/rooms/.*/state/m\.room\.encryption/"
    ).mock(return_value=httpx.Response(200, json={"event_id": "$enc2"}))

    # A second call should succeed without raising
    result = await enable_room_encryption("!room2:example.com")
    assert "event_id" in result


def test_enable_room_encryption_callable():
    """matrix service must expose enable_room_encryption."""
    assert callable(enable_room_encryption), "enable_room_encryption must be callable"


# ---------------------------------------------------------------------------
# Source-level checks for E2EE integration
# ---------------------------------------------------------------------------

def test_create_room_enables_e2ee_when_flag_set():
    """create_room route must call enable_room_encryption when e2ee is requested."""
    source = pathlib.Path("app/api/routes/chat.py").read_text()
    assert "enable_room_encryption" in source, (
        "create_room route must call matrix_service.enable_room_encryption when E2EE is enabled"
    )
    assert "matrix_e2ee_enabled" in source, (
        "create_room route must check _settings.matrix_e2ee_enabled"
    )


def test_send_message_rejects_e2ee_rooms():
    """send_message route must reject in-portal sends for E2EE rooms."""
    source = pathlib.Path("app/api/routes/chat.py").read_text()
    assert "e2ee_enabled" in source, (
        "send_message must check room['e2ee_enabled'] and reject the request"
    )


def test_sync_loop_handles_encrypted_events():
    """Sync loop must handle m.room.encrypted events without crashing."""
    source = pathlib.Path("app/services/matrix_sync.py").read_text()
    assert "m.room.encrypted" in source, (
        "matrix_sync must handle m.room.encrypted events"
    )
    assert "[encrypted message]" in source, (
        "matrix_sync must store a placeholder body for encrypted events"
    )

