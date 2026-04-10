"""Tests for the Password Pusher module integration."""
import asyncio
import json

import httpx
import pytest

from app.services import modules
from app.services.modules import DEFAULT_MODULES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop(*args, **kwargs):
    return None


class _AsyncClientFactory:
    def __init__(self, response):
        self._response = response
        self.captured_kwargs: dict[str, object] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, data=None, headers=None):
        self.captured_kwargs = {"url": url, "json": json, "data": data, "headers": headers}
        return self._response


class FakeResponse:
    def __init__(self, status_code=200, response_text=None):
        self.status_code = status_code
        self._text = response_text or json.dumps({
            "url_token": "abc123",
            "html_url": "https://pwpush.com/p/abc123",
            "expired": False,
            "deleted": False,
            "expire_after_days": 7,
            "expire_after_views": 5,
        })
        self.request = httpx.Request("POST", "https://pwpush.com/p.json")

    @property
    def text(self):
        return self._text

    def json(self):
        return json.loads(self._text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "HTTP error",
                request=self.request,
                response=self,
            )
        return None


def _make_fake_monitor_and_repo():
    """Return monkeypatch helpers for webhook_monitor and webhook_repo."""
    captured_event: dict[str, object] = {}
    fake_event_state: dict[str, object] = {"id": 1, "status": "pending", "attempt_count": 0}
    attempts: list[dict[str, object]] = []

    async def fake_create_manual_event(**kwargs):
        captured_event.update(kwargs)
        return dict(fake_event_state)

    async def fake_record_attempt(**kwargs):
        attempts.append(kwargs)

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update({
            "status": "succeeded",
            "attempt_count": attempt_number,
            "response_status": response_status,
            "response_body": response_body,
        })

    async def fake_mark_event_failed(event_id, *, attempt_number, error_message, response_status, response_body):
        fake_event_state.update({
            "status": "failed",
            "attempt_count": attempt_number,
            "last_error": error_message,
        })

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    return (
        captured_event,
        fake_event_state,
        attempts,
        fake_create_manual_event,
        fake_record_attempt,
        fake_mark_event_completed,
        fake_mark_event_failed,
        fake_get_event,
    )


# ---------------------------------------------------------------------------
# Module registration
# ---------------------------------------------------------------------------

def test_password_pusher_in_default_modules():
    slugs = {m["slug"] for m in DEFAULT_MODULES}
    assert "password-pusher" in slugs


def test_password_pusher_default_settings():
    module = next(m for m in DEFAULT_MODULES if m["slug"] == "password-pusher")
    settings = module["settings"]
    assert settings["base_url"] == "https://pwpush.com"
    assert settings["expire_after_days"] == 7
    assert settings["expire_after_views"] == 5
    assert settings["deletable_by_viewer"] is True
    assert settings["retrieval_step"] is False


def test_password_pusher_has_payload_schema():
    schema = modules.get_action_payload_schema("password-pusher")
    assert schema is not None
    field_names = {f["name"] for f in schema["fields"]}
    assert "payload" in field_names
    assert "expire_after_days" in field_names
    assert "expire_after_views" in field_names


def test_password_pusher_payload_required_field():
    """validate_action_payload should raise when payload is missing."""
    with pytest.raises(ValueError, match="payload"):
        modules.validate_action_payload("password-pusher", {})


def test_password_pusher_payload_validates_ok():
    modules.validate_action_payload("password-pusher", {"payload": "secret123"})


# ---------------------------------------------------------------------------
# Settings coercion
# ---------------------------------------------------------------------------

def test_coerce_settings_password_pusher_defaults():
    result = modules._coerce_settings("password-pusher", {})  # type: ignore[attr-defined]
    assert result["base_url"] == "https://pwpush.com"
    assert result["expire_after_days"] == 7
    assert result["expire_after_views"] == 5
    assert result["deletable_by_viewer"] is True
    assert result["retrieval_step"] is False


def test_coerce_settings_password_pusher_preserves_api_key(monkeypatch):
    existing = {
        "slug": "password-pusher",
        "settings": {"api_key": "existing-token", "user_email": "admin@example.com"},
    }
    result = modules._coerce_settings("password-pusher", {}, existing)  # type: ignore[attr-defined]
    assert result["api_key"] == "existing-token"
    assert result["user_email"] == "admin@example.com"


