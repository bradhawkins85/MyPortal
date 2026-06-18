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

    async def fake_has_technician_message(room_id):
        return False

    monkeypatch.setattr(assistant.chat_repo, "has_technician_message", fake_has_technician_message)

    async def fake_send(room, body, **kwargs):
        sent.append(body)
        return True

    async def fake_reserve(room_id, expected_count, when=None):
        return True

    async def fake_release(room_id, reserved_count):
        raise AssertionError("successful acknowledgement should not release reservation")

    async def fake_active(room_id):
        queued.append(room_id)
        return None

    monkeypatch.setattr(assistant, "_send_bot_message", fake_send)
    monkeypatch.setattr(assistant.chat_repo, "reserve_ai_bot_response", fake_reserve)
    monkeypatch.setattr(assistant.chat_repo, "release_ai_bot_response_reservation", fake_release)
    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(assistant, "_audit", fake_audit)
    monkeypatch.setattr(assistant.chat_repo, "get_active_ai_queue_item", fake_active)

    asyncio.run(assistant.scan_waiting_rooms_once())

    assert sent == [assistant._ACK_MESSAGE]
    assert queued == []


def test_scan_waiting_rooms_skips_ack_when_response_slot_already_reserved(monkeypatch):
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

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)

    async def fake_list(due_before):
        return [{"id": 42, "status": "open", "assigned_tech_user_id": None, "ai_bot_response_count": 0}]

    async def fake_has_technician_message(room_id):
        return False

    async def fake_reserve(room_id, expected_count, when=None):
        return False

    async def fake_send(room, body, **kwargs):
        sent.append(body)
        return True

    monkeypatch.setattr(assistant.chat_repo, "list_ai_waiting_candidate_rooms", fake_list)
    monkeypatch.setattr(assistant.chat_repo, "has_technician_message", fake_has_technician_message)
    monkeypatch.setattr(assistant.chat_repo, "reserve_ai_bot_response", fake_reserve)
    monkeypatch.setattr(assistant, "_send_bot_message", fake_send)

    asyncio.run(assistant.scan_waiting_rooms_once())

    assert sent == []


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

    async def fake_has_technician_message(room_id):
        return False

    monkeypatch.setattr(assistant.chat_repo, "has_technician_message", fake_has_technician_message)

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


