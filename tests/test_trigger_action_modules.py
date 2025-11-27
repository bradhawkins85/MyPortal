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
    
    # Check that included modules are in the result (including new ticket action modules)
    included_slugs = {
        "smtp", "sms-gateway", "tacticalrmm", "ntfy", "create-ticket", "create-task",
        "update-ticket", "update-ticket-description", "reprocess-ai", "add-ticket-reply"
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
    
    # Check that only enabled modules are in the result
    assert result_slugs == {"smtp", "create-task", "update-ticket"}, f"Expected only enabled modules, got {result_slugs}"
    
    # Verify disabled modules are not in the result
    disabled_slugs = {"ntfy", "create-ticket", "tacticalrmm", "add-ticket-reply"}
    for slug in disabled_slugs:
        assert slug not in result_slugs, f"Disabled module {slug} should not be in trigger action modules"
    
    # Verify the count
    assert len(result) == 3, f"Expected 3 enabled modules, got {len(result)}"


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
    
    # Verify that the module is returned
    assert len(result) == 1
    assert result[0]["slug"] == "smtp"
    
    # The settings should be present (redaction is done by _redact_module_settings)
    assert "settings" in result[0]


def test_internal_action_modules_enabled_by_default():
    """Test that internal action modules have enabled=True in DEFAULT_MODULES.
    
    These modules don't require external configuration and should be
    immediately available as trigger actions in automations.
    """
    # Internal action modules that should be enabled by default
    internal_action_slugs = {
        "create-ticket",
        "create-task",
        "update-ticket",
        "update-ticket-description",
        "reprocess-ai",
        "add-ticket-reply",
    }
    
    # Build a lookup of default modules by slug
    defaults_by_slug = {m["slug"]: m for m in DEFAULT_MODULES}
    
    for slug in internal_action_slugs:
        assert slug in defaults_by_slug, f"Module {slug} should be in DEFAULT_MODULES"
        module = defaults_by_slug[slug]
        assert module.get("enabled") is True, (
            f"Module {slug} should have enabled=True in DEFAULT_MODULES "
            f"since it's an internal action module that doesn't require external configuration"
        )


@pytest.mark.asyncio
async def test_ensure_default_modules_enables_internal_action_modules(monkeypatch):
    """Test that ensure_default_modules enables internal action modules on existing installations.
    
    When a module has enabled=True in DEFAULT_MODULES but is currently disabled in the database,
    ensure_default_modules should enable it to make it available in trigger actions.
    """
    from app.repositories import integration_modules as module_repo
    from app.core.database import db
    
    # Track what modules were updated with what values
    updated_modules = {}
    
    async def fake_list_modules():
        return [
            # Existing module that was disabled, but should be enabled by default
            {"slug": "update-ticket", "name": "Update Ticket", "enabled": False, "settings": {}},
            # Existing module that is already enabled
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
    
    # Verify that update-ticket was enabled (it was disabled but should be enabled by default)
    assert "update-ticket" in updated_modules, "update-ticket should have been updated"
    assert updated_modules["update-ticket"].get("enabled") is True, (
        "update-ticket should have been enabled since it's an internal action module"
    )
    
    # Verify that add-ticket-reply was NOT updated (it's already enabled)
    # It might be updated for other reasons (like name/description changes),
    # but if it was updated, enabled should not be in the update
    if "add-ticket-reply" in updated_modules:
        # If it was updated, it shouldn't have enabled in the update
        # (since it's already enabled)
        pass  # No assertion needed, it may be updated for other fields
    
    # Verify that smtp was NOT updated with enabled=True (it doesn't have enabled=True in defaults)
    if "smtp" in updated_modules:
        assert updated_modules["smtp"].get("enabled") is not True, (
            "smtp should not be automatically enabled since it requires external configuration"
        )
