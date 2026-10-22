"""Sensitive provisioning controls, exercised without a running portal.

Run with RUN_BROWSER_TESTS=1 and system Chromium, as for the tray download
help browser tests. All network requests are intercepted locally.
"""

from pathlib import Path
import os
import shutil
from types import SimpleNamespace
from urllib.parse import urlsplit

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape
import pytest
from starlette.datastructures import URL


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PASSWORD = "generated-package-password"


def _environment():
    return Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": "{% block content %}{% endblock %}"}),
            FileSystemLoader(ROOT / "app/templates"),
        ]),
        autoescape=select_autoescape(),
    )


def _admin_html():
    body = _environment().get_template("admin/tray/_section_deployment_urls.html").render(
        tray={"links": {
            "release_tag": "v1.2.3", "companies": [{"id": 1, "name": "Acme"}],
            "expiry_choices": [(7, "7 days")], "default_expiry": 7,
            "show_revoked": False, "links": [{
                "id": 9, "company_name": "Acme", "label": "New PCs",
                "url": "https://portal.test/deploy/private", "status": "active",
                "created_at": "2026-10-22", "download_count": 0,
                "provisioning_enabled": True,
                "build": {"status": "ready", "release_tag": "v1.2.3"},
            }],
        }},
        csrf_token="test-csrf",
        request=SimpleNamespace(url=URL("https://portal.test/admin/tray")),
    )
    return (
        '<!doctype html><html><head><meta name="viewport" '
        'content="width=device-width, initial-scale=1">'
        '<link rel="stylesheet" href="/static/css/app.css">'
        '<script defer src="/static/js/tray_deployment_links.js"></script>'
        f"</head><body>{body}</body></html>"
    )


def test_public_download_only_offers_ready_provisioning_packages():
    template = _environment().get_template("tray/deploy.html")
    context = {
        "app_name": "MyPortal", "state": "open", "windows_ready": True,
        "macos_ready": False, "provisioning_enabled": True,
        "deploy_base": "https://portal.test/deploy/private", "static_url": lambda path: path,
    }
    ready = template.render(**context, ppkg_ready=True)
    pending = template.render(**context, ppkg_ready=False)
    assert '/windows.ppkg"' in ready
    assert '/windows.ppkg"' not in pending
    assert "Windows key five times" in ready
    assert "ProvisioningAdmin" in ready
    assert "directory join settings selected by your IT team" in ready
    assert "domain controller and DNS" in ready
    assert "still being prepared" in pending
    assert PACKAGE_PASSWORD not in ready
    assert "provisioning-password" not in ready


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run optional browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required for optional browser checks")
    with api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            executable_path=executable,
            args=["--no-sandbox", "--disable-crash-reporter", "--disable-breakpad"],
        )
        yield instance
        instance.close()


@pytest.fixture
def mount(browser):
    contexts = []
    errors = []

    def create(*, width=1024, password_status=200):
        context = browser.new_context(viewport={"width": width, "height": 900})
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        password_requests = []

        def intercept(route):
            path = urlsplit(route.request.url).path
            if path == "/admin/tray":
                route.fulfill(status=200, content_type="text/html", body=_admin_html())
            elif path.endswith("/provisioning-password"):
                password_requests.append(route.request)
                route.fulfill(
                    status=password_status, content_type="application/json",
                    json={"password": PACKAGE_PASSWORD} if password_status == 200 else {},
                    headers={"Cache-Control": "no-store"},
                )
            elif path.startswith("/static/"):
                asset = ROOT / "app" / path.lstrip("/")
                route.fulfill(
                    status=200, body=asset.read_bytes(),
                    content_type="text/javascript" if path.endswith(".js") else "text/css",
                )
            else:
                route.abort()

        context.route("**/*", intercept)
        page.goto("https://portal.test/admin/tray")
        return page, password_requests

    yield create
    for context in contexts:
        context.close()
    assert not errors, errors


def test_provisioning_is_optional_and_disabling_clears_credentials(mount):
    page, _requests = mount()
    fields = page.locator("[data-provisioning-fields]")
    assert fields.is_hidden()
    assert page.locator("[data-provisioning-admin-password]").is_disabled()
    page.get_by_text("Provision new Windows computers", exact=False).first.click()
    page.locator("[data-provisioning-enabled]").check()
    assert fields.is_visible()
    password = page.locator("[data-provisioning-admin-password]")
    assert password.evaluate("input => input.required")
    password.fill("Bootstrap-secret-123")
    page.locator("#deploy-provisioning-wifi-password").fill("wifi-secret")
    page.locator("[data-provisioning-enabled]").uncheck()
    assert password.is_disabled()
    assert password.input_value() == ""
    assert page.locator("#deploy-provisioning-wifi-password").input_value() == ""


