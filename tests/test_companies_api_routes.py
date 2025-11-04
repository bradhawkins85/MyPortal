"""Tests for companies API routes to verify correct route paths."""

from __future__ import annotations

from app.api.routes import companies


def test_companies_router_has_correct_prefix():
    """Verify that the companies router has the /api prefix."""
    assert companies.router.prefix == "/api/companies"


def test_recurring_invoice_items_routes_exist():
    """Verify that recurring invoice items routes are registered."""
    route_paths = [route.path for route in companies.router.routes]
    
    # Check that the recurring invoice items routes exist
    # The paths include the router prefix
    assert "/api/companies/{company_id}/recurring-invoice-items" in route_paths
    assert "/api/companies/{company_id}/recurring-invoice-items/{item_id}" in route_paths


def test_recurring_invoice_items_post_route_exists():
    """Verify that POST route for creating recurring invoice items exists."""
    for route in companies.router.routes:
        if route.path == "/api/companies/{company_id}/recurring-invoice-items":
            assert "POST" in route.methods
            return
    assert False, "POST route for recurring-invoice-items not found"


def test_recurring_invoice_items_get_route_exists():
    """Verify that GET route for listing recurring invoice items exists."""
    for route in companies.router.routes:
        if route.path == "/api/companies/{company_id}/recurring-invoice-items":
            assert "GET" in route.methods
            return
    assert False, "GET route for recurring-invoice-items not found"

