from app.main import app


def test_admin_audit_logs_page_route_is_registered():
    """Regression: the sidebar Audit Trail link must resolve to a page route."""

    assert any(
        getattr(route, "path", None) == "/admin/audit-logs"
        and "GET" in getattr(route, "methods", set())
        for route in app.routes
    )
