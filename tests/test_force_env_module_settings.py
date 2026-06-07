import pytest

from app.services import modules as modules_service


@pytest.mark.asyncio
async def test_force_env_module_settings_ignores_db_values_for_env_backed_fields(monkeypatch):
    monkeypatch.setenv("FORCE_ENV_MODULE_SETTINGS", "true")
    monkeypatch.setenv("HUDU_BASE_URL", "https://env.example")
    monkeypatch.setenv("HUDU_API_KEY", "env-key")

    async def fake_get_module(slug: str):
        assert slug == "hudu"
        return {
            "slug": "hudu",
            "enabled": True,
            "settings": {
                "base_url": "https://db.example",
                "api_key": "db-key",
            },
        }

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)

    result = await modules_service.get_module_settings("hudu")

    assert result == {
        "base_url": "https://env.example",
        "api_key": "env-key",
    }


@pytest.mark.asyncio
async def test_force_env_module_settings_preserves_non_env_fields(monkeypatch):
    monkeypatch.setenv("FORCE_ENV_MODULE_SETTINGS", "true")
    monkeypatch.setenv("SYNCRO_BASE_URL", "https://env.syncro.example")
    monkeypatch.setenv("SYNCRO_API_KEY", "env-syncro-key")
    monkeypatch.setenv("SYNCRO_RATE_LIMIT_PER_MINUTE", "90")

    async def fake_get_module(slug: str):
        assert slug == "syncro"
        return {
            "slug": "syncro",
            "enabled": True,
            "settings": {
                "base_url": "https://db.syncro.example",
                "api_key": "db-syncro-key",
                "rate_limit_per_minute": 180,
                "custom_runtime_state": "preserve-me",
            },
        }

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)

    result = await modules_service.get_module("syncro", redact=False)

    assert result["settings"]["base_url"] == "https://env.syncro.example"
    assert result["settings"]["api_key"] == "env-syncro-key"
    assert result["settings"]["rate_limit_per_minute"] == 90
    assert result["settings"]["custom_runtime_state"] == "preserve-me"


@pytest.mark.asyncio
async def test_force_env_module_settings_uses_defaults_when_env_is_blank(monkeypatch):
    monkeypatch.setenv("FORCE_ENV_MODULE_SETTINGS", "true")
    monkeypatch.delenv("CALL_RECORDINGS_PATH", raising=False)
    monkeypatch.delenv("CALL_RECORDINGS_PHONE_SYSTEM", raising=False)

    async def fake_get_module(slug: str):
        assert slug == "call-recordings"
        return {
            "slug": "call-recordings",
            "enabled": True,
            "settings": {
                "recordings_path": "/srv/db-recordings",
                "phone_system_type": "grandstream-ucm",
            },
        }

    monkeypatch.setattr(modules_service.module_repo, "get_module", fake_get_module)

    result = await modules_service.get_module_settings("call-recordings")

    assert result["recordings_path"] == "/var/lib/myportal/call_recordings"
    assert result["phone_system_type"] == "generic"
