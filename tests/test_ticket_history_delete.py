"""Conversation deletion authorization, ownership, and browser regressions."""
import asyncio
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader
import pytest

from app import main
from app.features.tickets import admin_routes
from app.repositories import call_recordings, tickets
from app.security.csrf import CSRFMiddleware
from conftest import csrf_session


@pytest.fixture
def history_client(monkeypatch):
    user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(side_effect=lambda request: (user, None)))
    monkeypatch.setattr(tickets, "get_ticket", AsyncMock(return_value={"id": 7}))
    monkeypatch.setattr(tickets, "delete_reply", AsyncMock(return_value=True))
    monkeypatch.setattr(call_recordings, "remove_recording_from_ticket", AsyncMock(return_value=True))
    monkeypatch.setattr(admin_routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(admin_routes.tickets_service, "broadcast_ticket_event", AsyncMock())
    app = FastAPI()
    app.include_router(admin_routes.router)
    app.add_middleware(CSRFMiddleware, manager=SimpleNamespace(load_session=AsyncMock(return_value=csrf_session())))
    with TestClient(app, follow_redirects=False) as client:
        yield client, user


def post(client, kind="reply", **kwargs):
    return client.post(f"/admin/tickets/7/history/{kind}/11/delete", data={"_csrf": "test-csrf-token", **kwargs})


@pytest.mark.parametrize("kind", ["reply", "call_recording"])
def test_super_admin_deletes_entry(history_client, kind):
    client, _ = history_client
    response = post(client, kind)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/tickets/7#conversation"
    deletion = tickets.delete_reply if kind == "reply" else call_recordings.remove_recording_from_ticket
    deletion.assert_awaited_once_with(11, 7)
    admin_routes.audit_service.record.assert_awaited_once()
    assert admin_routes.audit_service.record.await_args.kwargs["metadata"] == {"entry_type": kind, "entry_id": 11}
    admin_routes.tickets_service.broadcast_ticket_event.assert_awaited_once_with(action="updated", ticket_id=7)


@pytest.mark.parametrize("kind", ["reply", "call_recording"])
@pytest.mark.parametrize("role", ["technician", "company_admin", "requester"])
def test_other_roles_cannot_delete(history_client, kind, role):
    client, user = history_client
    user.update(is_super_admin=False, is_company_admin=role == "company_admin", is_helpdesk_technician=role == "technician")
    assert post(client, kind).status_code == 403
    tickets.delete_reply.assert_not_awaited()
    call_recordings.remove_recording_from_ticket.assert_not_awaited()


def test_signed_out_user_cannot_delete(history_client, monkeypatch):
    from fastapi.responses import RedirectResponse

    client, _ = history_client
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(None, RedirectResponse("/auth/login", status_code=303))))
    response = post(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    tickets.delete_reply.assert_not_awaited()
    call_recordings.remove_recording_from_ticket.assert_not_awaited()


def test_get_cannot_delete(history_client):
    client, _ = history_client
    assert client.get("/admin/tickets/7/history/reply/11/delete").status_code == 405
    tickets.delete_reply.assert_not_awaited()


@pytest.mark.parametrize("token", [None, "invalid"])
def test_csrf_is_required(history_client, token):
    client, _ = history_client
    response = client.post("/admin/tickets/7/history/reply/11/delete", data={} if token is None else {"_csrf": token})
    assert response.status_code == 403
    tickets.delete_reply.assert_not_awaited()


@pytest.mark.parametrize("kind", ["reply", "call_recording"])
def test_missing_or_cross_ticket_entry_is_404(history_client, kind):
    client, _ = history_client
    deletion = tickets.delete_reply if kind == "reply" else call_recordings.remove_recording_from_ticket
    deletion.return_value = False
    assert post(client, kind).status_code == 404
    admin_routes.audit_service.record.assert_not_awaited()
    admin_routes.tickets_service.broadcast_ticket_event.assert_not_awaited()


def test_missing_ticket_is_404(history_client):
    client, _ = history_client
    tickets.get_ticket.return_value = None
    assert post(client).status_code == 404
    tickets.delete_reply.assert_not_awaited()


def test_unknown_entry_type_is_404(history_client):
    client, _ = history_client
    assert post(client, "invalid").status_code == 404
    tickets.delete_reply.assert_not_awaited()
    call_recordings.remove_recording_from_ticket.assert_not_awaited()


def test_portal_redirect_stays_on_ticket(history_client):
    client, _ = history_client
    assert post(client, view="portal").headers["location"] == "/tickets/7#conversation"


@pytest.mark.parametrize("affected", [0, 1])
def test_reply_delete_is_scoped_and_refreshes_search(monkeypatch, affected):
    execute = AsyncMock(return_value=affected)
    enqueue = AsyncMock()
    monkeypatch.setattr(tickets.db, "execute_rowcount", execute)
    monkeypatch.setattr(tickets, "_rag_outbox_service", lambda: SimpleNamespace(enqueue=enqueue))
    assert asyncio.run(tickets.delete_reply(11, 7)) is bool(affected)
    sql, params = execute.await_args.args
    assert "WHERE id = %s AND ticket_id = %s" in sql
    assert params == (11, 7)
    assert enqueue.await_count == affected


def test_recording_removal_is_scoped_and_preserves_recording(monkeypatch):
    execute = AsyncMock(return_value=1)
    monkeypatch.setattr(call_recordings.db, "execute_rowcount", execute)
    assert asyncio.run(call_recordings.remove_recording_from_ticket(11, 7))
    sql, params = execute.await_args.args
    assert "SET linked_ticket_id = NULL" in sql
    assert "WHERE id = %s AND linked_ticket_id = %s" in sql
    assert params == (11, 7)


@pytest.mark.parametrize("super_admin", [False, True])
@pytest.mark.parametrize("kind", ["reply", "call_recording"])
def test_history_control_visibility(super_admin, kind):
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    rendered = env.get_template("tickets/_history_delete.html").render(
        is_super_admin=super_admin, ticket={"id": 7}, history_entry_type=kind, history_entry_id=11, csrf_token="test-csrf-token",
    )
    assert ('data-history-delete' in rendered) is super_admin


def test_browser_confirm_cancel_and_submit():
    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("System Chromium required")
    from playwright.sync_api import sync_playwright
    env = Environment(loader=FileSystemLoader("app/templates"), autoescape=True)
    control = env.get_template("tickets/_history_delete.html").render(
        is_super_admin=True, ticket={"id": 7}, history_entry_type="reply", history_entry_id=11, csrf_token="test-csrf-token",
    )
    script = Path("app/static/js/ticket_detail.js").read_text()
    # Mount the actual confirmation initializer without unrelated ticket widgets.
    initializer = script[:script.index('  const aiAgentName')] + '\n})();'
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content(control)
        page.add_script_tag(content=initializer)
        page.evaluate("window.submissions = []; document.querySelector('form').addEventListener('submit', e => { if (!e.defaultPrevented) window.submissions.push(Object.fromEntries(new FormData(e.target))); e.preventDefault(); window.wasDisabled = e.target.querySelector('button').disabled; })")
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.get_by_role("button", name="Delete entry").click()
        assert page.get_by_role("button").is_enabled()
        assert page.evaluate("window.wasDisabled") is False
        assert page.evaluate("window.submissions") == []
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button").focus()
        page.keyboard.press("Enter")
        assert page.get_by_role("button").is_disabled()
        assert page.evaluate("window.wasDisabled") is True
        assert page.evaluate("window.submissions") == [{"_csrf": "test-csrf-token", "view": "admin"}]
        browser.close()