def test_directory_join_switching_requires_only_current_fields_and_clears_secrets(mount):
    page, _requests = mount()
    page.get_by_text("Provision new Windows computers", exact=False).first.click()
    page.locator("[data-provisioning-enabled]").check()
    mode = page.locator("[data-provisioning-join-mode]")
    entra_token = page.locator("#deploy-provisioning-entra-token")
    ad_domain = page.locator("#deploy-provisioning-ad-domain")
    ad_username = page.locator("#deploy-provisioning-ad-username")
    ad_password = page.locator("#deploy-provisioning-ad-password")
    assert mode.input_value() == "none"
    assert entra_token.is_disabled()
    assert ad_password.is_disabled()
    assert not entra_token.evaluate("input => input.required")
    assert not ad_password.evaluate("input => input.required")

    mode.select_option("entra")
    assert entra_token.is_visible()
    assert entra_token.is_enabled()
    assert entra_token.evaluate("input => input.required")
    assert ad_password.is_hidden()
    assert page.locator("#deploy-provisioning-entra-authority").input_value() == (
        "https://login.microsoftonline.com/common"
    )
    entra_token.fill("sensitive-bulk-token")

    mode.select_option("active_directory")
    assert entra_token.is_disabled()
    assert entra_token.input_value() == ""
    assert not entra_token.evaluate("input => input.required")
    for field in (ad_domain, ad_username, ad_password):
        assert field.is_visible()
        assert field.evaluate("input => input.required")
    assert not page.locator("#deploy-provisioning-ad-ou").evaluate("input => input.required")
    ad_domain.fill("corp.example.com")
    ad_username.fill("CORP\\provisioning")
    ad_password.fill("sensitive-domain-password")
    submitted = page.locator('form[action="/admin/tray/deployment-links"]').evaluate(
        "form => Object.fromEntries(new FormData(form))"
    )
    assert submitted["provisioning_join_mode"] == "active_directory"
    assert submitted["provisioning_ad_password"] == "sensitive-domain-password"
    assert "provisioning_entra_bulk_token" not in submitted
    assert "provisioning_entra_authority" not in submitted

    mode.select_option("none")
    assert ad_password.input_value() == ""
    assert ad_password.is_disabled()
    assert not ad_password.evaluate("input => input.required")
    mode.select_option("entra")
    entra_token.fill("replacement-bulk-token")
    page.locator("[data-provisioning-enabled]").uncheck()
    assert entra_token.input_value() == ""
    assert entra_token.is_disabled()
    assert not entra_token.evaluate("input => input.required")


@pytest.mark.parametrize("width", [320, 768, 1024, 1440])
def test_package_password_is_requested_deliberately_and_cleared_on_hide(mount, width):
    page, requests = mount(width=width)
    assert not requests
    assert PACKAGE_PASSWORD not in page.content()
    details = page.locator("[data-provisioning-package]")
    details.locator("summary").click()
    button = details.locator("[data-provisioning-password-url]")
    button.click()
    value = details.locator("[data-provisioning-password-value]")
    page.wait_for_function(
        "document.querySelector('[data-provisioning-password-value]').value === 'generated-package-password'"
    )
    assert len(requests) == 1
    assert value.is_visible()
    assert button.get_attribute("aria-expanded") == "true"
    assert "Copy password" in details.inner_text()
    button.click()
    assert value.input_value() == ""
    assert value.is_hidden()
    assert button.get_attribute("aria-expanded") == "false"
    button.click()
    page.wait_for_function(
        "document.querySelector('[data-provisioning-password-value]').value !== ''"
    )
    details.locator("summary").click()
    page.wait_for_function(
        "document.querySelector('[data-provisioning-password-value]').value === ''"
    )


def test_password_request_failure_leaves_no_secret_visible(mount):
    page, _requests = mount(password_status=403)
    details = page.locator("[data-provisioning-package]")
    details.locator("summary").click()
    button = details.locator("[data-provisioning-password-url]")
    button.click()
    status = details.locator("[data-provisioning-password-status]")
    status.wait_for(state="visible")
    page.wait_for_function(
        "document.querySelector('[data-provisioning-password-status]').textContent.includes('could not be loaded')"
    )
    assert details.locator("[data-provisioning-password-value]").input_value() == ""
    assert button.is_enabled()
    assert button.get_attribute("aria-expanded") == "false"
