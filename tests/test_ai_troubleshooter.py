"""Tests for the multi-stage AI troubleshooter (app.services.ai_troubleshooter)."""
from __future__ import annotations

import asyncio
import gzip
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.routes import tickets as tickets_routes
from app.services import ai_troubleshooter as ts
from app.services import tray as tray_service


TICKET = {"id": 77, "company_id": 3, "subject": "Wi-Fi keeps dropping", "ticket_number": "T-77"}
ASSET = {"asset_id": 1, "name": "LAPTOP-01", "os_name": "Windows 11 Pro"}
DEVICE = {
    "id": 5,
    "device_uid": "dev-uid",
    "hostname": "laptop-01",
    "company_id": 3,
    "status": "active",
    "os": "windows",
}
LLM = {"base_url": "http://llm", "model": "test-model", "api_key": ""}


class _Recorder:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.dispatched: list[dict] = []
        self.chats: list[list[dict]] = []
        self.chat_responses: list[str] = []


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()

    async def fake_note(ticket_id, body_html):
        r.notes.append(body_html)
        return {"id": len(r.notes)}

    async def fake_chat(llm, messages, *, json_mode=False):
        r.chats.append(messages)
        return r.chat_responses.pop(0)

    async def fake_dispatch(**kwargs):
        r.dispatched.append(kwargs)
        return 42, True

    async def fake_search(query, context, *, limit=8, use_ollama=True, include_access_metadata=False):
        assert use_ollama is False
        assert context.is_super_admin is False
        assert set(context.memberships) == {3}
        return {
            "results": [
                {
                    "id": 9,
                    "slug": "wifi-drops",
                    "title": "Wi-Fi drops on laptops",
                    "summary": "Update the WLAN driver",
                    "content": "<p>Update the <b>WLAN</b> driver.</p>",
                }
            ]
        }

    async def allowed(ticket_id):
        return True

    monkeypatch.setattr(tray_service, "add_troubleshoot_note", fake_note)
    monkeypatch.setattr(tray_service, "dispatch_troubleshoot_command", fake_dispatch)
    monkeypatch.setattr(ts, "_chat", fake_chat)
    monkeypatch.setattr(ts.knowledge_base_service, "search_articles", fake_search)
    monkeypatch.setattr(ts.ai_consent, "is_ai_allowed_for_ticket_id", allowed)
    return r


def _research_response():
    return json.dumps(
        {
            "articles": [
                {
                    "title": "Fix Wi-Fi connection issues in Windows",
                    "publisher": "Microsoft Support",
                    "url": "https://support.microsoft.com/wifi",
                    "summary": "Driver and power settings",
                    "steps": ["Update the driver", "Disable power saving"],
                },
                {"title": "Bad link", "url": "javascript:alert(1)"},
            ],
            "search_terms": ["wifi drops"],
        }
    )


def _plan_response(log_requests):
    return "```json\n" + json.dumps(
        {
            "summary": "Driver or power management",
            "steps": [
                {"action": "Update the WLAN driver", "detail": "From the vendor", "article": "Fix Wi-Fi"},
                {"action": "Review the WLAN event log", "detail": "", "article": ""},
            ],
            "log_requests": log_requests,
        }
    ) + "\n```"


def _run(**overrides):
    params = {
        "ticket": TICKET,
        "asset": ASSET,
        "device": DEVICE,
        "problem": "Ticket T-77 | Subject: Wi-Fi keeps dropping",
        "llm": LLM,
        "initiated_by_user_id": 1,
    }
    params.update(overrides)
    return asyncio.run(ts.run_research_and_plan(**params))


