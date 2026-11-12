"""Exercise styled email HTML through the actual editor, preview and save UI."""

import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader
import pytest


ROOT = Path(__file__).resolve().parents[1]
BANNER = (ROOT / "tests/fixtures/myportal_email_banner.html").read_text()


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        instance = playwright.chromium.launch(
            executable_path=executable, args=["--no-sandbox"]
        )
        yield instance
        instance.close()


def _markup(record):
    templates = ROOT / "app/templates"
    env = Environment(loader=FileSystemLoader(templates), autoescape=True)
    source = (templates / "admin/message_template_form.html").read_text()
    source = source.split("{% block content %}", 1)[1].split("{% endblock %}", 1)[0]
    content = env.from_string(source).render(
        template=record,
        default_content_type="text/html",
        content_type_options=[
            {"value": "text/html", "label": "HTML"},
            {"value": "text/plain", "label": "Plain text"},
        ],
        system_uses={},
        common_variables=[],
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="/static/css/suneditor.min.css">'
        '<style>body{margin:0}iframe{width:100%;max-width:640px;height:850px;'
        'border:0;display:block}</style></head><body>'
        + content
        + '<script src="/static/js/purify.min.js"></script>'
        '<script src="/static/js/suneditor.min.js"></script>'
        '<script src="/static/js/message_templates.js"></script></body></html>'
    )


def _open_editor(browser, content, *, rich_editor=True):
    page = browser.new_page(viewport={"width": 1024, "height": 900})
    state = {
        "record": {
            "id": 7, "slug": "banner-test", "name": "Banner test",
            "description": "", "content_type": "text/html", "content": content,
        },
        "saves": [],
        "errors": [],
    }
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/static/"):
            if not rich_editor and path.endswith("suneditor.min.js"):
                route.fulfill(body="", content_type="application/javascript")
                return
            asset = (ROOT / "app" / path.lstrip("/")).resolve()
            assert asset.is_relative_to(ROOT / "app/static")
            route.fulfill(path=str(asset))
        elif path == "/api/message-templates/7":
            assert route.request.method == "PUT"
            payload = route.request.post_data_json
            state["saves"].append(payload)
            state["record"].update(payload)
            route.fulfill(content_type="application/json", body=json.dumps(state["record"]))
        elif path == "/admin/message-templates/7/edit":
            route.fulfill(content_type="text/html", body=_markup(state["record"]))
        else:
            route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto("https://portal.test/admin/message-templates/7/edit")
    return page, state


def _assert_styled_preview(page):
    preview = page.frame_locator("[data-mt-preview-frame]")
    heading = preview.get_by_role("heading", name="Meet MyPortal.")
    heading.wait_for()
    assert heading.evaluate("el => getComputedStyle(el).fontSize") == "36px"
    assert heading.evaluate("el => getComputedStyle(el.closest('td')).backgroundColor") == "rgb(15, 23, 42)"
    ticket = preview.get_by_text("Ticket management", exact=True)
    assets = preview.get_by_text("Company assets", exact=True)
    assert assets.bounding_box()["x"] > ticket.bounding_box()["x"]
    assert assets.bounding_box()["y"] == ticket.bounding_box()["y"]
    assert preview.locator('table[cellpadding="0"][cellspacing="0"]').count() > 0
    assert preview.get_by_role("link", name="Sign up to MyPortal").get_attribute("href") == "https://portal.hawkinsit.au"
    assert page.locator("[data-mt-preview-frame]").get_attribute("sandbox") == ""


@pytest.mark.parametrize("leave_code_view", [False, True])
def test_appended_banner_survives_save_reopen_and_preview(browser, leave_code_view):
    original = "<p>Hello {{ user.first_name }}</p>"
    page, state = _open_editor(browser, original)
    try:
        page.locator('button[data-command="codeView"]').click()
        page.locator("textarea.se-wrapper-code").fill(original + BANNER)
        if leave_code_view:
            page.locator('button[data-command="codeView"]').click()
        with page.expect_response("**/api/message-templates/7"):
            if leave_code_view:
                page.locator("[data-mt-save]").click()
            else:
                page.keyboard.press("Control+s")
        saved = state["saves"][-1]
        assert saved["content_type"] == "text/html"
        assert "Hello {{ user.first_name }}" in saved["content"]
        assert "display:inline-block" in saved["content"]
        assert 'bgcolor="#0f172a"' in saved["content"]
        assert "<!--[if mso]>" in saved["content"]
        _assert_styled_preview(page)

        page.reload()
        _assert_styled_preview(page)
        with page.expect_response("**/api/message-templates/7"):
            page.locator("[data-mt-save]").click()
        assert "display:inline-block" in state["saves"][-1]["content"]
        assert 'bgcolor="#0f172a"' in state["saves"][-1]["content"]

        for width in (320, 768, 1024, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            frame = page.frame_locator("[data-mt-preview-frame]")
            assert frame.locator("body").evaluate(
                "el => el.ownerDocument.documentElement.scrollWidth <= el.ownerDocument.defaultView.innerWidth"
            )
        assert state["errors"] == []
    finally:
        page.close()


def test_styled_preview_still_removes_active_html_without_rich_editor(browser):
    content = '''<p style="color:#38bdf8;font-size:20px">Safe text</p>
      <script>parent.compromised=true</script><style>body{display:none}</style>
      <iframe src="https://untrusted.test/"></iframe>
      <a href="javascript:alert(1)" onclick="alert(2)">Unsafe link</a>
      <img src="/missing.png" onerror="alert(3)">
      <form action="https://untrusted.test/"><input name="secret"></form>'''
    page, state = _open_editor(browser, content, rich_editor=False)
    try:
        preview = page.frame_locator("[data-mt-preview-frame]")
        text = preview.get_by_text("Safe text", exact=True)
        text.wait_for()
        assert text.evaluate("el => getComputedStyle(el).color") == "rgb(56, 189, 248)"
        assert preview.locator("body script,body style,iframe,form,input").count() == 0
        assert preview.locator("[onclick],[onerror]").count() == 0
        assert preview.get_by_text("Unsafe link").get_attribute("href") is None
        assert page.locator("[data-mt-preview-frame]").get_attribute("sandbox") == ""
        assert state["errors"] == []
    finally:
        page.close()


def test_switching_styled_html_to_plain_text_preserves_words_and_links(browser):
    page, state = _open_editor(browser, BANNER)
    try:
        page.locator('[data-mt-format][value="text/plain"]').check()
        with page.expect_response("**/api/message-templates/7"):
            page.locator("[data-mt-save]").click()
        saved = state["saves"][-1]
        assert saved["content_type"] == "text/plain"
        assert "Ticket management" in saved["content"]
        assert "https://portal.hawkinsit.au" in saved["content"]
        assert "<table" not in saved["content"]
        assert "background-color" not in saved["content"]
        assert state["errors"] == []
    finally:
        page.close()