def test_coerce_settings_password_pusher_blank_api_key_preserves_existing():
    existing = {
        "slug": "password-pusher",
        "settings": {"api_key": "existing-token"},
    }
    result = modules._coerce_settings("password-pusher", {"api_key": ""}, existing)  # type: ignore[attr-defined]
    assert result["api_key"] == "existing-token"


def test_coerce_settings_password_pusher_clamps_expire_after_days():
    # expire_after_days=0 is clamped to minimum=1 by _coerce_int
    result = modules._coerce_settings("password-pusher", {"expire_after_days": 0})  # type: ignore[attr-defined]
    assert result["expire_after_days"] == 1  # clamped to minimum


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def test_password_pusher_redacts_api_key(monkeypatch):
    async def fake_list_modules():
        return [
            {
                "slug": "password-pusher",
                "enabled": True,
                "settings": {
                    "base_url": "https://pwpush.com",
                    "api_key": "super-secret-token",
                    "user_email": "admin@example.com",
                },
            }
        ]

    monkeypatch.setattr(modules.module_repo, "list_modules", fake_list_modules)
    result = asyncio.run(modules.list_modules())
    assert result[0]["settings"]["api_key"] == "********"
    assert result[0]["settings"]["user_email"] == "admin@example.com"


# ---------------------------------------------------------------------------
# _invoke_password_pusher – success
# ---------------------------------------------------------------------------

def test_invoke_password_pusher_success(monkeypatch):
    (
        captured_event,
        fake_event_state,
        attempts,
        fake_create_manual_event,
        fake_record_attempt,
        fake_mark_event_completed,
        fake_mark_event_failed,
        fake_get_event,
    ) = _make_fake_monitor_and_repo()

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_password_pusher(  # type: ignore[attr-defined]
            {"base_url": "https://pwpush.com", "expire_after_days": 7, "expire_after_views": 5,
             "deletable_by_viewer": True, "retrieval_step": False, "api_key": "", "user_email": ""},
            {"payload": "MySecretPassword123!"},
        )
    )

    assert result["status"] == "succeeded"
    assert result["push_url"] == "https://pwpush.com/p/abc123"
    assert result["url_token"] == "abc123"
    assert captured_event["name"] == "module.password-pusher.push"
    assert captured_event["target_url"] == "https://pwpush.com/p.json"
    # secret payload must NOT appear in webhook event tracking
    assert "payload" not in str(captured_event.get("payload", ""))
    # Verify the JSON body sent to pwpush
    assert client_factory.captured_kwargs["json"]["password"]["payload"] == "MySecretPassword123!"
    assert client_factory.captured_kwargs["json"]["password"]["expire_after_days"] == 7


def test_invoke_password_pusher_with_auth_headers(monkeypatch):
    (_, _, _, fake_create_manual_event, fake_record_attempt,
     fake_mark_event_completed, fake_mark_event_failed, fake_get_event) = _make_fake_monitor_and_repo()

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    asyncio.run(
        modules._invoke_password_pusher(  # type: ignore[attr-defined]
            {"base_url": "https://pwpush.com", "expire_after_days": 7, "expire_after_views": 5,
             "deletable_by_viewer": True, "retrieval_step": False,
             "api_key": "my-api-key", "user_email": "user@example.com"},
            {"payload": "secret"},
        )
    )

    sent_headers = client_factory.captured_kwargs["headers"]
    assert sent_headers["X-User-Email"] == "user@example.com"
    assert sent_headers["X-User-Token"] == "my-api-key"


def test_invoke_password_pusher_bearer_token_when_no_email(monkeypatch):
    (_, _, _, fake_create_manual_event, fake_record_attempt,
     fake_mark_event_completed, fake_mark_event_failed, fake_get_event) = _make_fake_monitor_and_repo()

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    asyncio.run(
        modules._invoke_password_pusher(  # type: ignore[attr-defined]
            {"base_url": "https://pwpush.com", "expire_after_days": 7, "expire_after_views": 5,
             "deletable_by_viewer": True, "retrieval_step": False,
             "api_key": "bearer-token", "user_email": ""},
            {"payload": "secret"},
        )
    )

    sent_headers = client_factory.captured_kwargs["headers"]
    assert sent_headers["Authorization"] == "Bearer bearer-token"
    assert "X-User-Token" not in sent_headers


