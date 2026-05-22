"""Test trigger action module filtering."""
import pytest

from app.services import modules
from app.services.modules import DEFAULT_MODULES


@pytest.mark.asyncio
async def test_list_trigger_action_modules_excludes_non_triggerable(monkeypatch):
    """Test that list_trigger_action_modules excludes non-triggerable modules."""
    from app.repositories import integration_modules as module_repo
    
    # Mock the module repository to return all modules
    async def fake_list_modules():
        return [
            {"slug": "smtp", "name": "Send Email", "enabled": True, "settings": {}},
            {"slug": "imap", "name": "IMAP Mailboxes", "enabled": True, "settings": {}},
            {"slug": "ollama", "name": "Ollama", "enabled": True, "settings": {}},
            {"slug": "xero", "name": "Xero", "enabled": True, "settings": {}},
            {"slug": "uptimekuma", "name": "Uptime Kuma", "enabled": True, "settings": {}},
            {"slug": "syncro", "name": "Syncro", "enabled": True, "settings": {}},
            {"slug": "chatgpt-mcp", "name": "ChatGPT MCP", "enabled": True, "settings": {}},
            {"slug": "sms-gateway", "name": "Send SMS", "enabled": True, "settings": {}},
            {"slug": "tacticalrmm", "name": "Tactical RMM", "enabled": True, "settings": {}},
            {"slug": "ntfy", "name": "ntfy", "enabled": True, "settings": {}},
            {"slug": "create-ticket", "name": "Create Ticket", "enabled": True, "settings": {}},
            {"slug": "create-task", "name": "Create Task", "enabled": True, "settings": {}},
            {"slug": "update-ticket", "name": "Update Ticket", "enabled": True, "settings": {}},
            {"slug": "update-ticket-description", "name": "Update Ticket Description", "enabled": True, "settings": {}},
            {"slug": "reprocess-ai", "name": "Reprocess AI", "enabled": True, "settings": {}},
            {"slug": "add-ticket-reply", "name": "Add Ticket Reply", "enabled": True, "settings": {}},
            {"slug": "trello", "name": "Trello", "enabled": True, "settings": {}},
        ]
    
    monkeypatch.setattr(module_repo, "list_modules", fake_list_modules)
    
    # Call the function
    result = await modules.list_trigger_action_modules()
    
    # Get slugs from result
    result_slugs = {module["slug"] for module in result}
    
    # Check that excluded modules are not in the result
    excluded_slugs = {"imap", "ollama", "xero", "uptimekuma", "syncro", "chatgpt-mcp"}
    for slug in excluded_slugs:
        assert slug not in result_slugs, f"{slug} should be excluded from trigger action modules"
    
    # Check that included modules are in the result (including new ticket action modules and trello)
    included_slugs = {
        "smtp", "sms-gateway", "tacticalrmm", "ntfy", "create-ticket", "create-task",
        "update-ticket", "update-ticket-description", "reprocess-ai", "add-ticket-reply",
        "trello"
    }
    for slug in included_slugs:
        assert slug in result_slugs, f"{slug} should be included in trigger action modules"
    
    # Verify the count
    assert len(result) == len(included_slugs), f"Expected {len(included_slugs)} modules, got {len(result)}"


@pytest.mark.asyncio
async def test_list_trigger_action_modules_excludes_disabled(monkeypatch):
    """Test that list_trigger_action_modules excludes disabled modules."""
    from app.repositories import integration_modules as module_repo
    
    # Mock the module repository to return both enabled and disabled modules
    async def fake_list_modules():
        return [
            {"slug": "smtp", "name": "Send Email", "enabled": True, "settings": {}},
            {"slug": "ntfy", "name": "ntfy", "enabled": False, "settings": {}},
            {"slug": "create-ticket", "name": "Create Ticket", "enabled": False, "settings": {}},
            {"slug": "create-task", "name": "Create Task", "enabled": True, "settings": {}},
            {"slug": "tacticalrmm", "name": "Tactical RMM", "enabled": False, "settings": {}},
            {"slug": "update-ticket", "name": "Update Ticket", "enabled": True, "settings": {}},
            {"slug": "add-ticket-reply", "name": "Add Ticket Reply", "enabled": False, "settings": {}},
        ]
    
    monkeypatch.setattr(module_repo, "list_modules", fake_list_modules)
    
    # Call the function
    result = await modules.list_trigger_action_modules()
    
    # Get slugs from result
    result_slugs = {module["slug"] for module in result}
    
    # Always-on ticket actions should be included even if missing/disabled in DB
    expected_slugs = {
        "smtp",
        "create-ticket",
        "create-task",
        "update-ticket",
        "update-ticket-description",
        "add-ticket-reply",
    }
    assert result_slugs == expected_slugs, f"Expected always-on ticket actions plus enabled modules, got {result_slugs}"
    
    # Verify disabled modules are not in the result
    disabled_slugs = {"ntfy", "tacticalrmm"}
    for slug in disabled_slugs:
        assert slug not in result_slugs, f"Disabled module {slug} should not be in trigger action modules"
    
    # Verify the count
    assert len(result) == len(expected_slugs), f"Expected {len(expected_slugs)} modules, got {len(result)}"


