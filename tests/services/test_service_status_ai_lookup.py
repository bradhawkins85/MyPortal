"""Tests for run_ai_lookup_for_service using trigger_module."""
import asyncio

import pytest

from app.services import service_status as service_status_svc


def _make_service(overrides=None):
    base = {
        "id": 1,
        "name": "Test Service",
        "status": "operational",
        "status_message": "",
        "ai_lookup_enabled": True,
        "ai_lookup_url": "https://example.com/status",
        "ai_lookup_prompt": "Is this service healthy?",
        "ai_lookup_model_override": None,
        "ai_lookup_interval_minutes": 60,
        "ai_lookup_last_checked_at": None,
        "ai_lookup_last_status": None,
        "ai_lookup_last_message": None,
    }
    if overrides:
        base.update(overrides)
    return base


def test_run_ai_lookup_routes_through_trigger_module(monkeypatch):
    """run_ai_lookup_for_service should use trigger_module, not a direct HTTP call."""
    service = _make_service()
    captured_calls: list[dict] = []

    async def fake_get_service(service_id):
        return service

    async def fake_get(url, **kwargs):
        class FakeResponse:
            text = '{"status": "ok"}'
            status_code = 200

            def raise_for_status(self):
                pass

        return FakeResponse()

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            return await fake_get(url, **kwargs)

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        captured_calls.append({"slug": slug, "payload": payload})
        return {
            "status": "succeeded",
            "response": {"response": '{"status": "operational", "message": "All good"}'},
        }

    async def fake_update_service(service_id, updates):
        pass

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert result["error"] is None
    assert len(captured_calls) == 1
    assert captured_calls[0]["slug"] == "ollama"
    assert "prompt" in captured_calls[0]["payload"]
    assert captured_calls[0]["payload"].get("format") == "json"


def test_run_ai_lookup_passes_model_override(monkeypatch):
    """When ai_lookup_model_override is set, it is forwarded to trigger_module."""
    service = _make_service({"ai_lookup_model_override": "llama3.2"})
    captured_payload: list[dict] = []

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "<html>OK</html>"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        captured_payload.append(payload or {})
        return {
            "status": "succeeded",
            "response": {"response": '{"status": "operational", "message": "OK"}'},
        }

    async def fake_update_service(service_id, updates):
        pass

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert result["error"] is None
    assert captured_payload[0].get("model") == "llama3.2"


def test_run_ai_lookup_returns_error_when_module_disabled(monkeypatch):
    """When the Ollama module is disabled, an appropriate error is returned."""
    service = _make_service()

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "OK"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        return {"status": "skipped", "reason": "Module disabled", "module": "ollama"}

    async def fake_update_service(service_id, updates):
        pass

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert result["changed"] is False
    assert "not enabled" in (result.get("error") or "")


def test_run_ai_lookup_returns_error_when_ollama_fails(monkeypatch):
    """When the Ollama call returns a failure status, an error is returned."""
    service = _make_service()
    update_calls: list[dict] = []

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "OK"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        return {
            "status": "failed",
            "last_error": "HTTP 404",
        }

    async def fake_update_service(service_id, updates):
        update_calls.append(updates)

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert result["changed"] is False
    assert "Ollama request failed" in (result.get("error") or "")
    assert "HTTP 404" in (result.get("error") or "")
    # The last checked timestamp should still be updated on failure
    assert len(update_calls) == 1
    assert "ai_lookup_last_checked_at" in update_calls[0]


def test_run_ai_lookup_no_model_override_in_payload(monkeypatch):
    """When there is no model override, 'model' key should not be in the payload."""
    service = _make_service({"ai_lookup_model_override": None})
    captured_payload: list[dict] = []

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "OK"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        captured_payload.append(payload or {})
        return {
            "status": "succeeded",
            "response": {"response": '{"status": "operational", "message": "OK"}'},
        }

    async def fake_update_service(service_id, updates):
        pass

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert "model" not in captured_payload[0]


# ---------------------------------------------------------------------------
# Tests for _parse_ai_status_response and _extract_json_object
# ---------------------------------------------------------------------------

