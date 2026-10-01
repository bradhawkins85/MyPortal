"""Application register: form parsing, access rules, storage and templates."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from starlette.datastructures import FormData
from starlette.requests import Request

from app.features.applications import routes
from app.repositories import applications as repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.menu_permissions import normalize_menu_permissions

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _request(path: str = "/applications/save") -> Request:
    return Request({"type": "http", "method": "POST", "path": path, "headers": []})


# ---------------------------------------------------------------------------
# Form parsing and validation
# ---------------------------------------------------------------------------


def test_form_maps_every_field():
    payload = routes._form_payload(FormData([
        ("name", "  Xero  "), ("type_id", "4"), ("type_name", ""), ("version", "2026.1"),
        ("business_impact", "Payroll stops"), ("importance", "critical"),
        ("champion_staff_id", "9"), ("champion_name", ""), ("product_key", " ABCD-1234 "),
        ("notes", "Renews yearly"), ("knowledge_base_article_ids", "3"), ("knowledge_base_article_ids", "5"),
        ("external_links", "Install guide | https://support.example.com/kb/1\n\nhttps://example.com/kb/2\n"),
    ]))

    assert payload.name == "Xero"
    assert payload.type_id == 4 and payload.type_name is None
    assert payload.importance == "critical"
    assert payload.champion_staff_id == 9
    assert payload.product_key == "ABCD-1234"
    assert payload.clear_product_key is False
    assert payload.knowledge_base_article_ids == [3, 5]
    assert [link.model_dump() for link in payload.external_links] == [
        {"title": "Install guide", "url": "https://support.example.com/kb/1"},
        {"title": None, "url": "https://example.com/kb/2"},
    ]


def test_external_link_titles_may_contain_pipes():
    assert routes.parse_external_links("A | B | https://example.com") == [
        {"title": "A | B", "url": "https://example.com"},
    ]


@pytest.mark.parametrize("line", ["javascript:alert(1)", "Guide | data:text/html,hi", "ftp://example.com", "not a url"])
def test_non_web_kb_links_are_rejected(line):
    with pytest.raises(HTTPException) as exc:
        routes._form_payload(FormData([("name", "App"), ("external_links", line)]))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("field,value", [("importance", "urgent"), ("name", "   "), ("type_id", "other")])
def test_invalid_fields_are_rejected(field, value):
    values = {"name": "App", "importance": "low", field: value}
    with pytest.raises(HTTPException) as exc:
        routes._form_payload(FormData(list(values.items())))
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Save rules
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_new_type_name_creates_or_reuses_a_type(monkeypatch):
    get_or_create = AsyncMock(return_value=12)
    monkeypatch.setattr(repo, "get_or_create_type", get_or_create)
    payload = routes.ApplicationInput(name="App", type_id=3, type_name="CRM")

    values = await routes._resolve_values(7, payload)

    assert values["type_id"] == 12
    get_or_create.assert_awaited_once_with(7, "CRM")


@pytest.mark.anyio
async def test_types_and_champions_from_other_companies_are_rejected(monkeypatch):
    monkeypatch.setattr(repo, "get_type", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await routes._resolve_values(7, routes.ApplicationInput(name="App", type_id=99))
    assert exc.value.status_code == 422

    monkeypatch.setattr(repo, "staff_belongs_to_company", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as exc:
        await routes._resolve_values(7, routes.ApplicationInput(name="App", champion_staff_id=5))
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_selected_staff_champion_replaces_free_text_name(monkeypatch):
    monkeypatch.setattr(repo, "staff_belongs_to_company", AsyncMock(return_value=True))
    values = await routes._resolve_values(
        7, routes.ApplicationInput(name="App", champion_staff_id=5, champion_name="Someone else"),
    )
    assert values["champion_staff_id"] == 5 and values["champion_name"] is None


@pytest.mark.anyio
async def test_articles_must_be_accessible_and_hidden_links_are_kept(monkeypatch):
    monkeypatch.setattr(routes, "_accessible_articles", AsyncMock(return_value=[{"id": 1}, {"id": 2}]))
    with pytest.raises(HTTPException) as exc:
        await routes._article_ids_to_store({"id": 3}, 7, None, [1, 3])
    assert exc.value.status_code == 422

    monkeypatch.setattr(repo, "get_links", AsyncMock(return_value={
        "articles": [{"id": 2}, {"id": 8}], "external": [],
    }))
    assert await routes._article_ids_to_store({"id": 3}, 7, 42, [1]) == [1, 8]


@pytest.mark.anyio
async def test_save_encrypts_product_key_and_keeps_it_out_of_audit(monkeypatch):
    monkeypatch.setattr(routes, "_accessible_articles", AsyncMock(return_value=[]))
    create = AsyncMock(return_value=42)
    monkeypatch.setattr(repo, "create_application", create)
    replace = AsyncMock()
    monkeypatch.setattr(repo, "replace_links", replace)
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)

    payload = routes.ApplicationInput(name="App", product_key="SECRET-KEY",
                                      external_links=[{"url": "https://example.com/kb"}])
    assert await routes._save(_request(), {"id": 3}, 7, payload, None) == 42

    stored = create.await_args.args[1]["product_key_encrypted"]
    assert stored != "SECRET-KEY" and decrypt_secret(stored) == "SECRET-KEY"
    replace.assert_awaited_once_with(7, 42, [], [{"title": None, "url": "https://example.com/kb"}])
    assert "SECRET-KEY" not in repr(audit.await_args)
    assert audit.await_args.kwargs["after"]["product_key_changed"] is True


@pytest.mark.anyio
async def test_editing_without_a_new_key_keeps_the_stored_key(monkeypatch):
    monkeypatch.setattr(routes, "_accessible_articles", AsyncMock(return_value=[]))
    monkeypatch.setattr(repo, "get_links", AsyncMock(return_value={"articles": [], "external": []}))
    update = AsyncMock()
    monkeypatch.setattr(repo, "update_application", update)
    monkeypatch.setattr(repo, "replace_links", AsyncMock())
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())

    await routes._save(_request(), {"id": 3}, 7, routes.ApplicationInput(name="App"), 42)

    assert update.await_args.kwargs == {"product_key": None, "clear_product_key": False}


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def test_permission_defaults_to_no_access_and_follows_assets_for_legacy_roles():
    assert normalize_menu_permissions(None)["menu.applications"] == "none"
    assert normalize_menu_permissions({"menu.assets": "write"})["menu.applications"] == "write"


@pytest.mark.anyio
async def test_read_only_users_cannot_write(monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module, "_membership_menu_can", lambda *args, write=False, **kw: not write)
    context = ({"id": 3}, 7, {})
    with pytest.raises(HTTPException) as exc:
        routes.require_write(context)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_product_key_reveal_is_audited(monkeypatch):
    monkeypatch.setattr(repo, "get_application", AsyncMock(return_value={"id": 42}))
    monkeypatch.setattr(repo, "get_product_key_ciphertext", AsyncMock(return_value=encrypt_secret("KEY-1")))
    audit = AsyncMock()
    monkeypatch.setattr(routes.audit_service, "record", audit)

    result = await routes.reveal_product_key(42, _request("/api/applications/42/product-key"), ({"id": 3}, 7, {}))

    assert result == {"product_key": "KEY-1"}
    assert audit.await_args.kwargs["action"] == "application.product_key.view"


@pytest.mark.anyio
async def test_delete_is_company_scoped(monkeypatch):
    monkeypatch.setattr(routes, "_web_context", AsyncMock(return_value=({"id": 3}, 7, {}, None)))
    monkeypatch.setattr(repo, "get_application", AsyncMock(return_value=None))
    delete = AsyncMock()
    monkeypatch.setattr(repo, "delete_application", delete)

    with pytest.raises(HTTPException) as exc:
        await routes.application_delete(_request("/applications/42/delete"), 42)

    assert exc.value.status_code == 404
    delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Storage against the real migration (SQLite)
# ---------------------------------------------------------------------------


class _SqliteDb:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _sql(sql):
        return sql.replace("%s", "?")

    async def fetch_one(self, sql, params=None):
        cursor = await self.conn.execute(self._sql(sql), params or ())
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql, params=None):
        cursor = await self.conn.execute(self._sql(sql), params or ())
        return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql, params=None):
        await self.conn.execute(self._sql(sql), params or ())

    async def execute_rowcount(self, sql, params=None):
        return (await self.conn.execute(self._sql(sql), params or ())).rowcount

    async def execute_returning_lastrowid(self, sql, params=None):
        return (await self.conn.execute(self._sql(sql), params or ())).lastrowid


@pytest.fixture
async def sqlite_db(monkeypatch):
    from app.core.database import Database

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY);
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        CREATE TABLE staff (id INTEGER PRIMARY KEY, company_id INT, first_name TEXT, last_name TEXT,
                            email TEXT, enabled INT DEFAULT 1);
        CREATE TABLE knowledge_base_articles (id INTEGER PRIMARY KEY, title TEXT, slug TEXT);
        INSERT INTO companies (id) VALUES (1), (2);
        INSERT INTO users (id) VALUES (3);
        INSERT INTO staff VALUES (5, 1, 'Ada', 'Lovelace', 'ada@example.com', 1), (6, 2, 'Bob', 'Other', 'b@x', 1);
        INSERT INTO knowledge_base_articles VALUES (10, 'Restore Xero', 'restore-xero');
    """)
    migration = (ROOT / "migrations/450_applications.sql").read_text()
    await conn.executescript(Database()._adapt_sql_for_sqlite(migration))
    fake = _SqliteDb(conn)
    monkeypatch.setattr(repo, "db", fake)
    yield fake
    await conn.close()


