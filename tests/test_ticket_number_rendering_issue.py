"""Test for the specific issue reported: {{ticket.number}} not rendering in automations."""
import pytest
from datetime import datetime, timezone

from app.services import value_templates
from app.services.tickets import _enrich_ticket_context


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
async def test_ticket_number_without_explicit_number_key():
    """Test that ticket.number resolves via ticket_number even without an explicit 'number' key.

    The template resolver uses _FIELD_ALIASES so that ``{{ ticket.number }}``
    falls back to ``ticket_number`` automatically, regardless of whether
    _enrich_ticket_context has been called.
    """
    context_without_number_alias = {
        "ticket": {
            "id": 123,
            "ticket_number": "TKT-789",
            # Note: NO explicit "number" key – relies on the alias fallback
            "subject": "Test",
        }
    }

    template = "Ticket: {{ticket.number}}"
    rendered = await value_templates.render_string_async(template, context_without_number_alias)

    # The alias fallback (number → ticket_number) should resolve the value.
    assert rendered == "Ticket: TKT-789", \
        "ticket.number should resolve via ticket_number alias even without explicit enrichment"


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


@pytest.mark.anyio
async def test_ticket_number_falls_back_to_id_when_null():
    """Locally-created tickets have ticket_number=None in the DB.

    After PR 1616 removed the id-fallback, {{ticket.ticket_number}} became
    empty for every locally-created ticket.  _enrich_ticket_context must fall
    back to str(id) when neither ticket_number nor number is set.
    """
    raw_ticket = {
        "id": 42,
        "ticket_number": None,  # as stored in the DB for locally-created tickets
        "subject": "Laptop broken",
    }
    enriched = await _enrich_ticket_context(raw_ticket)
    assert enriched["ticket_number"] == "42", \
        "ticket_number should fall back to str(id) when no explicit number exists"
    assert enriched["number"] == "42", \
        "number alias should also fall back to str(id)"


@pytest.mark.anyio
async def test_ticket_number_id_fallback_renders_in_template():
    """End-to-end: locally-created ticket (ticket_number=None) renders id in template."""
    raw_ticket = {
        "id": 99,
        "ticket_number": None,
        "subject": "Router offline",
    }
    enriched = await _enrich_ticket_context(raw_ticket)
    context = {"ticket": enriched}

    rendered = await value_templates.render_string_async(
        "Ticket #{{ticket.ticket_number}}", context
    )
    assert rendered == "Ticket #99", \
        "{{ticket.ticket_number}} should render the id when ticket_number is NULL in the DB"
