from pathlib import Path

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "app" / "templates" / "m365" / "index.html"


def test_m365_configuration_template_compiles() -> None:
    environment = Environment(loader=FileSystemLoader(ROOT / "app" / "templates"))

    environment.get_template("m365/index.html")


def test_m365_configuration_uses_compact_responsive_workspace() -> None:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'class="m365-grid"' in source
    assert 'class="m365-stack"' in source
    assert "@media (max-width: 900px)" in source
    assert "Connection overview" in source
    assert "Guided tenant setup" in source
    assert "Advanced provisioning credentials" in source
    assert source.count("{{ credential.tenant_id") == 1
    assert source.count("{{ credential.client_id") == 1


def test_m365_sensitive_actions_retain_csrf_and_safe_secret_fields() -> None:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert source.count('name="_csrf"') >= 4
    assert source.count('autocomplete="new-password"') == 2
    assert 'rel="noopener noreferrer"' in source
    assert "Delete stored Microsoft 365 credentials?" in source
