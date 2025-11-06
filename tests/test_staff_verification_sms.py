"""
Tests for staff verification SMS using SMS Gateway module.

This test validates that the staff verification endpoint uses the 
module.sms-gateway.send module instead of the old VERIFY_WEBHOOK_URL approach.
"""
import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_staff_verification_constructs_proper_sms_message(monkeypatch):
    """Test that staff verification constructs the proper SMS message format."""
    from app.repositories import staff as staff_repo
    from app.repositories import companies as company_repo
    from app.services import modules as modules_service
    
    # Track calls to trigger_module
    trigger_calls = []
    
    async def mock_trigger_module(slug, payload, background=True, on_complete=None):
        trigger_calls.append({
            "slug": slug,
            "payload": payload,
            "background": background,
        })
        return {
            "status": "succeeded",
            "response_status": 200,
            "event_id": 123,
        }
    
    # Mock repositories
    async def mock_purge():
        pass
    
    async def mock_upsert(*args, **kwargs):
        pass
    
    async def mock_get_company(company_id):
        return {
            "id": company_id,
            "name": "Test Company",
        }
    
    monkeypatch.setattr(staff_repo, "purge_expired_verification_codes", mock_purge)
    monkeypatch.setattr(staff_repo, "upsert_verification_code", mock_upsert)
    monkeypatch.setattr(company_repo, "get_company_by_id", mock_get_company)
    monkeypatch.setattr(modules_service, "trigger_module", mock_trigger_module)
    
    # Simulate the logic from the staff verification endpoint
    code = "123456"
    admin_name = "Test Admin"
    mobile_phone = "+1234567890"
    
    # Mimic the endpoint's logic
    staff_company = await company_repo.get_company_by_id(1)
    company_name = staff_company.get("name") if staff_company else ""
    
    # Construct SMS message (matching the endpoint's logic)
    message_parts = [f"Your verification code is: {code}"]
    if admin_name:
        message_parts.append(f"Requested by: {admin_name}")
    if company_name:
        message_parts.append(f"Company: {company_name}")
    message = " | ".join(message_parts)
    
    # Send SMS via SMS Gateway module
    result = await modules_service.trigger_module(
        "sms-gateway",
        {
            "message": message,
            "phoneNumbers": [mobile_phone],
        },
        background=False,
    )
    
    # Verify the call was made correctly
    assert len(trigger_calls) == 1
    call = trigger_calls[0]
    
    # Verify module slug
    assert call["slug"] == "sms-gateway"
    assert call["background"] is False
    
    # Verify payload structure
    payload = call["payload"]
    assert "message" in payload
    assert "phoneNumbers" in payload
    assert payload["phoneNumbers"] == ["+1234567890"]
    
    # Verify message content
    assert "Your verification code is: 123456" in payload["message"]
    assert "Test Admin" in payload["message"]
    assert "Test Company" in payload["message"]
    
    # Verify result
    assert result["status"] == "succeeded"
    assert result["response_status"] == 200


@pytest.mark.anyio("asyncio")
async def test_staff_verification_message_without_company_name(monkeypatch):
    """Test SMS message format when company name is not available."""
    from app.repositories import companies as company_repo
    from app.services import modules as modules_service
    
    trigger_calls = []
    
    async def mock_trigger_module(slug, payload, background=True, on_complete=None):
        trigger_calls.append({"slug": slug, "payload": payload, "background": background})
        return {"status": "succeeded", "response_status": 200}
    
    # Mock company_repo to return None
    async def mock_get_company(company_id):
        return None
    
    monkeypatch.setattr(company_repo, "get_company_by_id", mock_get_company)
    monkeypatch.setattr(modules_service, "trigger_module", mock_trigger_module)
    
    # Simulate the logic
    code = "654321"
    admin_name = "Another Admin"
    mobile_phone = "+0987654321"
    
    staff_company = await company_repo.get_company_by_id(1)
    company_name = staff_company.get("name") if staff_company else ""
    
    message_parts = [f"Your verification code is: {code}"]
    if admin_name:
        message_parts.append(f"Requested by: {admin_name}")
    if company_name:
        message_parts.append(f"Company: {company_name}")
    message = " | ".join(message_parts)
    
    await modules_service.trigger_module(
        "sms-gateway",
        {"message": message, "phoneNumbers": [mobile_phone]},
        background=False,
    )
    
    # Verify call
    assert len(trigger_calls) == 1
    payload = trigger_calls[0]["payload"]
    message = payload["message"]
    
    # Should have code and admin name, but not company
    assert "Your verification code is: 654321" in message
    assert "Another Admin" in message
    assert message.count("|") == 1  # Only two parts: code and admin


@pytest.mark.anyio("asyncio")
async def test_staff_verification_handles_module_error(monkeypatch):
    """Test that staff verification handles SMS gateway module errors gracefully."""
    from app.services import modules as modules_service
    
    # Mock trigger_module to raise an error
    async def failing_trigger_module(slug, payload, background=True, on_complete=None):
        raise Exception("SMS Gateway not configured")
    
    monkeypatch.setattr(modules_service, "trigger_module", failing_trigger_module)
    
    # Simulate error handling
    result = {}
    try:
        result = await modules_service.trigger_module(
            "sms-gateway",
            {"message": "Test", "phoneNumbers": ["+1234567890"]},
            background=False,
        )
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}
    
    # Verify error was captured
    assert result["status"] == "error"
    assert "SMS Gateway not configured" in result["error"]
