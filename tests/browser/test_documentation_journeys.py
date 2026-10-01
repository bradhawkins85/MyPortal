"""Staging browser gate for the IT documentation rollout.

These tests deliberately use only rendered controls and links.  Seed records are
supplied by the rollout owner in ``MYPORTAL_BROWSER_FIXTURE``; the suite never
creates privileged state through an API or database shortcut.
"""

import json
import os
from pathlib import Path
from urllib.parse import urljoin

import pytest

pytest.importorskip("playwright.sync_api")


def _fixture() -> dict:
    path = os.environ.get("MYPORTAL_BROWSER_FIXTURE")
    if not path:
        pytest.skip("staging browser fixture is not configured")
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def gate() -> dict:
    data = _fixture()
    required = {"base_url", "users", "asset", "website", "credential_share"}
    missing = required.difference(data)
    assert not missing, f"browser fixture is missing: {', '.join(sorted(missing))}"
    return data


def _login(page, gate: dict, role: str) -> None:
    account = gate["users"][role]
    page.goto(urljoin(gate["base_url"], "/login"))
    page.get_by_label("Email address").fill(account["email"])
    page.get_by_label("Password").fill(account["password"])
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_load_state("networkidle")
    assert "/login" not in page.url


def _direct_status(page, gate: dict, path: str) -> int:
    response = page.goto(urljoin(gate["base_url"], path))
    assert response is not None
    return response.status


@pytest.mark.browser_gate
def test_search_asset_runbook_keyboard_journey(page, gate):
    _login(page, gate, "technician")
    page.get_by_role("link", name="Documentation search").click()
    query = page.get_by_label("Hostname, asset or runbook")
    query.fill(gate["asset"]["search"])
    query.press("Enter")
    page.get_by_role("link", name=gate["asset"]["name"], exact=True).focus()
    page.keyboard.press("Enter")
    page.get_by_role("heading", name=gate["asset"]["name"]).wait_for()
    runbook = page.get_by_role("link", name=gate["asset"]["runbook"])
    runbook.focus()
    page.keyboard.press("Enter")
    page.get_by_role("heading", name=gate["asset"]["runbook"]).wait_for()

    page.goto(urljoin(gate["base_url"], "/documentation-search"))
    page.get_by_label("Hostname, asset or runbook").fill(gate["empty_search"])
    page.get_by_role("button", name="Search").click()
    page.get_by_text("No accessible documentation matched your search.").wait_for()


@pytest.mark.browser_gate
def test_asset_process_ticket_journey(page, gate):
    _login(page, gate, "technician")
    page.goto(urljoin(gate["base_url"], gate["asset"]["path"]))
    page.get_by_role("link", name="Start process").click()
    page.get_by_label("Template").select_option(label=gate["asset"]["process_template"])
    page.get_by_label("Linked ticket ID").fill(str(gate["asset"]["ticket_id"]))
    page.get_by_role("button", name="Start process").click()
    page.get_by_role("heading", name=gate["asset"]["process_template"]).wait_for()
    for step in page.locator("[data-step-action]").all():
        step.select_option("completed")
    page.get_by_role("button", name="Complete run").click()
    page.get_by_text("Completed", exact=True).first.wait_for()


@pytest.mark.browser_gate
def test_website_check_and_expiry_journey(page, gate):
    _login(page, gate, "technician")
    page.get_by_role("link", name="Websites").click()
    page.get_by_role("link", name=gate["website"]["name"], exact=True).click()
    page.get_by_role("heading", name=gate["website"]["name"]).wait_for()
    page.get_by_role("button", name="Check now").click()
    page.get_by_text("Check completed and observations were refreshed.").wait_for()
    page.get_by_text("TLS expiry").wait_for()
    page.get_by_role("link", name="Expirations").first.click()
    page.get_by_text(gate["website"]["name"]).wait_for()


@pytest.mark.browser_gate
def test_manual_asset_photo_mobile_fallback(page, gate, tmp_path):
    page.set_viewport_size({"width": 390, "height": 844})
    _login(page, gate, "technician")
    page.goto(urljoin(gate["base_url"], gate["asset"]["path"]))
    assert page.locator("#asset-camera").get_attribute("capture") == "environment"
    # Exercise the picker fallback with a runtime-only 1px PNG (no binary fixture).
    image = tmp_path / "gate-photo.png"
    image.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63606060f80f0001040100f5d6c4460000000049454e44ae426082"))
    page.locator("#asset-picker").set_input_files(image)
    page.get_by_label("Caption").fill("Rollout gate photo")
    page.get_by_role("button", name="Upload photo").click()
    page.get_by_role("img", name="Rollout gate photo").wait_for()


@pytest.mark.browser_gate
def test_credential_recipient_redemption_and_expiry(browser, gate):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    share = gate["credential_share"]
    page.goto(urljoin(gate["base_url"], share["url"]))
    page.get_by_label("Verification code").fill(share["code"])
    page.get_by_role("button", name="Verify code").click()
    page.get_by_role("button", name="Reveal credential once").click()
    page.locator("#secret").wait_for()
    assert page.locator("#secret").inner_text().strip()
    page.reload()
    page.get_by_text("expired, revoked, already used, or the code may be invalid", exact=False).wait_for()
    context.close()


@pytest.mark.browser_gate
@pytest.mark.parametrize("role", ["customer", "named_staff", "job_title"])
def test_authorised_roles_see_only_their_rendered_record(page, gate, role):
    _login(page, gate, role)
    expected = gate["users"][role]["visible_path"]
    assert _direct_status(page, gate, expected) == 200
    page.locator("main").get_by_text(gate["users"][role]["visible_text"]).wait_for()
    assert _direct_status(page, gate, gate["wrong_company_path"]) in {403, 404}
    assert gate["wrong_company_marker"] not in page.locator("body").inner_text()


@pytest.mark.browser_gate
def test_wrong_company_user_is_denied_in_navigation_and_direct_request(page, gate):
    _login(page, gate, "wrong_company")
    assert page.get_by_text(gate["asset"]["name"], exact=True).count() == 0
    assert _direct_status(page, gate, gate["asset"]["path"]) in {403, 404}
    assert gate["asset"]["name"] not in page.locator("body").inner_text()
