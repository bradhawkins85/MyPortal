"""Chromium regressions using the real rack template, JS, image routes and repository.

Run with RUN_BROWSER_TESTS=1. Requests stay local through Playwright interception;
an in-memory SQLite catalogue records the actual attachment writes.
"""
import os
import shutil
import sqlite3
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from PIL import Image

from app.features.assets import routes
from app.repositories import rack_item_images as repo
from app.services import rack_dashboard, rack_item_images, rack_item_types

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run Chromium checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def mounted(browser, monkeypatch, tmp_path):
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE rack_equipment (id INTEGER PRIMARY KEY, company_id INT, item_type TEXT);
        INSERT INTO rack_equipment VALUES (7, 1, 'switch');
        CREATE TABLE rack_item_images (
            id INTEGER PRIMARY KEY, company_id INT, item_type TEXT, kind TEXT,
            storage_name TEXT, thumbnail_name TEXT, content_type TEXT, size_bytes INT,
            content_hash TEXT, caption TEXT, source_product_id INT, uploaded_by INT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_id, item_type, kind, content_hash));
        CREATE TABLE rack_equipment_images (
            company_id INT, equipment_id INT, image_id INT, UNIQUE(equipment_id, image_id));
    """)

    def query(sql, params=()):
        return connection.execute(sql.replace("%s", "?").replace("INSERT IGNORE", "INSERT OR IGNORE"), params or ())

    async def fetch_one(sql, params=()):
        row = query(sql, params).fetchone()
        return dict(row) if row else None

    async def fetch_all(sql, params=()):
        return [dict(row) for row in query(sql, params).fetchall()]

    async def execute(sql, params=()):
        query(sql, params)
        connection.commit()

    async def insert(sql, params=()):
        cursor = query(sql, params)
        connection.commit()
        return cursor.lastrowid

    for name, function in (("fetch_one", fetch_one), ("fetch_all", fetch_all),
                           ("execute", execute), ("execute_returning_lastrowid", insert)):
        monkeypatch.setattr(repo.db, name, function)
    monkeypatch.setattr(routes, "_rack_image_context", AsyncMock(return_value=({"id": 3}, 1)))
    monkeypatch.setattr(routes, "_infrastructure_write_context", AsyncMock(return_value=({"id": 3}, 1, None)))
    monkeypatch.setattr(routes.audit_service, "record", AsyncMock())
    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(
        flash_redirect=lambda *args: routes.RedirectResponse("/racks?rack=1", status_code=303)))
    monkeypatch.setattr(rack_item_images, "_UPLOADS_ROOT", tmp_path)
    (tmp_path / "shop").mkdir()
    source = BytesIO()
    Image.new("RGB", (400, 300), "blue").save(source, "PNG")
    (tmp_path / "shop/widget.png").write_bytes(source.getvalue())
    monkeypatch.setattr(routes.shop_repo, "get_product_by_id", AsyncMock(return_value={
        "id": 9, "image_url": "/uploads/shop/widget.png"}))
    monkeypatch.setattr(routes.shop_repo, "search_products_for_company_lookup", AsyncMock(return_value=[{
        "id": 9, "sku": "NHU-UX7", "name": "Switch"}]))

    rack = {"id": 1, "name": "Core rack", "unit_count": 8, "numbering_direction": "bottom-up"}
    equipment = [{"id": 7, "company_id": 1, "rack_id": 1, "name": "Switch", "item_type": "switch",
                  "start_unit": 1, "unit_height": 1, "start_lane": 1, "width_lanes": 3,
                  "face": "front", "depth_mode": "half", "port_count": 0, "ports": []}]

    async def place(*args, **kwargs):
        query("INSERT INTO rack_equipment VALUES (8, 1, ?)", (args[11],))
        equipment.append({**equipment[0], "id": 8, "name": "New switch", "start_unit": args[3], "item_type": args[11]})
        connection.commit()
        return 8

    async def update(company_id, equipment_id, name, item_type, *args, **kwargs):
        query("UPDATE rack_equipment SET item_type=? WHERE id=? AND company_id=?", (item_type, equipment_id, company_id))
        connection.commit()

    monkeypatch.setattr(routes.infrastructure_repo, "place_asset", place)
    monkeypatch.setattr(routes.infrastructure_repo, "update_rack_equipment", update)
    environment = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "<!doctype html><html><head><meta charset='utf-8'>"
                    "<link rel='stylesheet' href='/static/css/app.css'>{% block styles %}{% endblock %}</head><body>"
                    "{% block content %}{% endblock %}{% block modals %}{% endblock %}"
                    "{% block scripts %}{% endblock %}</body></html>"}),
        FileSystemLoader(ROOT / "app/templates"),
    ]), autoescape=True)

    def html():
        data = {"racks": [rack], "equipment": equipment, "reservations": []}
        return environment.get_template("infrastructure/racks.html").render(
            **data, workspace=rack_dashboard.build_workspace(data, 1, "graphical"),
            can_edit=True, assets=[], power_outlets=[], csrf_token="test",
            item_type_groups=rack_item_types.grouped(), default_item_type="switch",
            connectors=rack_item_types.CONNECTORS, item_types=rack_item_types.ITEM_TYPES,
            item_type_labels={item.key: item.label for item in rack_item_types.ITEM_TYPES},
            image_path=rack_item_types.image_path, port_catalog=[], static_url=lambda path: path,
        )

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(5000)
    errors, posts, deferred = [], [], []
    state = {"fail_import": False, "fail_attached": False, "defer_library": False}
    page.on("pageerror", lambda error: errors.append(str(error)))

    def intercept(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == "/racks":
            route.fulfill(content_type="text/html", body=html())
        elif path.startswith("/static/"):
            asset = ROOT / "app" / path.lstrip("/")
            if asset.is_file():
                mime = "text/css" if asset.suffix == ".css" else "text/javascript" if asset.suffix == ".js" else "image/svg+xml"
                route.fulfill(content_type=mime, body=asset.read_bytes())
            else:
                route.fulfill(status=204)
        elif path.startswith("/api/"):
            if state["defer_library"] and path.endswith("/library"):
                deferred.append(route)
                return
            if state["fail_import"] and path.endswith("import-shop"):
                route.fulfill(status=422, json={"detail": "That product has no image to import"})
                return
            if state["fail_attached"] and path.endswith("/images"):
                route.fulfill(status=500, json={"detail": "Load failed"})
                return
            if request.method == "POST":
                posts.append((path, request.post_data_buffer))
            response = client.request(request.method, urlsplit(request.url).path + ("?" + urlsplit(request.url).query if urlsplit(request.url).query else ""),
                                      content=request.post_data_buffer,
                                      headers={"content-type": request.headers.get("content-type", "")}, follow_redirects=False)
            if response.status_code == 303:
                assert response.headers["location"] == "/racks?rack=1"
                # Playwright does not intercept a fulfilled response's redirect.
                # Render the redirect destination locally after verifying the response.
                route.fulfill(content_type="text/html", body=html())
                return
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
        else:
            route.fulfill(status=204)

    context.route("**/*", intercept)
    page.goto("http://racks.test/racks?rack=1")
    yield SimpleNamespace(page=page, connection=connection, posts=posts, state=state, deferred=deferred)
    context.close()
    client.close()
    connection.close()
    assert not errors, errors


def _open_edit(mounted, item_id=7):
    mounted.page.locator('button[data-rack-list-open]').click()
    mounted.page.locator(f'#placement-{item_id} [data-edit-equipment]').click()
    mounted.page.wait_for_function("document.querySelector('[name=image_ids]').disabled === false")


def _import(mounted):
    page = mounted.page
    page.locator('[data-image-import-product-search]').fill("NHU-UX7")
    page.wait_for_function("document.querySelector('[data-image-import-product]').value === '9'")
    page.locator('[data-image-import]').click()
    image = page.locator('[data-image-kind="product"] [data-image-attached] img')
    image.wait_for()
    page.wait_for_function("document.querySelector('[data-image-attached] img, [data-image-kind=product] [data-image-attached] img')?.naturalWidth > 0")
    page.wait_for_function("!document.querySelector('[data-image-import]').disabled")
    assert "[object HTMLDivElement]" not in page.locator('[data-rack-images]').inner_text()


def _save(mounted):
    with mounted.page.expect_navigation(wait_until="domcontentloaded"):
        mounted.page.locator('[data-submit-label]').click()
    assert mounted.page.locator('[data-rack-workspace]').count() == 1, (mounted.page.url, mounted.page.locator('body').inner_text())


def test_import_preview_save_reopen_and_detach(mounted):
    _open_edit(mounted)
    _import(mounted)
    # Selection is staged until the same Save changes that saves the rack item.
    assert mounted.connection.execute("SELECT count(*) FROM rack_equipment_images").fetchone()[0] == 0
    _save(mounted)
    assert mounted.connection.execute("SELECT equipment_id FROM rack_equipment_images").fetchone()[0] == 7
    _open_edit(mounted)
    assert mounted.page.locator('[data-image-kind="product"] [data-image-attached] img').count() == 1
    mounted.page.locator('[data-image-detach]').click()
    _save(mounted)
    assert mounted.connection.execute("SELECT count(*) FROM rack_equipment_images").fetchone()[0] == 0
    _open_edit(mounted)
    assert mounted.page.locator('[data-image-kind="product"] [data-image-library] img').count() == 1


def test_new_item_import_saves_attachment_and_reuses_image(mounted):
    page = mounted.page
    page.locator('[data-place-open][data-unit="2"][data-lane="1"]').first.click()
    page.wait_for_function("document.querySelector('[name=image_ids]').disabled === false")
    _import(mounted)
    _save(mounted)
    assert mounted.connection.execute("SELECT equipment_id FROM rack_equipment_images").fetchone()[0] == 8
    _open_edit(mounted, 7)
    _import(mounted)
    _save(mounted)
    assert mounted.connection.execute("SELECT count(*) FROM rack_item_images").fetchone()[0] == 1
    assert mounted.connection.execute("SELECT count(DISTINCT image_id) FROM rack_equipment_images").fetchone()[0] == 1
    assert mounted.connection.execute("SELECT count(*) FROM rack_equipment_images").fetchone()[0] == 2


def test_cancel_discards_selection_and_failed_import_shows_error(mounted):
    _open_edit(mounted)
    _import(mounted)
    mounted.page.locator('#rack-place-dialog [data-dialog-close]').first.click()
    _open_edit(mounted)
    assert mounted.page.locator('[data-image-attached] img').count() == 0
    assert mounted.page.locator('[data-image-library] img').count() == 1
    mounted.state["fail_import"] = True
    mounted.page.locator('[data-image-import-product-search]').fill("NHU-UX7")
    mounted.page.wait_for_function("document.querySelector('[data-image-import-product]').value === '9'")
    mounted.page.locator('[data-image-import]').click()
    mounted.page.locator('[data-image-status]').filter(has_text="That product has no image").wait_for()
    assert mounted.connection.execute("SELECT count(*) FROM rack_equipment_images").fetchone()[0] == 0


def test_multiple_images_render_as_cards_and_save_together(mounted):
    _open_edit(mounted)
    _import(mounted)
    source = BytesIO()
    Image.new("RGB", (400, 300), "red").save(source, "PNG")
    mounted.page.locator('[data-image-kind="product"] [data-image-upload]').set_input_files({
        "name": "second.png", "mimeType": "image/png", "buffer": source.getvalue()})
    mounted.page.wait_for_function("document.querySelectorAll('[data-image-kind=product] [data-image-attached] img').length === 2")
    grid = mounted.page.locator('[data-image-kind="product"] [data-image-attached]')
    assert grid.locator('> .rack-image').count() == 2
    assert "[object" not in grid.inner_text()
    _save(mounted)
    assert mounted.connection.execute("SELECT count(*) FROM rack_equipment_images").fetchone()[0] == 2
    _open_edit(mounted)
    assert mounted.page.locator('[data-image-kind="product"] [data-image-attached] img').count() == 2


def test_failed_gallery_load_does_not_clear_saved_attachments(mounted):
    _open_edit(mounted)
    _import(mounted)
    _save(mounted)
    mounted.state["fail_attached"] = True
    mounted.page.locator('button[data-rack-list-open]').click()
    mounted.page.locator('#placement-7 [data-edit-equipment]').click()
    mounted.page.locator('[data-image-status]').filter(has_text="Could not load images").wait_for()
    assert mounted.page.locator('[name=image_ids]').is_disabled()
    _save(mounted)
    assert mounted.connection.execute("SELECT count(*) FROM rack_equipment_images").fetchone()[0] == 1


def test_save_waits_for_pending_image_requests(mounted):
    mounted.state["defer_library"] = True
    mounted.page.locator('button[data-rack-list-open]').click()
    mounted.page.locator('#placement-7 [data-edit-equipment]').click()
    mounted.page.locator('[data-submit-label]').click()
    mounted.page.locator('[data-image-status]').filter(has_text="Wait for images to finish loading before saving").wait_for()
    assert not mounted.posts
    assert len(mounted.deferred) == 1
    mounted.deferred.pop().fulfill(json={"device": [], "product": []})
    mounted.page.wait_for_function("document.querySelector('[name=image_ids]').disabled === false")
    _save(mounted)