def test_stages_research_plan_and_request_logs(rec):
    rec.chat_responses = [
        _research_response(),
        _plan_response(
            [
                {"source": "Microsoft-Windows-WLAN-AutoConfig/Operational", "reason": "Look for disconnect reasons", "hours": 500},
                {"source": "C:\\Windows\\secrets.txt", "reason": "not allowed"},
                {"source": "macos:wifi", "reason": "wrong platform"},
            ]
        ),
    ]
    plan = _run()

    assert len(rec.notes) == 3
    research, steps, collect = rec.notes
    assert "Stage 1 of 4" in research
    assert '/knowledge-base/articles/wifi-drops' in research
    assert 'href="https://support.microsoft.com/wifi"' in research
    assert "javascript:" not in research
    assert "Stage 2 of 4" in steps
    assert "Update the WLAN driver" in steps
    assert "Look for disconnect reasons" in steps
    assert "Stage 3 of 4" in collect and "command #42" in collect

    # Only allowlisted sources for the endpoint's platform reach the device.
    assert plan["log_requests"] == [
        {
            "source": "Microsoft-Windows-WLAN-AutoConfig/Operational",
            "reason": "Look for disconnect reasons",
            "hours": ts.MAX_LOG_HOURS,
        }
    ]
    (dispatch,) = rec.dispatched
    payload = dispatch["payload"]
    assert payload["mode"] == ts.MODE_COLLECT_LOGS
    assert payload["log_requests"] == plan["log_requests"]
    # No LLM credentials go to the endpoint in the multi-stage flow.
    assert not any(key.startswith("llm_") for key in payload)
    context = payload[tray_service.SERVER_CONTEXT_KEY]
    assert context["model"] == "test-model"
    assert context["steps"] == ["Update the WLAN driver", "Review the WLAN event log"]

    # The plan prompt offers the model only the Windows allowlist.
    plan_system = rec.chats[1][0]["content"]
    assert "Microsoft-Windows-WLAN-AutoConfig/Operational" in plan_system
    assert "macos:wifi" not in plan_system
    # The KB article content is passed to the plan stage as plain text.
    assert "Update the WLAN driver." in rec.chats[1][1]["content"]


def test_no_log_requests_finishes_after_steps(rec):
    rec.chat_responses = [_research_response(), _plan_response([])]
    plan = _run()
    assert plan["log_requests"] == []
    assert rec.dispatched == []
    assert len(rec.notes) == 2
    assert "troubleshooter is finished" in rec.notes[1]


def test_inactive_device_lists_logs_without_dispatching(rec):
    rec.chat_responses = [
        _research_response(),
        _plan_response([{"source": "System", "reason": "Kernel-Power events", "hours": 6}]),
    ]
    _run(device={**DEVICE, "status": "disabled"})
    assert rec.dispatched == []
    assert "no active tray agent" in rec.notes[1]


def test_unparseable_plan_keeps_model_text(rec):
    rec.chat_responses = ["not json", "Restart the router <script>x</script>"]
    plan = _run()
    assert plan["steps"][0]["action"].startswith("Restart the router")
    assert "<script>" not in rec.notes[1]
    assert rec.dispatched == []


def test_research_failure_is_reported(rec, monkeypatch):
    async def boom(llm, messages, *, json_mode=False):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ts, "_chat", boom)
    assert _run() is None
    assert "Stage 1 of 4 failed" in rec.notes[0]
    assert "connection refused" in rec.notes[0]


def test_consent_withdrawn_stops_before_plan(rec, monkeypatch):
    async def denied(ticket_id):
        return False

    monkeypatch.setattr(ts.ai_consent, "is_ai_allowed_for_ticket_id", denied)
    rec.chat_responses = [_research_response()]
    assert _run() is None
    assert len(rec.chats) == 1
    assert "opted out" in rec.notes[-1]


def test_endpoint_platform():
    assert ts.endpoint_platform({"os": "windows"}, None) == "windows"
    assert ts.endpoint_platform({"os": "darwin"}, None) == "macos"
    assert ts.endpoint_platform(None, {"os_name": "macOS Sonoma"}) == "macos"
    assert ts.endpoint_platform(None, {"os_name": "Ubuntu"}) == "unknown"
    assert set(ts.allowed_log_sources("unknown")) == set(ts.WINDOWS_LOG_SOURCES) | set(ts.MACOS_LOG_SOURCES)


