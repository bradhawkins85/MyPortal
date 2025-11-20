"""Test for the specific issue reported: {{ticket.number}} not rendering in automations."""
import pytest
from datetime import datetime, timezone

from app.services import value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_exact_problem_statement_scenario():
    """Test the exact JSON from the problem statement."""
    # Simulate enriched ticket context as it would be passed to automations
    context = {
        "ticket": {
            "id": 123,
            "ticket_number": "TKT-456",
            "number": "TKT-456",  # Added by _enrich_ticket_context
            "subject": "Printer Issue",
            "description": "The printer is not working",
            "priority": "high",
            "status": "open",
            "created_at": datetime(2025, 11, 20, 10, 30, tzinfo=timezone.utc),
            "requester": {
                "id": 10,
                "email": "customer@example.com",
                "display_name": "Customer Name",
            },
            "latest_reply": {
                "id": 5,
                "body": "We are looking into this issue.",
                "author": {
                    "email": "support@example.com",
                    "display_name": "Support Team"
                },
                "created_at": datetime(2025, 11, 20, 11, 0, tzinfo=timezone.utc),
            }
        }
    }
    
    # This is the exact payload from the problem statement
    payload = {
        "html": "<p> {{ticket.latest_reply.body}}</p>",
        "recipients": ["{{ticket.requester.email}}"],
        "subject": "RE: Ticket #: {{ticket.number}} - {{ticket.subject}}",
        "text": "{{ticket.latest_reply.body}}"
    }
    
    # Render the payload as would happen in automation execution
    rendered = await value_templates.render_value_async(payload, context)
    
    # Verify each field renders correctly
    assert rendered["html"] == "<p> We are looking into this issue.</p>", \
        "ticket.latest_reply.body should render in html field"
    
    assert rendered["recipients"] == ["customer@example.com"], \
        "ticket.requester.email should render in recipients array"
    
    assert "TKT-456" in rendered["subject"], \
        "ticket.number should render in subject field"
    assert rendered["subject"] == "RE: Ticket #: TKT-456 - Printer Issue", \
        f"Subject should be fully rendered, got: {rendered['subject']}"
    
    assert rendered["text"] == "We are looking into this issue.", \
        "ticket.latest_reply.body should render in text field"
    
    print("\n✓ All variables from the problem statement rendered correctly!")
    print(f"  - ticket.number: {rendered['subject']}")
    print(f"  - ticket.requester.email: {rendered['recipients']}")
    print(f"  - ticket.latest_reply.body: {rendered['text']}")


@pytest.mark.anyio
async def test_ticket_number_without_enrichment():
    """Test that ticket.number doesn't exist without the enrichment step."""
    # This simulates what would happen if _enrich_ticket_context wasn't called
    context_without_enrichment = {
        "ticket": {
            "id": 123,
            "ticket_number": "TKT-789",
            # Note: NO "number" alias here
            "subject": "Test",
        }
    }
    
    template = "Ticket: {{ticket.number}}"
    rendered = await value_templates.render_string_async(template, context_without_enrichment)
    
    # Without the enrichment, ticket.number would be empty
    # This test documents that enrichment is necessary
    assert rendered == "Ticket: ", \
        "Without enrichment, ticket.number should not resolve"


@pytest.mark.anyio
async def test_ticket_number_with_enrichment():
    """Test that ticket.number works when enrichment adds it."""
    # This simulates what happens after _enrich_ticket_context is called
    context_with_enrichment = {
        "ticket": {
            "id": 123,
            "ticket_number": "TKT-789",
            "number": "TKT-789",  # Added by _enrich_ticket_context
            "subject": "Test",
        }
    }
    
    template = "Ticket: {{ticket.number}}"
    rendered = await value_templates.render_string_async(template, context_with_enrichment)
    
    # With enrichment, ticket.number resolves correctly
    assert rendered == "Ticket: TKT-789", \
        "With enrichment, ticket.number should resolve to the ticket_number value"