class TestParseAiStatusResponse:
    """Tests for robust parsing of AI / Ollama output."""

    def test_plain_json(self):
        status, msg = service_status_svc._parse_ai_status_response(
            '{"status": "operational", "message": "All good"}'
        )
        assert status == "operational"
        assert msg == "All good"

    def test_markdown_fenced_json(self):
        text = '```json\n{"status": "degraded", "message": "High latency"}\n```'
        status, msg = service_status_svc._parse_ai_status_response(text)
        assert status == "degraded"
        assert msg == "High latency"

    def test_markdown_fenced_no_tag(self):
        text = '```\n{"status": "outage", "message": "Service down"}\n```'
        status, msg = service_status_svc._parse_ai_status_response(text)
        assert status == "outage"
        assert msg == "Service down"

    def test_json_with_preamble_text(self):
        text = 'Based on my analysis:\n{"status": "operational", "message": "OK"}'
        status, msg = service_status_svc._parse_ai_status_response(text)
        assert status == "operational"
        assert msg == "OK"

    def test_json_with_surrounding_text(self):
        text = 'Here is the result: {"status": "maintenance", "message": "Planned downtime"} -- end'
        status, msg = service_status_svc._parse_ai_status_response(text)
        assert status == "maintenance"
        assert msg == "Planned downtime"

    def test_keyword_fallback_operational(self):
        status, msg = service_status_svc._parse_ai_status_response(
            "The service is currently operational."
        )
        assert status == "operational"
        assert msg is None

    def test_keyword_fallback_outage(self):
        status, msg = service_status_svc._parse_ai_status_response(
            "There is an outage affecting users."
        )
        assert status == "outage"
        assert msg is None

    def test_empty_string(self):
        assert service_status_svc._parse_ai_status_response("") == (None, None)

    def test_unparseable_text(self):
        assert service_status_svc._parse_ai_status_response(
            "I'm not sure what's happening."
        ) == (None, None)


class TestExtractJsonObject:
    """Tests for _extract_json_object helper."""

    def test_plain_json(self):
        result = service_status_svc._extract_json_object('{"a": 1}')
        assert result == {"a": 1}

    def test_markdown_fenced(self):
        result = service_status_svc._extract_json_object(
            '```json\n{"a": 1}\n```'
        )
        assert result == {"a": 1}

    def test_embedded_json(self):
        result = service_status_svc._extract_json_object(
            'prefix {"a": 1} suffix'
        )
        assert result == {"a": 1}

    def test_no_json(self):
        assert service_status_svc._extract_json_object("no json here") is None


# ---------------------------------------------------------------------------
# Test that unparseable AI responses return an error
# ---------------------------------------------------------------------------

def test_run_ai_lookup_returns_error_when_response_unparseable(monkeypatch):
    """When the AI response can't be parsed into a valid status, return an error."""
    service = _make_service()
    update_calls: list[dict] = []

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "OK"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        return {
            "status": "succeeded",
            "response": {"response": "I cannot determine the status of this service."},
        }

    async def fake_update_service(service_id, updates):
        update_calls.append(updates)

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert result["changed"] is False
    assert result.get("error") is not None
    assert "could not be interpreted" in result["error"]
    # The last checked timestamp should still be updated
    assert len(update_calls) == 1
    assert "ai_lookup_last_checked_at" in update_calls[0]


def test_run_ai_lookup_parses_markdown_wrapped_json(monkeypatch):
    """AI responses with JSON in markdown code blocks should be parsed correctly."""
    service = _make_service()

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "<html>status page</html>"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        return {
            "status": "succeeded",
            "response": {
                "response": '```json\n{"status": "degraded", "message": "High latency detected"}\n```',
            },
        }

    async def fake_update_service(service_id, updates):
        pass

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient())
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    result = asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert result["error"] is None
    assert result["status"] == "degraded"
    assert result["message"] == "High latency detected"
    assert result["changed"] is True


def test_run_ai_lookup_sends_browser_headers(monkeypatch):
    """URL fetch should include browser-like headers to avoid cutdown responses."""
    service = _make_service()
    captured_kwargs: list[dict] = []

    async def fake_get_service(service_id):
        return service

    class FakeHttpxClient:
        def __init__(self, **kwargs):
            captured_kwargs.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            class FakeResp:
                text = "OK"
                status_code = 200

                def raise_for_status(self):
                    pass

            return FakeResp()

    async def fake_trigger_module(slug, payload=None, *, background=True, on_complete=None):
        return {
            "status": "succeeded",
            "response": {"response": '{"status": "operational", "message": "OK"}'},
        }

    async def fake_update_service(service_id, updates):
        pass

    monkeypatch.setattr(service_status_svc, "get_service", fake_get_service)
    monkeypatch.setattr(service_status_svc, "_validate_lookup_url", lambda url: url)
    monkeypatch.setattr(service_status_svc.httpx, "AsyncClient", lambda *a, **kw: FakeHttpxClient(**kw))
    monkeypatch.setattr(service_status_svc.modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(service_status_svc.service_status_repo, "update_service", fake_update_service)

    asyncio.run(service_status_svc.run_ai_lookup_for_service(1))

    assert len(captured_kwargs) == 1
    headers = captured_kwargs[0].get("headers", {})
    assert "User-Agent" in headers
    assert "Mozilla" in headers["User-Agent"]
    assert "Accept" in headers