@pytest.mark.anyio
async def test_repository_round_trip(sqlite_db):
    type_id = await repo.get_or_create_type(1, "Accounting")
    assert await repo.get_or_create_type(1, "accounting") == type_id
    other_type = await repo.get_or_create_type(2, "Accounting")
    assert other_type != type_id

    values = {"name": "Xero", "type_id": type_id, "version": "2026", "business_impact": "Payroll",
              "importance": "critical", "champion_staff_id": 5, "champion_name": None,
              "product_key_encrypted": "cipher", "notes": None}
    app_id = await repo.create_application(1, values, 3)
    await repo.replace_links(1, app_id, [10], [{"title": "Vendor", "url": "https://example.com"}])

    app = await repo.get_application(1, app_id)
    assert app["type_name"] == "Accounting"
    assert app["champion_display"] == "Ada Lovelace"
    assert app["has_product_key"] is True
    assert await repo.get_application(2, app_id) is None
    assert [row["name"] for row in await repo.list_applications(1)] == ["Xero"]
    links = await repo.get_links(1, app_id)
    assert [a["slug"] for a in links["articles"]] == ["restore-xero"]
    assert links["external"][0]["url"] == "https://example.com"
    assert (await repo.get_links(2, app_id)) == {"articles": [], "external": []}
    with pytest.raises(ValueError):
        await repo.replace_links(2, app_id, [], [])

    await repo.update_application(1, app_id, {**values, "version": "2027"}, product_key=None)
    assert await repo.get_product_key_ciphertext(1, app_id) == "cipher"
    await repo.update_application(1, app_id, values, product_key=None, clear_product_key=True)
    assert (await repo.get_application(1, app_id))["has_product_key"] is False

    assert (await repo.list_types(1))[0]["application_count"] == 1
    assert [c["id"] for c in await repo.list_champion_choices(1)] == [5]
    assert await repo.staff_belongs_to_company(1, 6) is False

    assert await repo.delete_application(2, app_id) is False
    assert await repo.delete_application(1, app_id) is True
    assert await repo.list_applications(1) == []


