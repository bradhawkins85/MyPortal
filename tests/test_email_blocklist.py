import pytest

from app.repositories import email_blocklist


class FakeDb:
    def __init__(self):
        self.rows = [{"email": "blocked@example.com"}]
        self.last_query = None
        self.last_params = None

    async def fetch_all(self, query, params=None):
        self.last_query = query
        self.last_params = params or {}
        wanted = set(self.last_params.values())
        return [row for row in self.rows if row["email"] in wanted]


@pytest.mark.anyio
async def test_filter_allowed_removes_blocklisted_addresses(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(email_blocklist, "db", fake_db)

    allowed, blocked = await email_blocklist.filter_allowed([
        "Allowed@Example.com",
        "blocked@example.com",
        "allowed@example.com",
        "BLOCKED@example.com",
    ])

    assert allowed == ["Allowed@Example.com"]
    assert blocked == ["blocked@example.com"]


def test_normalize_email_rejects_invalid_address():
    with pytest.raises(ValueError):
        email_blocklist.normalize_email("not-an-email")


@pytest.mark.anyio
async def test_admin_email_blocklist_page_uses_base_template_context(monkeypatch):
    """Regression: page rendering must include base context such as plausible_config."""
    from fastapi import Request
    from fastapi.responses import HTMLResponse

    from app import main as main_module
    from app.features.tickets import admin_routes

    async def fake_require_helpdesk_page(request: Request):
        return {"id": 1, "email": "admin@example.com", "is_super_admin": True}, None

    async def fake_list_entries(**kwargs):
        return [
            {
                "id": 1,
                "email": "blocked@example.com",
                "source": "manual",
                "updated_at": None,
            }
        ]

    captured = {}

    async def fake_render_template(template_name, request, user, *, extra=None):
        captured["template_name"] = template_name
        captured["user"] = user
        captured["extra"] = extra or {}
        return HTMLResponse("ok")

    monkeypatch.setattr(main_module, "_require_helpdesk_page", fake_require_helpdesk_page)
    monkeypatch.setattr(main_module, "_render_template", fake_render_template)
    monkeypatch.setattr(email_blocklist, "list_entries", fake_list_entries)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/tickets/email-blocklist",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "client": ("testclient", 50000),
    }
    request = Request(scope)

    response = await admin_routes.admin_email_blocklist_page(request, search=None)

    assert response.status_code == 200
    assert captured["template_name"] == "admin/email_blocklist.html"
    assert captured["user"]["is_super_admin"] is True
    assert captured["extra"]["title"] == "Email blocklist"
    assert captured["extra"]["search"] == ""
    assert captured["extra"]["entries"][0]["updated_iso"] is None
