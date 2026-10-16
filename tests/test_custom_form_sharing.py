from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Response
from pydantic import ValidationError

from app.api.dependencies.auth import get_current_user
from app.api.routes import forms as api
from app.core.database import db
from app.schemas.forms import FormShareLinkCreate
from app.security.csrf import CSRFMiddleware
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import custom_forms

FORM = {"id": 1, "slug": "support", "name": "Support", "active": True}
ADMIN = {"id": 9, "is_super_admin": True}
TOKEN = "a" * 43
FIELD = {"id": 10, "field_type": "text", "label": "Issue", "required": True, "config": {}, "default_value": None}


@pytest.fixture
def sharing(monkeypatch):
    link = {"id": 1, "form_id": 1, "company_id": 7, "expires_at": None, "revoked_at": None}
    monkeypatch.setattr(api.forms_repo, "get_share_link_by_hash", AsyncMock(return_value=link))
    monkeypatch.setattr(api.forms_repo, "get_form", AsyncMock(return_value=FORM.copy()))
    monkeypatch.setattr(api.forms_repo, "list_fields", AsyncMock(return_value=[FIELD]))
    monkeypatch.setattr(api.companies_repo, "get_company_by_id", AsyncMock(return_value={"id": 7, "name": "Example"}))
    app = FastAPI()
    app.include_router(api.router)
    # Exercise real CSRF middleware with no portal session; authenticated routes
    # must still require CSRF while capability routes can submit anonymously.
    monkeypatch.setattr("app.security.csrf.get_settings", lambda: SimpleNamespace(enable_csrf=True, session_cookie_name="session"))
    app.add_middleware(CSRFMiddleware, manager=SimpleNamespace(load_session=AsyncMock(return_value=None)))
    from app.security.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
    return app, link