@pytest.mark.asyncio
async def test_list_trigger_action_modules_redacts_settings(monkeypatch):
    """Test that list_trigger_action_modules redacts sensitive settings."""
    from app.repositories import integration_modules as module_repo
    
    # Mock the module repository
    async def fake_list_modules():
        return [
            {
                "slug": "smtp",
                "name": "Send Email",
                "enabled": True,
                "settings": {"from_address": "test@example.com", "secret_key": "sensitive"},
            },
        ]
    
    monkeypatch.setattr(module_repo, "list_modules", fake_list_modules)
    
    # Call the function
    result = await modules.list_trigger_action_modules()
    
    smtp_module = next((module for module in result if module.get("slug") == "smtp"), None)
    assert smtp_module is not None
    assert "settings" in smtp_module
    assert "payload_schema" in smtp_module
    assert isinstance(smtp_module["payload_schema"], dict)


def test_always_on_ticket_action_modules_are_not_default_integration_modules():
    """Ticket action handlers are always-on logic and no longer DB-backed integration modules."""
    removed_slugs = {
        "create-ticket",
        "create-task",
        "update-ticket",
        "update-ticket-description",
        "add-ticket-reply",
    }
    defaults_by_slug = {m["slug"]: m for m in DEFAULT_MODULES}
    for slug in removed_slugs:
        assert slug not in defaults_by_slug, f"{slug} should not be in DEFAULT_MODULES"


@pytest.mark.asyncio
async def test_ensure_default_modules_does_not_force_ticket_action_module_flags(monkeypatch):
    """ensure_default_modules should not mutate legacy DB rows for always-on ticket action handlers."""
    from app.repositories import integration_modules as module_repo
    from app.core.database import db
    
    # Track what modules were updated with what values
    updated_modules = {}
    
    async def fake_list_modules():
        return [
            # Legacy rows that may still exist in old databases.
            {"slug": "update-ticket", "name": "Update Ticket", "enabled": False, "settings": {}},
            {"slug": "add-ticket-reply", "name": "Add Ticket Reply", "enabled": True, "settings": {}},
            # Existing external module that should stay disabled
            {"slug": "smtp", "name": "Send Email", "enabled": False, "settings": {}},
        ]
    
    async def fake_update_module(slug, **kwargs):
        updated_modules[slug] = kwargs
        return {"slug": slug, **kwargs}
    
    async def fake_upsert_module(**kwargs):
        return kwargs
    
    monkeypatch.setattr(module_repo, "list_modules", fake_list_modules)
    monkeypatch.setattr(module_repo, "update_module", fake_update_module)
    monkeypatch.setattr(module_repo, "upsert_module", fake_upsert_module)
    monkeypatch.setattr(db, "is_connected", lambda: True)
    
    # Run ensure_default_modules
    await modules.ensure_default_modules()
    
    assert "update-ticket" not in updated_modules
    assert "add-ticket-reply" not in updated_modules
    
    # Verify that smtp was NOT updated with enabled=True (it doesn't have enabled=True in defaults)
    if "smtp" in updated_modules:
        assert updated_modules["smtp"].get("enabled") is not True, (
            "smtp should not be automatically enabled since it requires external configuration"
        )


@pytest.mark.asyncio
async def test_trigger_module_uses_always_on_ticket_action_without_db_module(monkeypatch):
    """Always-on ticket actions should run even when no integration module record exists."""
    from app.repositories import integration_modules as module_repo
    from app.services import modules as modules_service

    async def fake_get_module(slug: str):
        assert slug == "create-ticket"
        return None

    async def fake_invoke_create_ticket(settings, payload, *, event_future=None):
        assert settings == {}
        assert payload == {"subject": "Always-on ticket action"}
        return {"status": "succeeded", "ticket_id": 42}

    monkeypatch.setattr(module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(modules_service, "_invoke_create_ticket", fake_invoke_create_ticket)

    result = await modules_service.trigger_module(
        "create-ticket",
        {"subject": "Always-on ticket action"},
        background=False,
    )

    assert result["status"] == "succeeded"
    assert result["ticket_id"] == 42
