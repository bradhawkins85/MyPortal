"""Test that message templates can be referenced by their slug directly."""
import pytest
from unittest.mock import patch


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_message_template_can_be_referenced_by_slug_directly():
    """Test that templates can be referenced as {{slug}} not just {{template.slug}}."""
    from app.services import value_templates
    from app.services import message_templates
    
    # Mock templates
    mock_templates = [
        {
            "slug": "email.new.ticket.subject",
            "name": "Email New Ticket Subject",
            "content": "New Ticket #{{ticket.number}} - {{ticket.subject}}",
            "content_type": "text/plain",
        },
        {
            "slug": "notification.ticket.updated",
            "name": "Ticket Updated Notification",
            "content": "Ticket {{ticket.number}} was updated",
            "content_type": "text/plain",
        },
    ]
    
    with patch.object(message_templates, 'iter_templates', return_value=mock_templates):
        # Test context with ticket data
        context = {
            "ticket": {
                "number": "TKT-123",
                "subject": "Printer is broken",
            }
        }
        
        # Test 1: Reference template by slug directly (without template. prefix)
        template_ref = "{{email.new.ticket.subject}}"
        result = await value_templates.render_string_async(template_ref, context)
        assert result != "", "Template reference should not be empty"
        assert "TKT-123" in result, f"Should contain ticket number, got: {result}"
        assert "Printer is broken" in result, f"Should contain ticket subject, got: {result}"
        assert "New Ticket #TKT-123" in result, f"Should contain rendered template content, got: {result}"
        
        # Test 2: Reference template with template. prefix (should still work)
        template_ref2 = "{{template.email.new.ticket.subject}}"
        result2 = await value_templates.render_string_async(template_ref2, context)
        assert result2 == result, "Both syntaxes should produce the same result"
        
        # Test 3: Reference template with UPPERCASE syntax (should still work)
        template_ref3 = "{{TEMPLATE_EMAIL_NEW_TICKET_SUBJECT}}"
        result3 = await value_templates.render_string_async(template_ref3, context)
        assert result3 == result, "UPPERCASE syntax should also work"
        
        # Test 4: Use template in a larger string
        message = "Subject: {{email.new.ticket.subject}}"
        result4 = await value_templates.render_string_async(message, context)
        assert "Subject: New Ticket #TKT-123 - Printer is broken" == result4


@pytest.mark.anyio
async def test_message_template_with_dots_in_slug():
    """Test that templates with dots in slug work correctly."""
    from app.services import value_templates
    from app.services import message_templates
    
    mock_templates = [
        {
            "slug": "email.new.ticket.subject",
            "content": "New: {{ticket.number}}",
        },
    ]
    
    with patch.object(message_templates, 'iter_templates', return_value=mock_templates):
        context = {"ticket": {"number": "TKT-456"}}
        
        # The template slug contains dots, which might conflict with context path resolution
        # but should still work as a token lookup
        result = await value_templates.render_string_async(
            "{{email.new.ticket.subject}}", context
        )
        assert "TKT-456" in result, f"Should render template content, got: {result}"


@pytest.mark.anyio
async def test_empty_template_renders_as_empty_string():
    """Test that an empty template renders as an empty string, not None."""
    from app.services import value_templates
    from app.services import message_templates
    
    mock_templates = [
        {
            "slug": "empty.template",
            "content": "",
        },
    ]
    
    with patch.object(message_templates, 'iter_templates', return_value=mock_templates):
        context = {}
        result = await value_templates.render_string_async("{{empty.template}}", context)
        assert result == "", f"Empty template should render as empty string, got: {result!r}"