# ---------------------------------------------------------------------------
# Templates and menu
# ---------------------------------------------------------------------------


def _environment():
    return Environment(
        loader=ChoiceLoader([
            DictLoader({
                "base.html": "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}",
                "macros/header.html": "{% macro page_header_actions(actions) %}{% endmacro %}",
                "macros/tables.html": (
                    "{% macro data_table(rows, columns, aria_label, table_id='') %}{{ caller() }}{% endmacro %}"
                    "{% macro table_toolbar(table_id) %}{% endmacro %}"
                    "{% macro empty_state(title, message) %}{{ title }}{% endmacro %}"
                ),
                "partials/csrf.html": "",
            }),
            FileSystemLoader(ROOT / "app" / "templates"),
        ]),
        autoescape=True,
    )


def test_detail_never_renders_the_product_key_and_escapes_links():
    rendered = _environment().get_template("applications/detail.html").render(
        request={"query_params": {}},
        application={"id": 42, "name": "Xero", "type_name": "Accounting", "version": "2026",
                     "importance": "critical", "champion_display": "Ada Lovelace",
                     "champion_email": "ada@example.com", "has_product_key": True,
                     "business_impact": "Payroll stops", "notes": None},
        links={"articles": [{"id": 10, "title": "Restore", "slug": "restore"}],
               "external": [{"title": '<b>Vendor</b>', "url": "https://example.com/kb"}]},
        importance_labels=dict(repo.IMPORTANCE_LEVELS), can_write=True,
    )
    assert "/api/applications/42/product-key" in rendered
    assert "Critical" in rendered and "Ada Lovelace" in rendered
    assert "&lt;b&gt;Vendor&lt;/b&gt;" in rendered
    assert 'href="/knowledge-base/articles/restore"' in rendered


