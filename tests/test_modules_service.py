import asyncio
import json

import httpx
import pytest

from app.services import modules


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
        self.captured_kwargs = {
            "url": url,
            "json": json,
            "data": data,
            "headers": headers,
        }
        return self._response

    async def request(self, method, url, json=None, headers=None):
        self.captured_kwargs = {
            "method": method,
            "url": url,
            "json": json,
            "headers": headers,
        }
        return self._response


def test_invoke_ollama_records_event_success(monkeypatch):
    captured_event: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured_event.update(kwargs)
        return {"id": 1, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 1,
        "status": "pending",
        "attempt_count": 0,
    }
    attempts: list[dict[str, object]] = []

    async def fake_record_attempt(**kwargs):
        attempts.append(kwargs)

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self._text = json.dumps({"result": "ok"})
            self.request = httpx.Request("POST", "http://example.com")

        @property
        def text(self):
            return self._text

        def json(self):
            return json.loads(self._text)

        def raise_for_status(self):
            return None

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_ollama({"base_url": "http://127.0.0.1:11434"}, {"prompt": "Hi"})
    )

    assert result["event_id"] == 1
    assert result["status"] == "succeeded"
    assert result["response"] == {"result": "ok"}
    assert captured_event.get("max_attempts") == 1
    assert "attempt_immediately" not in captured_event


def test_update_module_preserves_tacticalrmm_api_key_when_blank(monkeypatch):
    stored = {
        "slug": "tacticalrmm",
        "enabled": True,
        "settings": {"base_url": "https://rmm.example.com", "api_key": "secret-key", "verify_ssl": True},
    }
    captured: dict[str, object] = {}

    async def fake_get_module(slug: str):
        assert slug == "tacticalrmm"
        return stored

    async def fake_update_module(slug: str, *, enabled=None, settings=None):
        assert slug == "tacticalrmm"
        captured.update(settings or {})
        if settings:
            stored["settings"].update(settings)
        if enabled is not None:
            stored["enabled"] = enabled
        return {"slug": slug, "enabled": stored["enabled"], "settings": dict(stored["settings"])}

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules.module_repo, "update_module", fake_update_module)

    result = asyncio.run(
        modules.update_module(
            "tacticalrmm",
            enabled=True,
            settings={"base_url": "https://rmm.example.com", "api_key": "", "verify_ssl": "true"},
        )
    )

    assert captured["api_key"] == "secret-key"
    assert result["settings"]["api_key"] == "********"
    assert captured["verify_ssl"] is True


def test_update_module_preserves_ntfy_auth_token_when_blank(monkeypatch):
    stored = {
        "slug": "ntfy",
        "enabled": True,
        "settings": {"base_url": "https://ntfy.sh", "topic": "alerts", "auth_token": "existing-token"},
    }
    captured: dict[str, object] = {}

    async def fake_get_module(slug: str):
        assert slug == "ntfy"
        return stored

    async def fake_update_module(slug: str, *, enabled=None, settings=None):
        assert slug == "ntfy"
        captured.update(settings or {})
        if settings:
            stored["settings"].update(settings)
        if enabled is not None:
            stored["enabled"] = enabled
        return {"slug": slug, "enabled": stored["enabled"], "settings": dict(stored["settings"])}

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules.module_repo, "update_module", fake_update_module)

    result = asyncio.run(
        modules.update_module(
            "ntfy",
            enabled=False,
            settings={"base_url": " https://ntfy.example.com/ ", "topic": "critical", "auth_token": ""},
        )
    )

    assert captured["auth_token"] == "existing-token"
    assert captured["base_url"] == "https://ntfy.example.com"
    assert result["settings"]["auth_token"] == "********"


def test_list_modules_redacts_tacticalrmm_and_ntfy(monkeypatch):
    async def fake_list_modules():
        return [
            {
                "slug": "tacticalrmm",
                "enabled": True,
                "settings": {"base_url": "https://rmm.example.com", "api_key": "secret-key"},
            },
            {
                "slug": "ntfy",
                "enabled": True,
                "settings": {"base_url": "https://ntfy.example.com", "topic": "alerts", "auth_token": "existing-token"},
            },
        ]

    monkeypatch.setattr(modules.module_repo, "list_modules", fake_list_modules)

    module_list = asyncio.run(modules.list_modules())

    assert module_list[0]["settings"]["api_key"] == "********"
    assert module_list[1]["settings"]["auth_token"] == "********"


