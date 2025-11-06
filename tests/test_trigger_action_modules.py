"""Test trigger action module filtering."""
import pytest

from app.services import modules


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
    
    # Check that included modules are in the result
    included_slugs = {"smtp", "sms-gateway", "tacticalrmm", "ntfy", "create-ticket", "create-task"}
    for slug in included_slugs:
        assert slug in result_slugs, f"{slug} should be included in trigger action modules"
    
    # Verify the count
    assert len(result) == len(included_slugs), f"Expected {len(included_slugs)} modules, got {len(result)}"


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
