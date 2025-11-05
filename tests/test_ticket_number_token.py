"""Test that ticket.number token is available and works correctly."""
import pytest

from app.services import system_variables


def test_ticket_number_token_from_ticket_number_field():
    """Test that TICKET_NUMBER token is created from ticket_number field via 'number' alias."""
    context = {
        "ticket": {
            "id": 123,
            "ticket_number": "TKT-456",
            "number": "TKT-456",  # This alias should be added by _enrich_ticket_context
            "subject": "Test ticket"
        }
    }
    
    # Build context variables
    tokens = system_variables.build_context_variables(
        context,
        stringify=True
    )
    
    # The field ticket.ticket_number should create TICKET_TICKET_NUMBER
    assert "TICKET_TICKET_NUMBER" in tokens
    assert tokens["TICKET_TICKET_NUMBER"] == "TKT-456"
    
    # The field ticket.number should create TICKET_NUMBER
    assert "TICKET_NUMBER" in tokens
    assert tokens["TICKET_NUMBER"] == "TKT-456"
    
    print(f"All ticket tokens: {[k for k in sorted(tokens.keys()) if 'TICKET' in k]}")


def test_system_variables_ticket_number():
    """Test system variables include TICKET_NUMBER via 'number' alias."""
    ticket = {
        "id": 123,
        "ticket_number": "TKT-789",
        "number": "TKT-789",  # This alias should be added by _enrich_ticket_context
        "subject": "Another test"
    }
    
    sys_vars = system_variables.get_system_variables(ticket=ticket)
    
    # Check what ticket-related tokens exist
    ticket_vars = {k: v for k, v in sys_vars.items() if k.startswith("TICKET")}
    print(f"Ticket variables: {ticket_vars}")
    
    # The token TICKET_TICKET_NUMBER should exist
    assert "TICKET_TICKET_NUMBER" in sys_vars
    assert sys_vars["TICKET_TICKET_NUMBER"] == "TKT-789"
    
    # The token TICKET_NUMBER should also exist via the 'number' alias
    assert "TICKET_NUMBER" in sys_vars
    assert sys_vars["TICKET_NUMBER"] == "TKT-789"
