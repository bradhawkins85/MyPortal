import asyncio

from app.services import modules
from app.services import tacticalrmm


def test_suggest_assets_prefers_upn_username(monkeypatch):
    async def fake_fetch_one(sql, params):
        if "FROM tickets" in sql:
            return {
                "company_id": 7,
                "tacticalrmm_client_id": "client-7",
                "email": "alex.smith@example.com",
                "first_name": "Alex",
                "last_name": "Smith",
            }
        assert params == (7, "agent-1")
        return {"id": 42}

    async def fake_fetch_all(sql, params):
        assert "FROM assets" in sql
        assert params == (7,)
        return []

    async def fake_fetch_agents(client_id):
        assert client_id == "client-7"
        return [{"agent_id": "agent-1", "logged_username": "DOMAIN\\alex.smith"}]

    saved = []

    async def fake_replace(ticket_id, suggestions):
        saved.append((ticket_id, suggestions))

    monkeypatch.setattr(modules.db, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(modules.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(tacticalrmm, "fetch_agents", fake_fetch_agents)
    monkeypatch.setattr(
        modules.tickets_repo, "replace_ticket_suggested_assets", fake_replace
    )

    result = asyncio.run(modules._invoke_suggest_assets({}, {"ticket_id": 9}))

    assert result == {"status": "ok", "ticket_id": 9, "suggested": 1}
    assert saved == [(9, [(42, "alex.smith")])]


def test_suggest_assets_uses_imported_asset_without_tactical_mapping(monkeypatch):
    async def fake_fetch_one(sql, params):
        assert "FROM tickets" in sql
        return {
            "company_id": 7,
            "tacticalrmm_client_id": None,
            "email": "alex.smith@example.com",
            "first_name": "Alex",
            "last_name": "Smith",
        }

    async def fake_fetch_all(sql, params):
        assert "last_user" in sql
        assert params == (7,)
        return [
            {"id": 41, "last_user": "DOMAIN\\alex.smith"},
            {"id": 42, "last_user": "someone.else@example.com"},
        ]

    saved = []

    async def fake_replace(ticket_id, suggestions):
        saved.append((ticket_id, suggestions))

    monkeypatch.setattr(modules.db, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(modules.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(
        modules.tickets_repo, "replace_ticket_suggested_assets", fake_replace
    )

    result = asyncio.run(modules._invoke_suggest_assets({}, {"ticket_id": 9}))

    assert result == {"status": "ok", "ticket_id": 9, "suggested": 1}
    assert saved == [(9, [(41, "alex.smith")])]


def test_logged_username_normalisation_handles_domain_and_upn():
    assert modules._normalise_logged_username("DOMAIN\\Alex.Smith") == "alex.smith"
    assert modules._normalise_logged_username("Alex@Example.com") == "alex"


def test_suggest_assets_is_an_automation_action(monkeypatch):
    async def fake_list_modules():
        return []

    monkeypatch.setattr(modules.module_repo, "list_modules", fake_list_modules)
    actions = asyncio.run(modules.list_trigger_action_modules())
    assert any(action["slug"] == "suggest-assets" for action in actions)
