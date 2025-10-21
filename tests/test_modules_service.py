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

    monkeypatch.setattr(modules.webhook_monitor, "enqueue_event", fake_enqueue_event)
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
    assert captured_event["attempt_immediately"] is False
    assert attempts and attempts[0]["status"] == "succeeded"


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

    monkeypatch.setattr(modules.webhook_monitor, "enqueue_event", fake_enqueue_event)
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

    monkeypatch.setattr(modules.webhook_monitor, "enqueue_event", fake_enqueue_event)
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
    assert captured_event["attempt_immediately"] is False
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

    monkeypatch.setattr(modules.webhook_monitor, "enqueue_event", fake_enqueue_event)
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

    monkeypatch.setattr(modules.webhook_monitor, "enqueue_event", fake_enqueue_event)
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
    assert captured_event["attempt_immediately"] is False


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

    monkeypatch.setattr(modules.webhook_monitor, "enqueue_event", fake_enqueue_event)
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

    assert result["event_id"] == 15
    assert result["status"] == "succeeded"
    assert result["topic"] == "alerts"
    assert captured_event["attempt_immediately"] is False
