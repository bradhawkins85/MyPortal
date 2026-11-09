from pathlib import Path

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "app" / "templates" / "m365" / "index.html"


def test_m365_configuration_template_compiles() -> None:
    environment = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))

    environment.get_template("m365/index.html")


def test_m365_configuration_uses_the_settings_workspace() -> None:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "data-ae-workspace" in source and "asset_edit_sections.js" in source
    for key in ("overview", "setup", "credentials", "provisioning", "permissions", "sync", "signatures"):
        assert f'data-ae-section="{key}"' in source, key
    assert "Connection overview" in source
    assert "Guided tenant setup" in source
    assert "Advanced provisioning credentials" in source
    assert "Verify and activate connection" in source
    assert 'action="/m365/connection/complete"' in source
    assert "Successful sync jobs do not complete this setup step" in source
    assert source.count("{{ credential.tenant_id") == 1
    assert source.count("{{ credential.client_id") == 1


def test_m365_sensitive_actions_retain_csrf_and_safe_secret_fields() -> None:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert source.count('name="_csrf"') >= 4
    assert source.count('autocomplete="new-password"') == 2
    assert 'rel="noopener noreferrer"' in source
    assert "Delete stored Microsoft 365 credentials?" in source


def test_confirm_tenant_uses_navigation_for_oauth_redirect() -> None:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    partial = (TEMPLATE_PATH.parent / "_discovered_tenant.html").read_text(encoding="utf-8")

    assert '{% include "m365/_discovered_tenant.html" %}' in source
    expected_link = 'href="/m365/provision?tenant_id={{ discovered_tenant.tenant_id | urlencode }}"'
    assert expected_link in partial
    assert 'action="/m365/provision"' not in source + partial
    # The tenant is shown from the signed hand-off, never from the raw URL.
    assert "request.query_params.get('m365_discovered_tenant')" not in source
