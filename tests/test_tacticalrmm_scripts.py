import asyncio

from app.services import tacticalrmm


def test_fetch_scripts_normalises_script_library(monkeypatch):
    async def fake_load_settings():
        return {
            "base_url": "https://trmm.example",
            "api_key": "secret",
            "verify_ssl": True,
        }

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        assert endpoint == "scripts/"
        assert method == "GET"
        assert body is None
        return {
            "results": [
                {
                    "id": 2,
                    "name": "Beta",
                    "description": "Second",
                    "shell": "powershell",
                },
                {"id": "1", "filename": "Alpha.ps1", "category": "Ops"},
                {"name": "Missing ID"},
            ]
        }

    monkeypatch.setattr(tacticalrmm, "_load_settings", fake_load_settings)
    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    scripts = asyncio.run(tacticalrmm.fetch_scripts())

    assert [script["id"] for script in scripts] == [1, 2]
    assert scripts[0]["name"] == "Alpha.ps1"
    assert scripts[1]["script_type"] == "powershell"


def test_run_script_on_agent_posts_default_body(monkeypatch):
    calls = []

    async def fake_call_endpoint(endpoint, *, method="GET", body=None):
        calls.append((endpoint, method, body))
        if endpoint == "scripts/89/":
            return {
                "id": 89,
                "name": "Collect Logs",
                "timeout": 120,
                "run_as_user": True,
            }
        return {"event_id": 123, "status": "completed"}

    monkeypatch.setattr(tacticalrmm, "_call_endpoint", fake_call_endpoint)

    result = asyncio.run(tacticalrmm.run_script_on_agent("agent-abc", 89))

    assert result["response"]["event_id"] == 123
    endpoint, method, body = calls[-1]
    assert endpoint == "agents/agent-abc/runscript/"
    assert method == "POST"
    assert body["script"] == 89
    assert body["output"] == "forget"
    assert body["emailMode"] == "default"
    assert body["args"] == []
    assert body["env_vars"] == []
    assert body["timeout"] == 120
    assert body["run_as_user"] is True
