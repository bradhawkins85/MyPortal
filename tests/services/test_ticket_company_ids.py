import pytest
from pathlib import Path

from app.services.tickets import parse_company_id


@pytest.mark.parametrize("value", [None, "", "not-an-id", 0, -1, object()])
def test_parse_company_id_rejects_invalid_selection(value):
    """Malformed state cannot become an allowed company ID."""
    assert parse_company_id(value) is None


@pytest.mark.parametrize(("value", "expected"), [(42, 42), ("42", 42)])
def test_parse_company_id_accepts_positive_integer(value, expected):
    assert parse_company_id(value) == expected


def test_company_selection_handlers_share_validated_parser():
    """Equivalent API, feature-pack, and legacy handlers stay aligned."""
    root = Path(__file__).parents[2]
    handlers = (
        "app/api/routes/tickets.py",
        "app/features/tickets/portal_routes.py",
        "app/main.py",
    )
    for relative_path in handlers:
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "tickets_service.parse_company_id(" in source