def test_form_offers_searchable_types_and_existing_links():
    rendered = _environment().get_template("applications/form.html").render(
        request={"query_params": {}}, title="Edit application",
        application={"id": 42, "name": "Xero", "type_id": 4, "importance": "high", "has_product_key": True,
                     "champion_staff_id": None, "champion_name": "Vendor rep"},
        links={"articles": [], "external": [{"title": "Guide", "url": "https://example.com"}]},
        types=[{"id": 4, "name": "Accounting"}], staff=[], articles=[],
        importance_levels=repo.IMPORTANCE_LEVELS,
    )
    assert 'name="type_id" data-searchable="on"' in rendered
    assert '<option value="4" selected>Accounting</option>' in rendered
    assert "Guide | https://example.com" in rendered
    assert 'name="clear_product_key"' in rendered
    assert 'value="high" selected' in rendered


def test_applications_menu_sits_in_assets_and_network():
    from app.repositories.sidebar_preferences import build_default_sidebar_preferences

    groups = {g["id"]: g["items"] for g in build_default_sidebar_preferences()["groups"]}
    assert "/applications" in groups["__group__:default-infrastructure"]
    assert 'href="/applications"' in (ROOT / "app/templates/base.html").read_text()


def test_saved_layouts_receive_new_default_items_in_their_group():
    from app.repositories.sidebar_preferences import resolve_stored_preferences

    resolved = resolve_stored_preferences({
        "version": 1, "order": ["/", "__group__:default-infrastructure", "/websites"],
        "groups": [{"id": "__group__:default-infrastructure", "label": "Kit", "icon": "server",
                    "items": ["/devices", "/assets"]}],
    })
    items = resolved["groups"][0]["items"]
    # Placed after its default predecessor; items the user moved out stay put.
    assert items.index("/applications") == items.index("/assets") + 1
    assert "/websites" not in items