def test_invoke_password_pusher_constructs_url_from_token(monkeypatch):
    """When html_url is absent, the URL should be constructed from url_token."""
    (_, fake_event_state, _, fake_create_manual_event, fake_record_attempt,
     fake_mark_event_completed, fake_mark_event_failed, fake_get_event) = _make_fake_monitor_and_repo()

    no_html_url_response = FakeResponse(
        response_text=json.dumps({"url_token": "xyz789", "expired": False})
    )
    client_factory = _AsyncClientFactory(no_html_url_response)

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_password_pusher(  # type: ignore[attr-defined]
            {"base_url": "https://pwpush.example.com", "expire_after_days": 7, "expire_after_views": 5,
             "deletable_by_viewer": True, "retrieval_step": False, "api_key": "", "user_email": ""},
            {"payload": "secret"},
        )
    )

    assert result["push_url"] == "https://pwpush.example.com/p/xyz789"
    assert result["url_token"] == "xyz789"


def test_invoke_password_pusher_payload_overrides_settings(monkeypatch):
    (_, _, _, fake_create_manual_event, fake_record_attempt,
     fake_mark_event_completed, fake_mark_event_failed, fake_get_event) = _make_fake_monitor_and_repo()

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    asyncio.run(
        modules._invoke_password_pusher(  # type: ignore[attr-defined]
            {"base_url": "https://pwpush.com", "expire_after_days": 7, "expire_after_views": 5,
             "deletable_by_viewer": True, "retrieval_step": False, "api_key": "", "user_email": ""},
            {"payload": "secret", "expire_after_days": 3, "expire_after_views": 2,
             "deletable_by_viewer": False, "retrieval_step": True, "note": "For IT only"},
        )
    )

    body = client_factory.captured_kwargs["json"]["password"]
    assert body["expire_after_days"] == 3
    assert body["expire_after_views"] == 2
    assert body["deletable_by_viewer"] is False
    assert body["retrieval_step"] is True
    assert body["note"] == "For IT only"


# ---------------------------------------------------------------------------
# _invoke_password_pusher – error handling
# ---------------------------------------------------------------------------

def test_invoke_password_pusher_missing_payload():
    with pytest.raises(ValueError, match="payload"):
        asyncio.run(
            modules._invoke_password_pusher(  # type: ignore[attr-defined]
                {"base_url": "https://pwpush.com", "expire_after_days": 7, "expire_after_views": 5,
                 "deletable_by_viewer": True, "retrieval_step": False, "api_key": "", "user_email": ""},
                {},
            )
        )


def test_invoke_password_pusher_http_error(monkeypatch):
    (_, _, _, fake_create_manual_event, fake_record_attempt,
     _, fake_mark_event_failed, fake_get_event) = _make_fake_monitor_and_repo()

    async def fake_mark_event_completed(*a, **kw):
        pass

    error_response = FakeResponse(status_code=401, response_text='{"error": "Unauthorized"}')
    client_factory = _AsyncClientFactory(error_response)

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_password_pusher(  # type: ignore[attr-defined]
            {"base_url": "https://pwpush.com", "expire_after_days": 7, "expire_after_views": 5,
             "deletable_by_viewer": True, "retrieval_step": False, "api_key": "", "user_email": ""},
            {"payload": "secret"},
        )
    )

    assert result["status"] == "failed"
    assert result["push_url"] is None


# ---------------------------------------------------------------------------
# update_module preserves api_key when blank
# ---------------------------------------------------------------------------

def test_update_module_preserves_password_pusher_api_key_when_blank(monkeypatch):
    stored = {
        "slug": "password-pusher",
        "enabled": True,
        "settings": {
            "base_url": "https://pwpush.com",
            "api_key": "existing-api-key",
            "user_email": "admin@example.com",
        },
    }

    async def fake_get_module(slug):
        return stored

    async def fake_update_module(slug, *, enabled=None, settings=None):
        if settings:
            stored["settings"].update(settings)
        if enabled is not None:
            stored["enabled"] = enabled
        return {"slug": slug, "enabled": stored["enabled"], "settings": dict(stored["settings"])}

    async def fake_broadcast_refresh(**kwargs):
        pass

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules.module_repo, "update_module", fake_update_module)
    monkeypatch.setattr(modules.refresh_notifier, "broadcast_refresh", fake_broadcast_refresh)

    result = asyncio.run(
        modules.update_module(
            "password-pusher",
            enabled=True,
            settings={"base_url": "https://pwpush.example.com", "api_key": ""},
        )
    )

    # api_key must be preserved and redacted in the result
    assert result["settings"]["api_key"] == "********"
    assert stored["settings"]["api_key"] == "existing-api-key"