@pytest.mark.asyncio
async def test_anonymous_page_and_submit_with_real_csrf(sharing, monkeypatch):
    app, link = sharing
    submit = AsyncMock(return_value={"ticket_id": 77, "submission_id": 3, "attachments": [{"id": 4}]})
    monkeypatch.setattr(custom_forms, "submit_form", submit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        page = await client.get(f"/public/forms/support?token={TOKEN}")
        assert page.status_code == 200
        assert 'name="field_10"' in page.text
        assert 'action="https://portal.test/public/forms/support?token=' in page.text
        assert page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["cache-control"] == "no-store"
        assert "/auth/logout" not in page.text
        response = await client.post(f"/public/forms/support?token={TOKEN}", data={"field_10": "Help", "company_id": "999"})
        assert response.status_code == 201
        assert response.json() == {"status": "ok", "message": "Your submission was received."}
        assert submit.await_args.kwargs["requester"] == {"company_id": 7}
        assert submit.await_args.kwargs["values"] == {10: "Help"}
        html = await client.post(f"/public/forms/support?token={TOKEN}", data={"field_10": "Help"}, headers={"Accept": "text/html"})
        assert html.status_code == 200
        assert "Your request has been submitted" in html.text
        assert "/tickets/" not in html.text
    api.forms_repo.get_share_link_by_hash.assert_awaited_with(hashlib.sha256(TOKEN.encode()).hexdigest())


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "invalid", "unknown", "expired", "revoked", "inactive", "wrong_form"])
async def test_invalid_links_cannot_read_or_submit(sharing, monkeypatch, case):
    app, link = sharing
    token = TOKEN
    slug = "support"
    if case == "missing":
        token = ""
    elif case == "invalid":
        token = "short"
    elif case == "unknown":
        api.forms_repo.get_share_link_by_hash.return_value = None
    elif case == "expired":
        link["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif case == "revoked":
        link["revoked_at"] = datetime.now(timezone.utc)
    elif case == "inactive":
        api.forms_repo.get_form.return_value["active"] = False
    elif case == "wrong_form":
        slug = "another-form"
    submit = AsyncMock()
    monkeypatch.setattr(custom_forms, "submit_form", submit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        for method in ["GET", "POST"]:
            response = await client.request(method, f"/public/forms/{slug}?token={token}")
            assert response.status_code == 404
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_is_rechecked_after_page_load(sharing):
    app, link = sharing
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        assert (await client.get(f"/public/forms/support?token={TOKEN}")).status_code == 200
        link["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).replace(tzinfo=None).isoformat()
        assert (await client.post(f"/public/forms/support?token={TOKEN}")).status_code == 404


@pytest.mark.asyncio
async def test_public_validation_preserves_answers(sharing, monkeypatch):
    app, _ = sharing
    monkeypatch.setattr(custom_forms, "submit_form", AsyncMock(side_effect=custom_forms.FormValidationError(["Check your answer."])))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        response = await client.post(f"/public/forms/support?token={TOKEN}", data={"field_10": "Keep me"}, headers={"Accept": "text/html"})
    assert response.status_code == 400
    assert "Check your answer." in response.text
    assert 'value="Keep me"' in response.text
    assert f"token={TOKEN}" in response.text


@pytest.mark.parametrize("expiry", ["2000-01-01T00:00:00+00:00", "2099-01-01T00:00:00", "invalid"])
def test_expiry_requires_future_aware_datetime(expiry):
    with pytest.raises(ValidationError):
        FormShareLinkCreate(company_id=7, expires_at=expiry)


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [None, datetime(2099, 1, 1, tzinfo=timezone.utc)])
async def test_admin_creation_hashes_tokens_and_normalizes_expiry(sharing, monkeypatch, expires_at):
    app, _ = sharing
    create = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(api.forms_repo, "create_share_link", create)
    request = httpx.Request("POST", "https://portal.test/api/forms/1/share-links")
    from starlette.requests import Request
    req = Request({"type": "http", "method": "POST", "scheme": "https", "server": ("portal.test", 443), "path": request.url.path, "headers": [], "router": app.router})
    result = await api.api_create_share_link(1, FormShareLinkCreate(company_id=7, expires_at=expires_at), req, Response(), ADMIN)
    token = result["url"].split("token=")[1]
    assert len(token) == 43
    assert create.await_args.kwargs["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert create.await_args.kwargs["expires_at"] == (expires_at.replace(tzinfo=None) if expires_at else None)
    assert decrypt_secret(create.await_args.kwargs["token_encrypted"], allow_plaintext=False) == token
    assert "token_hash" not in result


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_links(sharing):
    for fn, args in [
        (api.api_create_share_link, [1, FormShareLinkCreate(company_id=7), None, Response()]),
        (api.api_list_share_links, [1, None, Response()]),
        (api.api_revoke_share_link, [1, 1]),
        (api.save_share_links_page, [1, None]),
    ]:
        with pytest.raises(HTTPException) as exc:
            await fn(*args, user={"id": 4})
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_anonymous_service_ticket_and_upload_attribution(monkeypatch):
    from app.services import tickets, ticket_attachments
    create_ticket = AsyncMock(return_value={"id": 77})
    save_file = AsyncMock(return_value={"id": 4, "filename": "image.png"})
    record = AsyncMock(return_value={"id": 3})
    monkeypatch.setattr(tickets, "create_ticket", create_ticket)
    monkeypatch.setattr(ticket_attachments, "save_uploaded_file", save_file)
    monkeypatch.setattr(api.forms_repo, "create_submission", record)
    await custom_forms.submit_form(form=FORM, fields=[FIELD, {"id": 11, "field_type": "file", "label": "Photo"}],
                                   values={10: "Help"}, files={11: [SimpleNamespace(filename="image.png")]}, requester={"company_id": 7})
    assert create_ticket.await_args.kwargs["requester_id"] is None
    assert create_ticket.await_args.kwargs["company_id"] == 7
    assert save_file.await_args.kwargs["uploaded_by_user_id"] is None
    assert save_file.await_args.kwargs["access_level"] == "closed"
    assert record.await_args.kwargs["submitted_by"] is None


def test_share_link_migration_sqlite_and_metadata():
    path = Path(__file__).resolve().parents[1] / "migrations/492_custom_form_share_links.sql"
    assert db._migration_metadata(path)["phase"] == "expand"
    connection = sqlite3.connect(":memory:")
    connection.executescript(db._adapt_sql_for_sqlite(path.read_text()))
    connection.execute("INSERT INTO custom_form_share_links (form_id, company_id, token_hash) VALUES (1, 7, 'digest')")
    assert connection.execute("SELECT expires_at FROM custom_form_share_links").fetchone() == (None,)
    connection.close()


@pytest.mark.asyncio
async def test_portal_cookie_does_not_change_anonymous_attribution(sharing, monkeypatch):
    app, _ = sharing
    current_user = AsyncMock(return_value={"id": 99, "company_id": 999})
    app.dependency_overrides[get_current_user] = current_user
    submit = AsyncMock(return_value={"ticket_id": 77, "submission_id": 3, "attachments": []})
    monkeypatch.setattr(custom_forms, "submit_form", submit)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test", cookies={"session": "existing-session"}) as client:
        response = await client.post(f"/public/forms/support?token={TOKEN}", data={"field_10": "Help"})
    assert response.status_code == 201
    assert submit.await_args.kwargs["requester"] == {"company_id": 7}
    current_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_submission_cannot_use_magic_token_to_bypass_csrf(sharing):
    app, _ = sharing
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        response = await client.post(f"/forms/support/submit?token={TOKEN}", data={"field_10": "Help"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_repository_revocation_is_scoped_to_form(monkeypatch):
    from app.repositories import forms
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    for name in ["492_custom_form_share_links.sql", "502_custom_form_share_link_reveal.sql"]:
        connection.executescript(db._adapt_sql_for_sqlite((migrations / name).read_text()))

    async def insert(sql, params):
        return connection.execute(sql, params).lastrowid

    async def fetch(sql, params):
        row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    async def execute(sql, params):
        connection.execute(sql, params)

    monkeypatch.setattr(forms.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(forms.db, "fetch_one", fetch)
    monkeypatch.setattr(forms.db, "execute", execute)
    link = await forms.create_share_link(form_id=1, company_id=7, token_hash="digest", token_encrypted="cipher", expires_at=None, created_by=9)
    assert "token_hash" not in link
    assert link["token_encrypted"] == "cipher"
    await forms.revoke_share_link(2, link["id"])
    assert (await forms.get_share_link_by_hash("digest"))["revoked_at"] is None
    await forms.revoke_share_link(1, link["id"])
    assert (await forms.get_share_link_by_hash("digest"))["revoked_at"] is not None
    connection.close()


def _admin_request(app, path="/admin/custom-forms/1/sharing"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "scheme": "https", "server": ("portal.test", 443),
                    "path": path, "query_string": b"", "headers": [], "router": app.router})


@pytest.mark.asyncio
async def test_saved_links_can_be_revealed_after_creation(sharing, monkeypatch):
    app, _ = sharing
    links = [
        {"id": 3, "company_id": 7, "company_name": "Example", "token_encrypted": encrypt_secret(TOKEN), "expires_at": None, "revoked_at": None},
        {"id": 2, "company_id": 7, "company_name": "Example", "token_encrypted": None, "expires_at": None, "revoked_at": None},
        {"id": 1, "company_id": 7, "company_name": "Example", "token_encrypted": encrypt_secret(TOKEN), "expires_at": None, "revoked_at": "2026-01-01"},
    ]
    monkeypatch.setattr(api.forms_repo, "list_share_links", AsyncMock(return_value=links))
    response = Response()
    result = await api.api_list_share_links(1, _admin_request(app), response, user=ADMIN)
    assert result[0]["url"] == f"https://portal.test/public/forms/support?token={TOKEN}"
    assert result[1]["url"] is None  # created before tokens were stored encrypted
    assert result[2]["url"] is None  # revoked links are never revealed
    assert all("token_encrypted" not in link for link in result)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_qr_code_png_download_and_print(sharing, monkeypatch):
    app, _ = sharing
    link = {"id": 3, "form_id": 1, "company_id": 7, "company_name": "Example", "token_encrypted": encrypt_secret(TOKEN), "expires_at": None, "revoked_at": None}
    monkeypatch.setattr(api.forms_repo, "get_share_link", AsyncMock(return_value=link))
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        png = await client.get("/admin/custom-forms/1/sharing/3/qr.png")
        assert png.status_code == 200
        assert png.headers["content-type"] == "image/png"
        assert png.content.startswith(b"\x89PNG")
        assert png.headers["cache-control"] == "no-store"
        assert "content-disposition" not in png.headers
        download = await client.get("/admin/custom-forms/1/sharing/3/qr.png?download=1")
        assert download.headers["content-disposition"] == 'attachment; filename="support-link-3-qr.png"'
    api.forms_repo.get_share_link.assert_awaited_with(1, 3)

    import app.main as main_module
    monkeypatch.setattr(main_module, "_require_super_admin_page", AsyncMock(return_value=(ADMIN, None)))
    printable = await main_module.custom_form_share_link_print(1, 3, _admin_request(app, "/admin/custom-forms/1/sharing/3/print"))
    body = printable.body.decode()
    assert printable.status_code == 200
    assert printable.headers["cache-control"] == "no-store"
    assert "data:image/png;base64," in body
    assert f"token={TOKEN}" in body
    assert "data-cf-print" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "revoked", "legacy", "rotated_key"])
async def test_qr_code_unavailable_without_revealable_link(sharing, monkeypatch, case):
    app, _ = sharing
    link = {"id": 3, "form_id": 1, "company_id": 7, "token_encrypted": encrypt_secret(TOKEN), "expires_at": None, "revoked_at": None}
    if case == "revoked":
        link["revoked_at"] = "2026-01-01"
    elif case == "legacy":
        link["token_encrypted"] = None
    elif case == "rotated_key":
        parts = link["token_encrypted"].split(":")
        parts[2] = parts[2][:-4] + ("AAA=" if not parts[2].endswith("AAA=") else "BBB=")
        link["token_encrypted"] = ":".join(parts)
    monkeypatch.setattr(api.forms_repo, "get_share_link", AsyncMock(return_value=None if case == "missing" else link))
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://portal.test") as client:
        assert (await client.get("/admin/custom-forms/1/sharing/3/qr.png")).status_code == 404
    import app.main as main_module
    monkeypatch.setattr(main_module, "_require_super_admin_page", AsyncMock(return_value=(ADMIN, None)))
    with pytest.raises(HTTPException) as exc:
        await main_module.custom_form_share_link_print(1, 3, _admin_request(app))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_non_admin_cannot_view_qr_codes(sharing):
    app, _ = sharing
    with pytest.raises(HTTPException) as exc:
        await api.share_link_qr(1, 3, _admin_request(app), user={"id": 4})
    assert exc.value.status_code == 403
    import app.main as main_module
    with pytest.raises(HTTPException) as exc:
        await main_module.forms_api._revealed_share_link(1, 3, _admin_request(app), {"id": 4})
    assert exc.value.status_code == 403


def test_reveal_migration_adds_nullable_column():
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    path = migrations / "502_custom_form_share_link_reveal.sql"
    assert db._migration_metadata(path)["phase"] == "expand"
    connection = sqlite3.connect(":memory:")
    connection.executescript(db._adapt_sql_for_sqlite((migrations / "492_custom_form_share_links.sql").read_text()))
    connection.execute("INSERT INTO custom_form_share_links (form_id, company_id, token_hash) VALUES (1, 7, 'digest')")
    connection.executescript(db._adapt_sql_for_sqlite(path.read_text()))
    assert connection.execute("SELECT token_encrypted FROM custom_form_share_links").fetchone() == (None,)
    connection.close()
