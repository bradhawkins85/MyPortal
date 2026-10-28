"""Access control and value-free schema checks for the browser-only tool."""
from pathlib import Path

import pytest
from fastapi.responses import RedirectResponse
from fastapi import HTTPException
from starlette.requests import Request

import app.main as main
from app.services import env_catalog
from app.services.component_availability import ComponentAvailability


def page_request():
    return Request({"type": "http", "method": "GET", "scheme": "https", "path": "/admin/env-checker", "root_path": "", "query_string": b"", "headers": [], "server": ("portal.test", 443), "app": main.app, "router": main.app.router})


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch):
    async def base_context(request, user, *, extra=None):
        context = await main._build_public_context(request, extra=extra)
        context.update(current_user=user, user=user, is_super_admin=bool(user.get("is_super_admin")), has_admin_access=True, menu_access={}, current_path=request.url.path)
        return context

    monkeypatch.setattr(main, "_build_base_context", base_context)
    async def modules():
        return [{"slug": "ollama", "name": "Ollama", "enabled": False, "settings": {"api_key": "must-not-render-secret"}}]

    monkeypatch.setattr(main.modules_service, "list_modules", modules)


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.asyncio
async def test_page_rejects_non_super_admin(monkeypatch, authenticated):
    async def user(request):
        if authenticated:
            return {"id": 2, "is_super_admin": False}, None
        return None, RedirectResponse("/login", status_code=303)

    monkeypatch.setattr(main, "_require_authenticated_user", user)
    if authenticated:
        with pytest.raises(HTTPException) as error:
            await main.admin_env_checker_page(page_request())
        assert error.value.status_code == 403
    else:
        response = await main.admin_env_checker_page(page_request())
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_super_admin_page_contains_only_schema_no_values(monkeypatch):
    async def admin(request):
        return {"id": 1, "is_super_admin": True, "email": "admin@example.com"}, None

    monkeypatch.setattr(main, "_require_super_admin_page", admin)
    response = await main.admin_env_checker_page(page_request())
    body = response.body.decode()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="env-input"' in body
    assert 'id="env-output"' in body
    assert "must-not-render-secret" not in body
    assert "OLLAMA_MODEL" in body
    assert "env-formatter.js" in body
    assert 'name="env' not in body


@pytest.mark.parametrize("super_admin", [False, True])
@pytest.mark.asyncio
async def test_menu_entry_is_only_visible_to_super_admin(super_admin):
    user = {"id": 1, "is_super_admin": super_admin, "email": "admin@example.com"}
    response = await main._render_template("admin/env_checker.html", page_request(), user, extra={"env_catalog": []})
    assert ('href="/admin/env-checker"' in response.body.decode()) is super_admin


def test_catalog_uses_known_modules_even_when_deployment_hides_them(monkeypatch):
    policy = ComponentAvailability(disabled_modules=frozenset({"whisperx"}))
    monkeypatch.setattr(env_catalog, "get_component_availability", lambda: policy)
    catalog = {item["key"]: item for item in env_catalog.build_environment_catalog([], [])}
    assert catalog["WHISPERX_API_KEY"]["group"] == "Module: Speech Transcription"
    assert catalog["WHISPERX_API_KEY"]["active"] is False
    assert catalog["WHISPERCPP_BASE_URL"]["group"] == "Module: Speech Transcription"
    assert catalog["TRANSCRIPTION_ENGINE"]["group"] == "Module: Speech Transcription"
    assert catalog["APP_NAME"]["active"] is True
    assert catalog["SESSION_SECRET"]["aliases"] == ("SESSION_SECRET", "SECRET_KEY")
    assert catalog["SESSION_SECRET"]["group"] == catalog["SECRET_KEY"]["group"]
    assert "XERO_TOKEN_KEEPALIVE_INTERVAL_SECONDS" in catalog
    assert all(set(item) == {"key", "aliases", "group", "active"} for item in catalog.values())


def test_catalog_respects_module_state_and_pack_availability(monkeypatch):
    monkeypatch.setattr(env_catalog, "get_component_availability", lambda: ComponentAvailability(disabled_feature_packs=frozenset({"rag_index"})))
    rows = env_catalog.build_environment_catalog([
        {"slug": "ollama", "name": "Ollama", "enabled": True},
    ], [{"slug": "ollama"}, {"slug": "rag_index"}])
    catalog = {item["key"]: item for item in rows}
    assert catalog["OLLAMA_MODEL"]["active"] is True
    assert catalog["RAG_MAX_CONTEXT_TOKENS"]["active"] is False


def test_browser_keeps_contents_local_and_invalidates_old_output(monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright

    chromium = shutil.which("chromium")
    if not chromium:
        pytest.skip("System Chromium is required for browser verification")

    async def admin(request):
        return {"id": 1, "is_super_admin": True, "email": "admin@example.com"}, None

    monkeypatch.setattr(main, "_require_super_admin_page", admin)
    import asyncio

    html = asyncio.run(main.admin_env_checker_page(page_request())).body.decode()
    static_root = Path(__file__).resolve().parents[1] / "app" / "static"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        requests = []
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            path = urlsplit(route.request.url).path
            requests.append((route.request.method, route.request.url, route.request.post_data))
            if path == "/admin/env-checker":
                route.fulfill(body=html, content_type="text/html")
            elif path.startswith("/static/"):
                asset = (static_root / path.removeprefix("/static/")).resolve()
                if asset.is_relative_to(static_root) and asset.is_file():
                    route.fulfill(path=str(asset))
                else:
                    route.fulfill(status=404)
            else:
                route.fulfill(status=200, body="{}", content_type="application/json")

        context.route("**/*", serve)
        page.goto("https://portal.test/admin/env-checker")
        page.wait_for_load_state("networkidle")
        assert page.locator("#env-results").is_hidden()
        for width in (320, 768, 1024, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        local_before = page.evaluate("JSON.stringify(localStorage)")
        session_before = page.evaluate("JSON.stringify(sessionStorage)")
        page.locator("#env-input").fill('OLD_PRIVATE_KEY=secret-never-transmit\nOLLAMA_MODEL=llama3\nAPP_NAME=Portal')
        page.get_by_role("button", name="Check and format").click()
        output = page.locator("#env-output").input_value()
        assert output.index("APP_NAME=Portal") < output.index("OLLAMA_MODEL=llama3") < output.index("OLD_PRIVATE_KEY=")
        assert "secret-never-transmit" in output
        assert all("secret-never-transmit" not in str(request) for request in requests)
        assert page.evaluate("JSON.stringify(localStorage)") == local_before
        assert page.evaluate("JSON.stringify(sessionStorage)") == session_before
        page.get_by_role("button", name="Copy formatted .env").click()
        assert page.evaluate("navigator.clipboard.readText()") == output
        with page.expect_download() as downloaded:
            page.get_by_role("button", name="Download .env").click()
        assert downloaded.value.suggested_filename == "formatted.env"
        assert Path(downloaded.value.path()).read_text() == output
        page.locator("#env-input").fill("APP_NAME=Changed")
        assert page.locator("#env-output").input_value() == ""
        assert page.locator("#env-results").is_hidden()
        page.get_by_role("button", name="Clear contents").click()
        assert page.locator("#env-input").input_value() == ""
        page.locator("#env-input").fill("APP_NAME=Private")
        page.get_by_role("button", name="Check and format").click()
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        assert page.locator("#env-input").input_value() == ""
        assert page.locator("#env-output").input_value() == ""
        assert errors == []
        browser.close()