def test_log_source_allowlists_match_tray_agent():
    go_source = (
        Path(__file__).resolve().parents[1] / "tray" / "internal" / "agent" / "logsources.go"
    ).read_text(encoding="utf-8")
    windows_block = go_source.split("var WindowsLogSources = []string{", 1)[1].split("}", 1)[0]
    mac_block = go_source.split("var MacOSLogPredicates = map[string]string{", 1)[1].split("\n}", 1)[0]
    assert re.findall(r'"([^"]+)",', windows_block) == list(ts.WINDOWS_LOG_SOURCES)
    assert re.findall(r'^\s*"(macos:[^"]+)":', mac_block, re.MULTILINE) == list(ts.MACOS_LOG_SOURCES)


# ---------------------------------------------------------------------------
# Log bundle handling
# ---------------------------------------------------------------------------


BUNDLE_TEXT = (
    "### System Log\n2024-05-01 10:00:00 [Information] id=1: boot\n\n"
    "### Microsoft-Windows-WLAN-AutoConfig/Operational Log\n"
    "2024-05-01 10:05:00 [Error] id=8002: WLAN AutoConfig service failed to connect\n\n"
)


def test_decompress_tolerates_truncated_bundle():
    data = gzip.compress(BUNDLE_TEXT.encode() * 50)
    assert ts.decompress_log_bundle(data) == BUNDLE_TEXT * 50
    partial = ts.decompress_log_bundle(data[: len(data) // 2])
    assert partial and (BUNDLE_TEXT * 50).startswith(partial)
    assert ts.decompress_log_bundle(b"not gzip") == ""


def test_decompress_is_bounded():
    data = gzip.compress(b"A" * (ts._MAX_DECOMPRESSED_LOG_BYTES * 4))
    assert len(ts.decompress_log_bundle(data)) == ts._MAX_DECOMPRESSED_LOG_BYTES


def test_split_sections_and_sample_prefers_errors():
    sections = ts.split_log_sections(BUNDLE_TEXT)
    assert list(sections) == ["System", "Microsoft-Windows-WLAN-AutoConfig/Operational"]
    text = "\n".join(["info line %d" % i for i in range(200)] + ["[Error] disk failure"])
    sample, truncated = ts._sample_log(text, 200)
    assert truncated
    assert "[Error] disk failure" in sample
    assert len(sample) <= 200


def _collect_command(status="delivered"):
    return {
        "id": 11,
        "command": "troubleshoot",
        "device_id": 5,
        "status": status,
        "payload_json": json.dumps(
            {
                "ticket_id": 77,
                "mode": ts.MODE_COLLECT_LOGS,
                "log_requests": [
                    {
                        "source": "Microsoft-Windows-WLAN-AutoConfig/Operational",
                        "reason": "Look for disconnect reasons",
                        "hours": 24,
                    }
                ],
                tray_service.SERVER_CONTEXT_KEY: {
                    "problem": "Wi-Fi keeps dropping",
                    "model": "test-model",
                    "summary": "Driver issue",
                    "steps": ["Update the WLAN driver"],
                },
            }
        ),
    }


def test_analysis_sends_logs_with_reasons(rec):
    rec.chat_responses = [
        json.dumps(
            {
                "summary": "The adapter fails to reconnect.",
                "findings": [
                    {
                        "finding": "WLAN AutoConfig failures",
                        "evidence": "2024-05-01 10:05:00 [Error] id=8002",
                        "source": "Microsoft-Windows-WLAN-AutoConfig/Operational",
                    }
                ],
                "likely_causes": ["Outdated driver"],
                "solutions": ["Update the WLAN driver"],
                "next_steps": ["Re-test after the update"],
            }
        )
    ]
    asyncio.run(
        ts.analyse_collected_logs(
            ticket_id=77,
            command=_collect_command(),
            log_text=BUNDLE_TEXT,
            endpoint_label="laptop-01",
            llm=LLM,
        )
    )
    user_prompt = rec.chats[0][1]["content"]
    assert "Look for disconnect reasons" in user_prompt
    assert "failed to connect" in user_prompt
    assert "Update the WLAN driver" in user_prompt
    # An unrequested section is still analysed, labelled as a default source.
    assert "default log set" in user_prompt
    (note,) = rec.notes
    assert "Stage 4 of 4" in note
    assert "Possible solutions" in note and "Outdated driver" in note


def test_analysis_skips_empty_logs(rec):
    asyncio.run(
        ts.analyse_collected_logs(
            ticket_id=77, command=_collect_command(), log_text="  ", endpoint_label="x", llm=LLM
        )
    )
    assert rec.chats == []
    assert "skipped" in rec.notes[0]


# ---------------------------------------------------------------------------
# Device delivery and the completion callback
# ---------------------------------------------------------------------------


def test_server_context_never_sent_to_device(monkeypatch):
    sent: list[dict] = []

    async def fake_log_command(**kwargs):
        return 42

    async def fake_send(device_uid, payload):
        sent.append(payload)
        return True

    async def noop(*args, **kwargs):
        return None

    async def fake_queued(device_id):
        return [_collect_command(status="queued")]

    async def allowed(ticket_id):
        return True

    monkeypatch.setattr(tray_service.tray_repo, "log_command", fake_log_command)
    monkeypatch.setattr(tray_service.tray_repo, "mark_command_delivered", noop)
    monkeypatch.setattr(tray_service.tray_repo, "get_queued_commands_for_device", fake_queued)
    monkeypatch.setattr(tray_service, "send_to_device", fake_send)
    monkeypatch.setattr(tray_service.ai_consent, "is_ai_allowed_for_ticket_id", allowed)

    payload = json.loads(_collect_command()["payload_json"])
    asyncio.run(
        tray_service.dispatch_troubleshoot_command(
            device_id=5, device_uid="dev-uid", payload=payload, initiated_by_user_id=1
        )
    )
    asyncio.run(tray_service.deliver_queued_commands(DEVICE))
    assert len(sent) == 2
    for message in sent:
        assert tray_service.SERVER_CONTEXT_KEY not in message
        assert message["mode"] == ts.MODE_COLLECT_LOGS
        assert message["log_requests"]


class _Upload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class _Request:
    base_url = "https://portal.example/"


@pytest.fixture
def completion(monkeypatch, rec):
    state = {"command": _collect_command(), "completed": [], "scheduled": [], "attached": []}

    async def fake_get_command(command_id):
        return dict(state["command"])

    async def fake_get_ticket(ticket_id):
        return {"id": ticket_id, "company_id": 3}

    async def fake_mark_completed(command_id, *, error=None):
        state["completed"].append((command_id, error))

    async def fake_save(ticket_id, **kwargs):
        state["attached"].append(kwargs)
        return {"id": 500}

    def fake_schedule(**kwargs):
        state["scheduled"].append(kwargs)
        return bool(ts.decompress_log_bundle(kwargs["log_bytes"]).strip())

    from app.repositories import tickets as tickets_repo
    from app.repositories import tray as tray_repo

    monkeypatch.setattr(tray_repo, "get_command", fake_get_command)
    monkeypatch.setattr(tray_repo, "mark_command_completed", fake_mark_completed)
    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_routes.attachments_service, "save_file_bytes", fake_save)
    monkeypatch.setattr(ts, "schedule_log_analysis", fake_schedule)
    return state


def _complete(**kwargs):
    params = {
        "ticket_id": 77,
        "request": _Request(),
        "command_id": 11,
        "guidance": "",
        "endpoint": "laptop-01",
        "error": "",
        "log_bundle": _Upload(gzip.compress(BUNDLE_TEXT.encode())),
        "device": DEVICE,
    }
    params.update(kwargs)
    return asyncio.run(tickets_routes.receive_troubleshoot_result(**params))


def test_collect_mode_completion_queues_analysis(rec, completion):
    response = _complete()
    assert response.status_code == 200
    (scheduled,) = completion["scheduled"]
    assert scheduled["ticket_id"] == 77
    assert scheduled["endpoint_label"] == "laptop-01"
    assert completion["attached"] and completion["attached"][0]["access_level"] == "closed"
    assert "logs received from laptop-01" in rec.notes[0]
    assert "analysis will be posted" in rec.notes[0]
    assert completion["completed"] == [(11, None)]


def test_collect_mode_completion_with_only_error(rec, completion):
    _complete(log_bundle=None, error="log collection: unsupported")
    assert completion["scheduled"] == []
    assert "nothing to analyse" in rec.notes[0]
    assert completion["completed"] == [(11, "log collection: unsupported")]


def test_collect_mode_completion_rejected_once_finished(rec, completion):
    completion["command"] = _collect_command(status="completed")
    with pytest.raises(HTTPException) as exc:
        _complete()
    assert exc.value.status_code == 409
    assert completion["scheduled"] == []


# ---------------------------------------------------------------------------
# Route: starting the troubleshooter from a ticket asset
# ---------------------------------------------------------------------------


def test_asset_route_starts_multi_stage_run(monkeypatch):
    started: list[dict] = []

    async def fake_get_ticket(ticket_id):
        return {**TICKET, "description": "<p>Drops every hour</p>"}

    async def fake_assets(ticket_id):
        return [{**ASSET, "tray_device_uid": "dev-uid"}]

    async def fake_device(uid):
        return dict(DEVICE)

    async def allowed(ticket):
        return True

    async def fake_start(**kwargs):
        started.append(kwargs)

    from app.repositories import tickets as tickets_repo
    from app.repositories import tray as tray_repo

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_ticket_assets", fake_assets)
    monkeypatch.setattr(tray_repo, "get_device_by_uid", fake_device)
    monkeypatch.setattr(tickets_routes.ai_consent, "is_ai_allowed_for_ticket", allowed)
    monkeypatch.setattr(ts, "start_asset_troubleshoot", fake_start)
    monkeypatch.setattr(
        tray_service, "build_troubleshoot_llm_config", lambda model=None: dict(LLM)
    )

    response = asyncio.run(
        tickets_routes.troubleshoot_ticket_asset(
            ticket_id=77, asset_id=1, payload=None, current_user={"id": 1}
        )
    )
    assert response.status == "started"
    assert response.command_id is None
    (call,) = started
    assert "Drops every hour" in call["problem"]
    assert call["device"]["device_uid"] == "dev-uid"


def test_troubleshooter_notes_are_the_ai_commenter_type():
    assert tray_service.is_troubleshoot_agent_reply(
        {"author_id": None, "author_email": tray_service.TROUBLESHOOT_AGENT_EMAIL}
    )
    # A real user with the same address is not the AI agent.
    assert not tray_service.is_troubleshoot_agent_reply(
        {"author_id": 3, "author_email": tray_service.TROUBLESHOOT_AGENT_EMAIL}
    )
    assert not tray_service.is_troubleshoot_agent_reply(
        {"author_id": None, "author_email": "someone@example.com"}
    )


def test_ticket_template_renders_ai_commenter_type():
    template = (
        Path(__file__).resolve().parents[1] / "app" / "templates" / "admin" / "ticket_detail.html"
    ).read_text(encoding="utf-8")
    assert "{% set reply_kind = 'ai' if reply.is_ai else" in template
    assert 'data-message-kind="{{ reply_kind }}"' in template
    assert 'data-history-filter="ai"' in template


# ---------------------------------------------------------------------------
# Optional public web search
# ---------------------------------------------------------------------------


def test_web_search_disabled_by_default(rec, monkeypatch):
    called = []

    async def fake_research(terms):
        called.append(terms)
        return []

    monkeypatch.setattr(ts.troubleshoot_web_search, "research", fake_research)
    monkeypatch.setattr(ts.troubleshoot_web_search, "is_enabled", lambda: False)
    rec.chat_responses = [_research_response(), _plan_response([])]
    _run()
    assert called == []
    assert "Public web" not in rec.notes[0]


def test_web_search_pages_feed_plan_and_kb_note(rec, monkeypatch):
    searched = []

    async def fake_research(terms):
        searched.append(list(terms))
        return [
            {
                "title": "Fix Wi-Fi drops",
                "url": "https://example.com/wifi",
                "snippet": "Reset the adapter",
                "content": "1. Reset the network adapter 2. Update the driver",
            },
            {"title": "Unrelated", "url": "https://example.com/other", "snippet": "", "content": "x"},
        ]

    monkeypatch.setattr(ts.troubleshoot_web_search, "is_enabled", lambda: True)
    monkeypatch.setattr(ts.troubleshoot_web_search, "research", fake_research)
    plan = json.loads(_plan_response([]).strip("`").removeprefix("json\n"))
    plan["web_sources"] = [
        {"id": "web-1", "steps": ["Reset the network adapter", "Update the driver"]},
        {"id": "web-9", "steps": ["unknown page"]},
    ]
    rec.chat_responses = [_research_response(), json.dumps(plan)]
    result = _run()

    # Only the generic search terms leave the server, never the ticket text.
    assert searched == [["wifi drops"]]
    assert 'href="https://example.com/wifi"' in rec.notes[0]
    plan_messages = rec.chats[1]
    assert "web_sources" in plan_messages[0]["content"]
    assert "Reset the network adapter 2." in plan_messages[1]["content"]
    assert result["web_sources"] == [
        {
            "title": "Fix Wi-Fi drops",
            "url": "https://example.com/wifi",
            "steps": ["Reset the network adapter", "Update the driver"],
        }
    ]
    kb_note = rec.notes[2]
    assert "knowledge base article" in kb_note
    assert "<code>https://example.com/wifi</code>" in kb_note
    assert "<ol><li>Reset the network adapter</li><li>Update the driver</li></ol>" in kb_note


def test_web_search_failure_is_noted_and_run_continues(rec, monkeypatch):
    async def boom(terms):
        raise ValueError("TROUBLESHOOT_WEB_SEARCH_URL is not set")

    monkeypatch.setattr(ts.troubleshoot_web_search, "is_enabled", lambda: True)
    monkeypatch.setattr(ts.troubleshoot_web_search, "research", boom)
    rec.chat_responses = [_research_response(), _plan_response([])]
    assert _run() is not None
    assert "Public web search failed" in rec.notes[0]
    assert "TROUBLESHOOT_WEB_SEARCH_URL" in rec.notes[0]


def test_html_to_text_keeps_steps_and_drops_scripts():
    from app.services import troubleshoot_web_search as web

    title, text = web.html_to_text(
        "<html><head><title>Fix &amp; repair</title><script>alert(1)</script></head>"
        "<body><nav>Menu</nav><h1>Steps</h1><ol><li>Restart</li><li>Update</li></ol></body></html>"
    )
    assert title == "Fix & repair"
    assert "alert" not in text and "Menu" not in text
    assert text.splitlines() == ["Steps", "Restart", "Update"]


def test_fetch_page_text_refuses_private_addresses():
    from app.services import troubleshoot_web_search as web

    assert asyncio.run(web.fetch_page_text("http://127.0.0.1/admin")) == ""
    assert asyncio.run(web.fetch_page_text("http://169.254.169.254/latest/meta-data")) == ""


def test_searxng_and_brave_results_are_parsed(monkeypatch):
    import httpx
    from types import SimpleNamespace

    from app.services import troubleshoot_web_search as web

    settings = SimpleNamespace(
        troubleshoot_web_search_url="https://search.example",
        troubleshoot_web_search_api_key="key",
    )
    monkeypatch.setattr(web, "get_settings", lambda: settings)

    async def ok(url, **kwargs):
        return url

    monkeypatch.setattr(web, "validate_outbound_url_async", ok)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.example":
            assert request.url.params["format"] == "json"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "Fix it", "url": "https://a.example/x", "content": "steps"},
                        {"title": "Bad", "url": "file:///etc/passwd"},
                    ]
                },
            )
        assert request.headers["X-Subscription-Token"] == "key"
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "Brave", "url": "https://b.example", "description": "d"}]}},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await web._search_searxng(client, "wifi"), await web._search_brave(client, "wifi")

    searx, brave = asyncio.run(run())
    assert searx == [{"title": "Fix it", "url": "https://a.example/x", "snippet": "steps"}]
    assert brave == [{"title": "Brave", "url": "https://b.example", "snippet": "d"}]
