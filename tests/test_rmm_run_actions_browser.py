"""Run actions with real templates/JS and locally intercepted API requests.

Run with RUN_BROWSER_TESTS=1 and system Chromium.
"""
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("RUN_BROWSER_TESTS") != "1":
        pytest.skip("Set RUN_BROWSER_TESTS=1 to run browser checks")
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
    if not executable:
        pytest.skip("Chromium is required")
    with api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, args=[
            "--no-sandbox", "--disable-crash-reporter", "--disable-breakpad",
        ])
        yield browser
        browser.close()


@pytest.fixture
def mount(browser):
    contexts = []
    errors = []

    def create(status="failed", *, can_run=True, picked_up=False, width=1280, result_status=None):
        run = {
            "id": 4, "script_id": 7, "asset_id": 10, "script_name": "Check disk",
            "script_path": "Common/check.ps1", "asset_name": "PC-01", "status": status,
            "timeout_seconds": 90, "exit_code": 1, "queued_at": "2026-10-15T00:00:00",
            "inputs": [
                {"kind": "param", "name": "Drive", "value": "C", "sensitive": False},
                {"kind": "param", "name": "Password", "value": "••••••••", "sensitive": True},
                {"kind": "env", "name": "API_TOKEN", "value": "{{company.variables.Token}}", "sensitive": True, "is_variable": True},
            ],
        }
        fields = [
            {"key": "param:Drive", "kind": "param", "name": "Drive", "type": "string", "mandatory": True},
            {"key": "param:Password", "kind": "param", "name": "Password", "type": "secret", "sensitive": True},
            {"key": "env:API_TOKEN", "kind": "env", "name": "API_TOKEN", "type": "secret", "sensitive": True},
        ]
        detail = {
            "script": {"id": 7, "name": "Check disk", "path": "Common/check.ps1", "language": "powershell", "language_label": "PowerShell", "default_timeout_seconds": 600},
            "fields": fields, "variables": [], "agents": [
                {"asset_id": 10, "asset_name": "PC-01"}, {"asset_id": 11, "asset_name": "PC-02"},
            ],
        }
        env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True)
        rows = env.get_template("rmm/_macros.html").module.run_rows([run], show_device=False, can_run=can_run)
        modal = env.get_template("rmm/_run_modal.html").render()
        html = (
            '<!doctype html><meta charset="utf-8"><meta name="csrf-token" content="test-csrf">'
            '<link rel="stylesheet" href="/static/css/app.css">'
            '<link rel="stylesheet" href="/static/css/rmm.css">'
            '<div data-rmm-page><div class="table-wrapper"><table class="table"><tbody>' + str(rows) + '</tbody></table></div></div>'
            '<script type="application/json" id="rmm-config">' + json.dumps({"can_run": can_run, "asset_id": 11}) + '</script>'
            + modal + '<script src="/static/js/rmm_scripts.js"></script>'
        )
        context = browser.new_context(viewport={"width": width, "height": 900})
        contexts.append(context)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        posts = []

        def intercept(route):
            path = urlsplit(route.request.url).path
            if path == "/fixture":
                route.fulfill(content_type="text/html", body=html)
                return
            if path == "/static/js/rmm_scripts.js":
                route.fulfill(content_type="application/javascript", body=(ROOT / "app/static/js/rmm_scripts.js").read_text())
                return
            if path in ("/static/css/app.css", "/static/css/rmm.css"):
                route.fulfill(content_type="text/css", body=(ROOT / "app" / path.lstrip("/")).read_text())
                return
            response = {}
            code = 200
            if path == "/api/rmm/runs/4/cancel":
                posts.append((path, route.request.headers, None))
                run["status"] = "dispatched" if picked_up else "cancelled"
                code = 409 if picked_up else 200
                response = {"detail": "The device has already collected this run."} if picked_up else {"run": run}
            elif path == "/api/rmm/runs/4":
                if result_status:
                    run["status"] = result_status
                response = {"run": run}
            elif path == "/api/rmm/scripts":
                response = {"scripts": [detail["script"]]}
            elif path == "/api/rmm/scripts/7":
                response = detail
            elif path == "/api/rmm/scripts/7/runs":
                posts.append((path, route.request.headers, route.request.post_data_json))
                response = {"queued": [{"run_id": 5}], "problems": []}
            elif path == "/api/rmm/runs/5":
                response = {"run": {**run, "id": 5, "status": "queued"}}
            else:
                raise AssertionError(f"Unexpected request: {path}")
            route.fulfill(status=code, content_type="application/json", body=json.dumps(response))

        page.route("**/*", intercept)
        page.goto("http://rmm.test/fixture")
        return page, posts

    yield create
    for context in contexts:
        context.close()
    assert not errors


