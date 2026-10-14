import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

import app.main as main_module


@pytest.mark.asyncio
@pytest.mark.parametrize("form_id", [None, 1])
async def test_custom_forms_admin_renders_database_timestamps(monkeypatch, form_id):
    created_at = datetime(2026, 10, 14, 1, 30)
    updated_at = datetime(2026, 10, 14, 2, 30, tzinfo=timezone.utc)
    form = {
        "id": 1,
        "slug": "support",
        "name": "Support",
        "active": True,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    field = {
        "id": 10,
        "form_id": 1,
        "field_type": "select",
        "label": "Issue </script><script>alert(1)</script>",
        "required": True,
        "options": ["Hardware", "Software"],
        "placeholder": None,
        "help_text": None,
        "default_value": None,
        "sort_order": 0,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    monkeypatch.setattr(
        main_module, "_require_super_admin_page",
        AsyncMock(return_value=({"id": 9, "is_super_admin": True}, None)),
    )
    monkeypatch.setattr(main_module.forms_repo, "list_forms", AsyncMock(return_value=[form]))
    monkeypatch.setattr(main_module.forms_repo, "get_form", AsyncMock(return_value=form))
    monkeypatch.setattr(main_module.forms_repo, "list_fields", AsyncMock(return_value=[field]))
    monkeypatch.setattr(main_module.forms_repo, "list_access_user_ids", AsyncMock(return_value=[9]))
    monkeypatch.setattr(main_module.forms_repo, "list_submissions", AsyncMock(return_value=[]))
    monkeypatch.setattr(main_module.user_repo, "list_active_users_for_admin", AsyncMock(return_value=[
        {"id": 9, "first_name": "Ada", "last_name": "Admin", "email": "ada@example.com"},
    ]))

    async def render_content(template_name, request, user, *, extra):
        # Render the real page content without unrelated base-layout services.
        env = Environment(
            loader=ChoiceLoader([
                DictLoader({"base.html": "{% block content %}{% endblock %}"}),
                FileSystemLoader(Path(__file__).resolve().parent.parent / "app/templates"),
            ]),
            autoescape=select_autoescape(("html", "xml")),
        )
        template = env.get_template(template_name)
        body = template.render(extra)
        return HTMLResponse(body)

    monkeypatch.setattr(main_module, "_render_template", render_content)
    request = Request({"type": "http", "method": "GET", "path": "/admin/custom-forms"})
    response = await main_module.custom_forms_admin_page(request, form_id=form_id)

    assert response.status_code == 200
    body = response.body.decode()
    assert "Support" in body
    if form_id is None:
        assert "data-cf-field-data" not in body
    else:
        match = re.search(r'<script[^>]*data-cf-field-data="10"[^>]*>(.*?)</script>', body, re.S)
        assert match is not None
        payload = json.loads(match.group(1))
        assert payload == {**field, "created_at": created_at.isoformat(), "updated_at": updated_at.isoformat()}
        assert "<script>" not in match.group(1)
        assert field["created_at"] is created_at