def test_ollama_generate_records_webhook_monitor_success(monkeypatch):
    settings = SimpleNamespace(
        matrixbot_ai_ollama_url="http://ollama.test",
        matrixbot_ai_ollama_model="llama3",
    )
    events: list[dict] = []
    successes: list[dict] = []

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)

    async def fake_create_manual_event(**kwargs):
        events.append(kwargs)
        return {"id": 123}

    async def fake_record_manual_success(event_id, **kwargs):
        successes.append({"event_id": event_id, **kwargs})
        return {"id": event_id, "status": "succeeded"}

    monkeypatch.setattr(assistant.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(assistant.webhook_monitor, "record_manual_success", fake_record_manual_success)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "ok"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr(assistant.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(assistant._ollama_generate("secret transcript", json_format=True))

    assert result == "ok"
    assert events[0]["name"] == "MATRIXBOT_AI Ollama generate"
    assert events[0]["target_url"] == "http://ollama.test/api/generate"
    assert events[0]["payload"]["prompt_length"] == len("secret transcript")
    assert "prompt_preview" not in events[0]["payload"]
    assert successes[0]["event_id"] == 123
    assert successes[0]["response_status"] == 200


def test_build_article_recommendation_messages_formats_four_plain_text_and_html_messages():
    article = {"title": "TouchPad <setup>"}
    link = "https://portal.example.test/knowledge-base/articles/touchpad?x=1&y=2"
    summary = " Enable the touchpad.\nUse Fn + F10. "

    messages = assistant._build_article_recommendation_messages(article, link, summary)

    assert messages == [
        (
            "While you wait, this article may help: TouchPad <setup>",
            "<p>While you wait, this article may help: TouchPad &lt;setup&gt;</p>",
        ),
        (
            "https://portal.example.test/knowledge-base/articles/touchpad?x=1&y=2",
            (
                '<p><a href="https://portal.example.test/knowledge-base/articles/touchpad?x=1&amp;y=2">'
                'https://portal.example.test/knowledge-base/articles/touchpad?x=1&amp;y=2</a></p>'
            ),
        ),
        ("Enable the touchpad. Use Fn + F10.", "<p>Enable the touchpad. Use Fn + F10.</p>"),
        (
            assistant._AI_DISCLAIMER,
            f"<p>{assistant._AI_DISCLAIMER}</p>",
        ),
    ]


def test_send_bot_message_forwards_formatted_body(monkeypatch):
    settings = SimpleNamespace(matrix_bot_user_id="@bot:example.test")
    calls: list[dict] = []

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)

    async def fake_create_manual_event(**kwargs):
        return {"id": 789}

    async def fake_record_manual_success(event_id, **kwargs):
        return {"id": event_id, "status": "succeeded"}

    async def fake_send_message(room_id, body, **kwargs):
        calls.append({"room_id": room_id, "body": body, **kwargs})
        return {"event_id": "$event"}

    async def fake_add_message(**kwargs):
        return {"id": 10, **kwargs}

    async def fake_increment(*args, **kwargs):
        return None

    async def fake_broadcast_refresh(**kwargs):
        return None

    monkeypatch.setattr(assistant.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(assistant.webhook_monitor, "record_manual_success", fake_record_manual_success)
    monkeypatch.setattr(assistant.matrix_service, "send_message", fake_send_message)
    monkeypatch.setattr(assistant.chat_repo, "add_message", fake_add_message)
    monkeypatch.setattr(assistant.chat_repo, "increment_ai_bot_response", fake_increment)
    monkeypatch.setattr(assistant.refresh_notifier, "broadcast_refresh", fake_broadcast_refresh)

    sent = asyncio.run(
        assistant._send_bot_message(
            {"id": 42, "matrix_room_id": "!room:id"},
            "plain",
            formatted_body='<p><a href="https://example.test">https://example.test</a></p>',
        )
    )

    assert sent is True
    assert calls == [
        {
            "room_id": "!room:id",
            "body": "plain",
            "formatted_body": '<p><a href="https://example.test">https://example.test</a></p>',
        }
    ]


def test_send_bot_message_records_webhook_monitor_failure(monkeypatch):
    settings = SimpleNamespace(matrix_bot_user_id="@bot:example.test")
    events: list[dict] = []
    failures: list[dict] = []

    monkeypatch.setattr(assistant, "get_settings", lambda: settings)

    async def fake_create_manual_event(**kwargs):
        events.append(kwargs)
        return {"id": 456}

    async def fake_record_manual_failure(event_id, **kwargs):
        failures.append({"event_id": event_id, **kwargs})
        return {"id": event_id, "status": "failed"}

    async def fake_send_message(room_id, body):
        raise RuntimeError("matrix unavailable")

    monkeypatch.setattr(assistant.webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(assistant.webhook_monitor, "record_manual_failure", fake_record_manual_failure)
    monkeypatch.setattr(assistant.matrix_service, "send_message", fake_send_message)

    sent = asyncio.run(assistant._send_bot_message({"id": 42, "matrix_room_id": "!room:id"}, "hello"))

    assert sent is False
    assert events[0]["name"] == "MATRIXBOT_AI Matrix message"
    assert events[0]["target_url"] == "matrix://!room:id"
    assert events[0]["payload"]["message_preview"] == "hello"
    assert failures[0]["event_id"] == 456
    assert failures[0]["error_message"] == "matrix unavailable"


def test_handle_chat_opened_marks_auto_assigned_room_without_technician_message(monkeypatch):
    room = {"id": 42, "status": "open", "assigned_tech_user_id": 7}
    marked: list[int] = []
    cancelled: list[tuple[int, str]] = []
    audited: list[str] = []

    async def fake_get_room(room_id):
        return room

    async def fake_mark(room_id, when=None):
        marked.append(room_id)

    async def fake_cancel(room_id, reason):
        cancelled.append((room_id, reason))

    async def fake_has_technician_message(room_id):
        return False

    async def fake_audit(action, room_id, value=None):
        audited.append(action)

    monkeypatch.setattr(assistant.chat_repo, "get_room", fake_get_room)
    monkeypatch.setattr(assistant.chat_repo, "mark_user_activity", fake_mark)
    monkeypatch.setattr(assistant.chat_repo, "cancel_active_ai_queue_for_room", fake_cancel)
    monkeypatch.setattr(assistant.chat_repo, "has_technician_message", fake_has_technician_message)
    monkeypatch.setattr(assistant, "_audit", fake_audit)

    asyncio.run(assistant.handle_chat_opened(42))

    assert marked == [42]
    assert cancelled == [(42, "chat_opened_timer_reset")]
    assert audited == ["matrix_ai_waiting_assistant.chat_opened"]


def test_handle_chat_opened_ignores_room_with_technician_message(monkeypatch):
    room = {"id": 42, "status": "open", "assigned_tech_user_id": 7}
    marked: list[int] = []

    async def fake_get_room(room_id):
        return room

    async def fake_mark(room_id, when=None):
        marked.append(room_id)

    async def fake_has_technician_message(room_id):
        return True

    monkeypatch.setattr(assistant.chat_repo, "get_room", fake_get_room)
    monkeypatch.setattr(assistant.chat_repo, "mark_user_activity", fake_mark)
    monkeypatch.setattr(assistant.chat_repo, "has_technician_message", fake_has_technician_message)

    asyncio.run(assistant.handle_chat_opened(42))

    assert marked == []


def test_article_visible_to_room_respects_company_restrictions():
    room = {"company_id": 10, "created_by_user_id": 20}

    assert assistant._article_visible_to_room(
        {"permission_scope": "company", "company_ids": [10]},
        room,
    ) is True
    assert assistant._article_visible_to_room(
        {"permission_scope": "company", "company_ids": [99]},
        room,
    ) is False
    assert assistant._article_visible_to_room(
        {"permission_scope": "company_admin", "company_admin_ids": [10]},
        room,
    ) is False
    assert assistant._article_visible_to_room(
        {"permission_scope": "user", "allowed_user_ids": [20]},
        room,
    ) is True
    assert assistant._article_visible_to_room(
        {"permission_scope": "user", "allowed_user_ids": [21]},
        room,
    ) is False
