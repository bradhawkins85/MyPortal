from app.main import app


def test_admin_session_drilldown_routes_are_registered():
    routes = {
        (getattr(route, "path", None), frozenset(getattr(route, "methods", set())))
        for route in app.routes
    }
    assert any(
        path == "/admin/sessions/{session_id}" and "GET" in methods
        for path, methods in routes
    )
    assert any(
        path == "/admin/sessions/{session_id}/revoke" and "POST" in methods
        for path, methods in routes
    )


def test_admin_benchmarking_page_route_is_registered():
    assert any(
        getattr(route, "path", None) == "/admin/benchmarking"
        and "GET" in getattr(route, "methods", set())
        for route in app.routes
    )
