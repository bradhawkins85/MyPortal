import hashlib

import hashlib

import pytest

from app.services import modules as modules_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_update_module_hashes_chatgpt_secret(monkeypatch):
    stored = {
        "slug": "chatgpt-mcp",
        "enabled": False,
        "settings": {"shared_secret_hash": "", "allowed_actions": []},
    }
    captured: dict[str, dict] = {}

    async def fake_get_module(slug: str):
        assert slug == "chatgpt-mcp"
        return stored

    async def fake_update_module(slug: str, *, enabled=None, settings=None):
        captured["settings"] = settings
        if settings:
            stored["settings"].update(settings)
        stored["enabled"] = enabled
        return {"slug": slug, "enabled": enabled, "settings": dict(stored["settings"])}

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service.module_repo, "update_module", fake_update_module)

    result = await modules_service.update_module(
        "chatgpt-mcp",
        enabled=True,
        settings={"shared_secret": "super-secret", "allowed_actions": ["listTickets"]},
    )

    expected_hash = hashlib.sha256("super-secret".encode("utf-8")).hexdigest()
    assert captured["settings"]["shared_secret_hash"] == expected_hash
    assert result["settings"]["shared_secret_hash"] == "********"
    assert "shared_secret" not in captured["settings"]


@pytest.mark.anyio("asyncio")
async def test_list_modules_redacts_secret(monkeypatch):
    async def fake_list_modules():
        return [
            {
                "slug": "chatgpt-mcp",
                "enabled": True,
                "settings": {"shared_secret_hash": "abc123", "allowed_actions": ["listTickets"]},
            }
        ]

    monkeypatch.setattr(modules_service.module_repo, "list_modules", fake_list_modules)

    modules = await modules_service.list_modules()

    assert modules[0]["settings"]["shared_secret_hash"] == "********"


@pytest.mark.anyio("asyncio")
async def test_update_module_hashes_uptimekuma_secret(monkeypatch):
    stored = {
        "slug": "uptimekuma",
        "enabled": False,
        "settings": {"shared_secret_hash": ""},
    }
    captured: dict[str, dict] = {}

    async def fake_get_module(slug: str):
        assert slug == "uptimekuma"
        return stored

    async def fake_update_module(slug: str, *, enabled=None, settings=None):
        captured["settings"] = settings
        if settings:
            stored["settings"].update(settings)
        stored["enabled"] = enabled
        return {"slug": slug, "enabled": enabled, "settings": dict(stored["settings"])}

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service.module_repo, "update_module", fake_update_module)

    result = await modules_service.update_module(
        "uptimekuma",
        enabled=True,
        settings={"shared_secret": "monitor-token"},
    )

    expected_hash = hashlib.sha256("monitor-token".encode("utf-8")).hexdigest()
    assert captured["settings"]["shared_secret_hash"] == expected_hash
    assert result["settings"]["shared_secret_hash"] == "********"
