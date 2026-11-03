"""Per-company RustDesk unattended access: the company edit form and its save route."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.features.companies import handlers


class _Request:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


def _fake_main(monkeypatch):
    from fastapi.responses import RedirectResponse

    async def require_super_admin(request):
        return {"id": 9, "is_super_admin": True}, None

    def edit_redirect(*, company_id, success=None, error=None):
        return RedirectResponse(url=f"/admin/companies/{company_id}/edit", status_code=303)

    main = SimpleNamespace(_require_super_admin_page=require_super_admin, _company_edit_redirect=edit_redirect)
    monkeypatch.setattr(handlers, "_main", lambda: main)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("form,expected", [({"rustdesk_unattended_access": "1"}, 1), ({}, 0)])
async def test_saving_remote_control_settings_stores_the_choice(monkeypatch, form, expected):
    from app.repositories import companies as companies_repo
    from app.services import audit as audit_service

    _fake_main(monkeypatch)
    saved: dict = {}
    audited: list = []

    async def get_company(company_id):
        return {"id": company_id, "name": "Contoso", "rustdesk_unattended_access": 1}

    async def update_company(company_id, **updates):
        saved.update(company_id=company_id, **updates)
        return updates

    async def record(**kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(companies_repo, "get_company_by_id", get_company)
    monkeypatch.setattr(companies_repo, "update_company", update_company)
    monkeypatch.setattr(audit_service, "record", record)

    response = await handlers.admin_company_remote_control_save(5, _Request(form))
    assert saved == {"company_id": 5, "rustdesk_unattended_access": expected}
    assert response.headers["location"] == "/admin/companies/5/edit#ce-remote-control"
    assert audited[0]["metadata"] == {"rustdesk_unattended_access": bool(expected)}


def test_the_setting_is_an_allowed_company_column():
    from app.repositories import companies as companies_repo

    companies_repo._validate_company_fields({"rustdesk_unattended_access": 0})


def test_route_is_registered_on_the_companies_pack():
    from app.features.companies import admin_routes

    routes = {(route.path, tuple(sorted(route.methods))): route.endpoint for route in admin_routes.router.routes}
    assert routes[("/admin/companies/{company_id}/remote-control", ("POST",))] is handlers.admin_company_remote_control_save


def test_company_edit_page_has_a_remote_control_section():
    from pathlib import Path

    template = Path("app/templates/admin/company_edit.html").read_text()
    start = template.index('data-company-edit-section="remote-control"')
    section = template[start:template.index('<section class="card card--panel ce-section"', start)]
    assert 'action="/admin/companies/{{ company.id }}/remote-control"' in section
    assert 'name="rustdesk_unattended_access"' in section
    assert 'partials/csrf.html' in section
    assert '("remote-control", "ce-remote-control", "Remote control"' in template