def test_invoke_ollama_uses_default_model_when_blank(monkeypatch):
    async def fake_enqueue_event(**kwargs):
        return {"id": 12, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 12,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state["attempt_count"] = kwargs["attempt_number"]

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self._text = json.dumps({"result": "ok"})
            self.request = httpx.Request("POST", "http://example.com")

        @property
        def text(self):
            return self._text

        def raise_for_status(self):
            return None

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_ollama(
            {"base_url": "  http://127.0.0.1:11434/  ", "model": "   "},
            {"prompt": "Hi"},
        )
    )

    assert result["status"] == "succeeded"
    assert client_factory.captured_kwargs["json"]["model"] == modules._DEFAULT_OLLAMA_MODEL
    assert client_factory.captured_kwargs["url"] == f"{modules._DEFAULT_OLLAMA_BASE_URL}/api/generate"


def test_invoke_ollama_records_event_failure(monkeypatch):
    async def fake_enqueue_event(**kwargs):
        return {"id": 4, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 4,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state["attempt_count"] = kwargs["attempt_number"]
        fake_event_state["response_status"] = kwargs["response_status"]
        fake_event_state["response_body"] = kwargs["response_body"]
        fake_event_state["last_error"] = kwargs["error_message"]

    async def fake_mark_event_failed(event_id, *, attempt_number, error_message, response_status, response_body):
        fake_event_state.update(
            {
                "status": "failed",
                "attempt_count": attempt_number,
                "last_error": error_message,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    request = httpx.Request("POST", "http://example.com")

    class FakeResponse:
        def __init__(self):
            self.status_code = 500
            self.text = "error"
            self.request = request

        def raise_for_status(self):
            raise httpx.HTTPStatusError("boom", request=request, response=self)

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(modules._invoke_ollama({"base_url": "http://localhost"}, {"prompt": "Hi"}))

    assert result["event_id"] == 4
    assert result["status"] == "failed"
    assert result["last_error"].startswith("HTTP 500")


def test_invoke_smtp_records_success(monkeypatch):
    captured_event: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured_event.update(kwargs)
        return {"id": 7, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 7,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state.update(
            {
                "attempt_count": kwargs["attempt_number"],
                "response_status": kwargs["response_status"],
                "response_body": kwargs["response_body"],
            }
        )

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    async def fake_send_email(**kwargs):
        captured_event["email_kwargs"] = kwargs
        return True, {"id": 70, "status": "succeeded"}

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.email_service, "send_email", fake_send_email)

    result = asyncio.run(
        modules._invoke_smtp(
            {"from_address": "alerts@example.com"},
            {"recipients": ["user@example.com"], "subject": "Test"},
        )
    )

    assert result["event_id"] == 7
    assert result["status"] == "succeeded"
    assert result["recipients"] == ["user@example.com"]
    assert captured_event.get("max_attempts") == 1
    assert "attempt_immediately" not in captured_event
    assert result["email_event_id"] == 70
    assert "email_kwargs" in captured_event


def test_invoke_smtp_records_failure(monkeypatch):
    async def fake_enqueue_event(**kwargs):
        return {"id": 9, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 9,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state.update(
            {
                "attempt_count": kwargs["attempt_number"],
                "response_status": kwargs["response_status"],
                "response_body": kwargs["response_body"],
                "last_error": kwargs["error_message"],
            }
        )

    async def fake_mark_event_failed(event_id, *, attempt_number, error_message, response_status, response_body):
        fake_event_state.update(
            {
                "status": "failed",
                "attempt_count": attempt_number,
                "last_error": error_message,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    async def fake_send_email(**kwargs):
        return False, None

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", fake_mark_event_failed)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.email_service, "send_email", fake_send_email)

    result = asyncio.run(
        modules._invoke_smtp(
            {"from_address": "alerts@example.com"},
            {"recipients": ["user@example.com"], "subject": "Test"},
        )
    )

    assert result["event_id"] == 9
    assert result["status"] == "failed"
    assert "last_error" in result
    assert result.get("email_event_id") is None


def test_invoke_tacticalrmm_records_success(monkeypatch):
    captured_event: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured_event.update(kwargs)
        return {"id": 11, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 11,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state.update(
            {
                "attempt_count": kwargs["attempt_number"],
                "response_status": kwargs["response_status"],
                "response_body": kwargs["response_body"],
            }
        )

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    class FakeResponse:
        def __init__(self):
            self.status_code = 201
            self._text = json.dumps({"ok": True})
            self.request = httpx.Request("POST", "http://example.com")

        @property
        def text(self):
            return self._text

        def json(self):
            return json.loads(self._text)

        def raise_for_status(self):
            return None

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_tacticalrmm(
            {"base_url": "https://rmm.example.com", "api_key": "abc", "verify_ssl": False},
            {"endpoint": "/api/run", "body": {"id": 1}},
        )
    )

    assert result["event_id"] == 11
    assert result["status"] == "succeeded"
    assert result["status_code"] == 201
    assert client_factory.captured_kwargs["headers"].get("X-API-KEY") == "abc"
    assert captured_event.get("max_attempts") == 1
    assert "attempt_immediately" not in captured_event


def test_push_companies_to_tacticalrmm_requires_module(monkeypatch):
    async def fake_get_module(slug):
        return None

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)

    with pytest.raises(ValueError):
        asyncio.run(modules.push_companies_to_tacticalrmm())


def test_push_companies_to_tacticalrmm_validates_configuration(monkeypatch):
    async def fake_get_module(slug):
        return {"slug": slug, "settings": {"base_url": "", "api_key": ""}}

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)

    with pytest.raises(ValueError):
        asyncio.run(modules.push_companies_to_tacticalrmm())


def test_push_companies_to_tacticalrmm_creates_clients_and_sites(monkeypatch):
    module_record = {
        "slug": "tacticalrmm",
        "settings": {"base_url": "https://rmm.example.com", "api_key": "token", "verify_ssl": True},
    }

    async def fake_get_module(slug):
        return module_record

    async def fake_list_companies():
        return [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Beta"},
        ]

    call_payloads: list[dict[str, object]] = []
    responses = [
        {"status": "succeeded", "response": [{"id": 21, "name": "Beta", "sites": [{"id": 5, "name": "North"}]}]},
        {"status": "succeeded", "event_id": 101},
        {"status": "succeeded", "event_id": 102},
    ]

    async def fake_invoke(settings, payload, event_future=None):
        call_payloads.append(payload)
        if responses:
            return responses.pop(0)
        raise AssertionError("Unexpected TacticalRMM invocation")

    monkeypatch.setattr(modules.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules.company_repo, "list_companies", fake_list_companies)
    monkeypatch.setattr(modules, "_invoke_tacticalrmm", fake_invoke)

    summary = asyncio.run(modules.push_companies_to_tacticalrmm())

    assert summary["created_clients"] == ["Alpha"]
    assert summary["existing_clients"] == ["Beta"]
    assert not summary["errors"]

    created_sites = summary["created_sites"]
    assert any(
        entry["company"] == "Alpha" and entry["action"] == "created_with_client"
        for entry in created_sites
    )
    assert any(
        entry["company"] == "Beta" and entry["action"] == "created_for_existing_client"
        for entry in created_sites
    )

    assert call_payloads[0] == {"endpoint": "/clients/", "method": "GET"}
    assert call_payloads[1]["endpoint"] == "/clients/"
    assert call_payloads[1]["method"] == "POST"
    assert call_payloads[2]["endpoint"] == "/clients/sites/"
    assert call_payloads[2]["method"] == "POST"
    assert len(call_payloads) == 3
def test_invoke_ntfy_records_success(monkeypatch):
    captured_event: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured_event.update(kwargs)
        return {"id": 15, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 15,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state.update(
            {
                "attempt_count": kwargs["attempt_number"],
                "response_status": kwargs["response_status"],
                "response_body": kwargs["response_body"],
            }
        )

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.text = "ok"
            self.request = httpx.Request("POST", "http://example.com")

        def raise_for_status(self):
            return None

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    result = asyncio.run(
        modules._invoke_ntfy(
            {"base_url": "https://ntfy.sh", "topic": "alerts"},
            {"message": "hello", "priority": "high"},
        )
    )

    request_kwargs = client_factory.captured_kwargs

    assert result["event_id"] == 15
    assert result["status"] == "succeeded"
    assert result["topic"] == "alerts"
    assert result["priority"] == "high"
    assert result["title"] == "Automation event"
    assert captured_event.get("max_attempts") == 1
    assert "attempt_immediately" not in captured_event
    assert captured_event.get("payload") == {
        "topic": "alerts",
        "message": "hello",
        "priority": "high",
        "title": "Automation event",
        "headers": {
            "Title": "Automation event",
            "Priority": "high",
        },
    }
    assert request_kwargs["url"] == "https://ntfy.sh/alerts"
    assert request_kwargs["data"] == b"hello"
    assert request_kwargs["headers"]["Title"] == "Automation event"
    assert request_kwargs["headers"]["Priority"] == "high"


def test_invoke_ntfy_payload_overrides_defaults(monkeypatch):
    captured_event: dict[str, object] = {}

    async def fake_enqueue_event(**kwargs):
        captured_event.update(kwargs)
        return {"id": 18, "status": "pending", "attempt_count": 0}

    fake_event_state = {
        "id": 18,
        "status": "pending",
        "attempt_count": 0,
    }

    async def fake_record_attempt(**kwargs):
        fake_event_state.update(
            {
                "attempt_count": kwargs["attempt_number"],
                "response_status": kwargs["response_status"],
                "response_body": kwargs["response_body"],
            }
        )

    async def fake_mark_event_completed(event_id, *, attempt_number, response_status, response_body):
        fake_event_state.update(
            {
                "status": "succeeded",
                "attempt_count": attempt_number,
                "response_status": response_status,
                "response_body": response_body,
            }
        )

    async def fake_get_event(event_id):
        return dict(fake_event_state)

    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.text = "ok"
            self.request = httpx.Request("POST", "http://example.com")

        def raise_for_status(self):
            return None

    client_factory = _AsyncClientFactory(FakeResponse())

    monkeypatch.setattr(modules.webhook_monitor, "create_manual_event", fake_enqueue_event)
    monkeypatch.setattr(modules.webhook_repo, "record_attempt", fake_record_attempt)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_completed", fake_mark_event_completed)
    monkeypatch.setattr(modules.webhook_repo, "mark_event_failed", _noop)
    monkeypatch.setattr(modules.webhook_repo, "get_event", fake_get_event)
    monkeypatch.setattr(modules.httpx, "AsyncClient", lambda *a, **kw: client_factory)

    payload = {
        "base_url": "https://alerts.example.com",
        "topic": "custom-topic",
        "auth_token": "payload-token",
        "title": "Ticket created",
        "message": {"text": "Ticket #1"},
        "priority": 5,
        "tags": ["tickets", "created"],
        "click": "https://portal.example.com/tickets/1",
        "headers": {"Custom-Header": "value"},
    }

    result = asyncio.run(
        modules._invoke_ntfy(
            {"base_url": "https://ntfy.sh", "topic": "alerts", "auth_token": "settings-token"},
            payload,
        )
    )

    request_kwargs = client_factory.captured_kwargs
    expected_message = json.dumps({"text": "Ticket #1"}).encode("utf-8")

    assert request_kwargs["url"] == "https://alerts.example.com/custom-topic"
    assert request_kwargs["data"] == expected_message
    assert request_kwargs["headers"]["Title"] == "Ticket created"
    assert request_kwargs["headers"]["Priority"] == "5"
    assert request_kwargs["headers"]["Tags"] == "tickets,created"
    assert request_kwargs["headers"]["Click"] == "https://portal.example.com/tickets/1"
    assert request_kwargs["headers"]["Authorization"] == "Bearer payload-token"
    assert request_kwargs["headers"]["Custom-Header"] == "value"

    assert captured_event.get("payload") == {
        "topic": "custom-topic",
        "message": json.dumps({"text": "Ticket #1"}),
        "priority": "5",
        "title": "Ticket created",
        "headers": {
            "Title": "Ticket created",
            "Priority": "5",
            "Tags": "tickets,created",
            "Click": "https://portal.example.com/tickets/1",
            "Authorization": "Bearer payload-token",
            "Custom-Header": "value",
        },
    }

    assert result["status"] == "succeeded"
    assert result["topic"] == "custom-topic"
    assert result["priority"] == "5"
    assert result["title"] == "Ticket created"
    assert captured_event.get("max_attempts") == 1
    assert "attempt_immediately" not in captured_event