@pytest.mark.parametrize("status", ["expired", "timed_out", "failed"])
@pytest.mark.parametrize("width", [390, 1280])
def test_retry_prefills_device_values_timeout_and_requires_secrets(mount, status, width):
    from playwright.sync_api import expect

    page, posts = mount(status, width=width)
    page.locator('[data-rmm-retry-run-id="4"]').click()
    expect(page.locator('[data-rmm-value="param:Drive"]')).to_have_value("C")
    expect(page.locator('[data-rmm-value="param:Password"]')).to_have_value("")
    expect(page.locator('[data-rmm-value="env:API_TOKEN"]')).to_have_value("{{company.variables.Token}}")
    expect(page.locator('[data-rmm-timeout]')).to_have_value("1.5")
    expect(page.locator('[data-rmm-submit]')).to_have_text("Retry script")
    expect(page.locator('#rmm-device-10')).to_be_checked()
    expect(page.locator('#rmm-device-11')).not_to_be_checked()
    page.locator('[data-rmm-submit]').click()
    expect(page.locator('[data-rmm-error="param:Password"]')).to_contain_text("Re-enter")
    assert posts == []
    page.locator('[data-rmm-value="param:Password"]').fill("new-secret")
    page.locator('[data-rmm-submit]').click()
    expect(page.locator('[data-rmm-result-modal]')).to_be_visible()
    assert len(posts) == 1
    path, headers, payload = posts[0]
    assert path == "/api/rmm/scripts/7/runs"
    assert headers["x-csrf-token"] == "test-csrf"
    assert payload == {"asset_ids": [10], "timeout_seconds": 90, "values": {
        "param:Drive": "C", "param:Password": "new-secret", "env:API_TOKEN": "{{company.variables.Token}}",
    }}
    expect(page.locator('[data-rmm-run-row="4"] [data-column-key="status"]')).to_contain_text({"expired": "Expired", "timed_out": "Timed out", "failed": "Failed"}[status])


@pytest.mark.parametrize("picked_up", [False, True])
def test_cancel_updates_list_and_handles_agent_pickup(mount, picked_up):
    from playwright.sync_api import expect

    page, posts = mount("queued", picked_up=picked_up)
    page.locator('[data-rmm-cancel-run-id="4"]').click()
    expect(page.locator('[data-rmm-run-row="4"] [data-column-key="status"]')).to_contain_text("Sent to device" if picked_up else "Cancelled")
    expect(page.locator('[data-rmm-cancel-run-id="4"]')).to_be_hidden()
    if picked_up:
        expect(page.locator('[data-rmm-action-error]')).to_contain_text("already collected")
    assert len(posts) == 1
    assert posts[0][1]["x-csrf-token"] == "test-csrf"


@pytest.mark.parametrize("status", ["dispatched", "running", "completed", "cancelled"])
def test_active_and_ineligible_runs_have_no_actions(mount, status):
    page, _ = mount(status)
    assert page.locator('[data-rmm-cancel-run-id], [data-rmm-retry-run-id]').count() == 0


@pytest.mark.parametrize("status", ["queued", "failed"])
def test_read_only_runs_have_no_write_actions(mount, status):
    page, _ = mount(status, can_run=False)
    assert page.locator('[data-rmm-cancel-run-id], [data-rmm-retry-run-id]').count() == 0


def test_result_refresh_exposes_retry_when_a_queued_run_fails(mount):
    from playwright.sync_api import expect

    page, _ = mount("queued", result_status="failed")
    page.locator('[data-rmm-view-run="4"]').click()
    expect(page.locator('[data-rmm-retry-run]')).to_be_visible()
    expect(page.locator('[data-rmm-retry-run-id="4"]')).to_be_visible()
    expect(page.locator('[data-rmm-cancel-run-id="4"]')).to_be_hidden()
    page.locator('[data-rmm-retry-run]').click()
    expect(page.locator('[data-rmm-value="param:Drive"]')).to_have_value("C")


def test_result_cancel_keeps_the_list_in_sync(mount):
    from playwright.sync_api import expect

    page, posts = mount("queued")
    page.locator('[data-rmm-view-run="4"]').click()
    page.locator('[data-rmm-cancel-run]').click()
    expect(page.locator('[data-rmm-result-stats]')).to_contain_text("Cancelled")
    expect(page.locator('[data-rmm-run-row="4"] [data-column-key="status"]')).to_contain_text("Cancelled")
    expect(page.locator('[data-rmm-cancel-run]')).to_be_hidden()
    assert len(posts) == 1
