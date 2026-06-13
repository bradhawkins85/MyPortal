from __future__ import annotations

from types import SimpleNamespace

import asyncio

from app.services import matrix_ai_waiting_assistant as assistant


def test_extract_keywords_uses_ollama_json(monkeypatch):
    async def fake_generate(prompt: str, *, json_format: bool = False) -> str:
        assert json_format is True
        assert "Chat transcript" in prompt
        return '{"keywords":["Windows 11", "VPN", "vpn", "", "Error 809"]}'

    monkeypatch.setattr(assistant, "_ollama_generate", fake_generate)

    keywords = asyncio.run(assistant._extract_keywords("User: VPN fails with Error 809 on Windows 11"))

    assert keywords == ["windows 11", "vpn", "error 809"]


def test_scan_waiting_rooms_sends_ack_before_analysis(monkeypatch):
    settings = SimpleNamespace(
        matrix_enabled=True,
        matrixbot_ai_waiting_assistant_enabled=True,
        matrixbot_ai_ollama_enabled=True,
        matrixbot_ai_ollama_url="http://ollama.test",
        matrixbot_ai_ollama_model="llama3",
        matrixbot_ai_max_responses=2,
        matrixbot_ai_response_delay_minutes=5,
    )
    sent: list[str] = []
    queued: list[int] = []

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    async def fake_list(due_before):
        return [{"id": 42, "status": "open", "assigned_tech_user_id": None, "ai_bot_response_count": 0}]

    monkeypatch.setattr(assistant.chat_repo, "list_ai_waiting_candidate_rooms", fake_list)

    async def fake_send(room, body):
        sent.append(body)
        return True

    async def fake_active(room_id):
        queued.append(room_id)
        return None

    monkeypatch.setattr(assistant, "_send_bot_message", fake_send)
    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(assistant, "_audit", fake_audit)
    monkeypatch.setattr(assistant.chat_repo, "get_active_ai_queue_item", fake_active)

    asyncio.run(assistant.scan_waiting_rooms_once())

    assert sent == [assistant._ACK_MESSAGE]
    assert queued == []


def test_scan_waiting_rooms_queues_second_response_analysis(monkeypatch):
    settings = SimpleNamespace(
        matrix_enabled=True,
        matrixbot_ai_waiting_assistant_enabled=True,
        matrixbot_ai_ollama_enabled=True,
        matrixbot_ai_ollama_url="http://ollama.test",
        matrixbot_ai_ollama_model="llama3",
        matrixbot_ai_max_responses=2,
        matrixbot_ai_response_delay_minutes=5,
        matrixbot_ai_queue_timeout_minutes=60,
    )
    created: list[dict] = []

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    async def fake_list(due_before):
        return [{"id": 42, "status": "open", "assigned_tech_user_id": None, "ai_bot_response_count": 1}]

    async def fake_active(room_id):
        return None

    monkeypatch.setattr(assistant.chat_repo, "list_ai_waiting_candidate_rooms", fake_list)
    monkeypatch.setattr(assistant.chat_repo, "get_active_ai_queue_item", fake_active)

    async def fake_create(**kwargs):
        created.append(kwargs)
        return {"id": 7}

    monkeypatch.setattr(assistant.chat_repo, "create_ai_queue_item", fake_create)
    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(assistant, "_audit", fake_audit)

    asyncio.run(assistant.scan_waiting_rooms_once())

    assert created[0]["chat_room_id"] == 42
    assert created[0]["created_for_response_number"] == 2


def test_ollama_generate_records_webhook_monitor_event(monkeypatch):
    settings = SimpleNamespace(
        matrixbot_ai_ollama_url="http://ollama.test",
        matrixbot_ai_ollama_model="assistant-model",
        matrixbot_ai_queue_retry_minutes=5,
    )
    calls: dict[str, object] = {}

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)

    async def fake_create_manual_event(**kwargs):
        calls["create"] = kwargs
        return {"id": 123}

    async def fake_record_manual_success(event_id, **kwargs):
        calls["success"] = {"event_id": event_id, **kwargs}
        return {"id": event_id, "status": "succeeded"}

    monkeypatch.setattr(assistant.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(assistant.webhook_monitor, "record_manual_success", fake_record_manual_success)

    class FakeResponse:
        status_code = 200
        text = '{"response":"done"}'
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "done"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            calls["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, *, json, headers):
            calls["post"] = {"endpoint": endpoint, "json": json, "headers": headers}
            return FakeResponse()

    monkeypatch.setattr(assistant.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(assistant._ollama_generate("Summarise this", json_format=True))

    assert result == "done"
    assert calls["create"]["name"] == "matrixbot.ai_waiting_assistant.ollama.generate"
    assert calls["create"]["target_url"] == "http://ollama.test/api/generate"
    assert calls["post"]["json"]["model"] == "assistant-model"
    assert calls["post"]["json"]["format"] == "json"
    assert calls["success"]["event_id"] == 123
    assert calls["success"]["response_status"] == 200
