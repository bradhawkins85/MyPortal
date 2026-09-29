import pathlib

from app.core.core_components import CORE_COMPONENTS_BY_SLUG
from app.features.help import service
from app.features.help.requirements import ARTICLE_REQUIREMENTS, SECTION_REQUIREMENTS
from app.services.modules import DEFAULT_MODULES

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FEATURES_DIR = REPO_ROOT / "app" / "features"


def _all_requirements():
    for group in (*SECTION_REQUIREMENTS.values(), *ARTICLE_REQUIREMENTS.values()):
        yield from group


def test_requirement_slugs_name_real_packs_and_modules():
    packs = {path.parent.name for path in FEATURES_DIR.glob("*/__init__.py")}
    packs |= set(CORE_COMPONENTS_BY_SLUG)
    modules = {module["slug"] for module in DEFAULT_MODULES}
    for requirement in _all_requirements():
        kind, _, slug = requirement.partition(":")
        assert kind in {"pack", "module"}, requirement
        assert slug in (packs if kind == "pack" else modules), requirement


def test_requirement_keys_name_real_articles():
    sections = {section["slug"]: section for section in service.list_sections()}
    for section_slug in SECTION_REQUIREMENTS:
        assert section_slug in sections, section_slug
    articles = {
        f"{article['section_slug']}/{article['name']}"
        for section in sections.values()
        for article in section["articles"]
    }
    missing = sorted(set(ARTICLE_REQUIREMENTS) - articles)
    assert not missing, missing


def _check(*, packs=(), modules=(), available_modules=None, enabled=None):
    disabled_packs = set(packs)
    return service.build_requirement_check(
        pack_available=lambda slug: slug not in disabled_packs,
        module_available=lambda slug: available_modules is None or slug in available_modules,
        enabled_modules=enabled if enabled is not None else modules,
    )


def _visible_keys(is_active):
    return {
        f"{article['section_slug']}/{article['name']}"
        for section in service.filter_sections(service.list_sections(), is_active)
        for article in section["articles"]
    }


def test_disabled_pack_hides_its_articles_and_empty_sections():
    visible = _visible_keys(_check(packs={"tickets", "shop"}, modules={"xero"}))

    assert not any(key.startswith("tickets/") for key in visible)
    assert "administration/Shop Packages" not in visible
    assert "integrations/Xero Integration" in visible
    assert "getting-started/Home" in visible


def test_module_articles_follow_module_enabled_state():
    hidden = _visible_keys(_check(modules=()))
    shown = _visible_keys(_check(modules={"xero", "imap"}))

    assert "integrations/Xero Integration" not in hidden
    assert "tickets/IMAP Setup" not in hidden
    assert "integrations/Xero Integration" in shown
    assert "tickets/IMAP Setup" in shown


def test_article_inside_restricted_section_needs_both_requirements():
    visible = _visible_keys(_check(packs={"tickets"}, modules={"imap"}))

    assert "tickets/IMAP Setup" not in visible


def test_deployment_excluded_module_is_hidden_even_if_enabled():
    visible = _visible_keys(
        _check(available_modules=set(), enabled={"xero"})
    )

    assert "integrations/Xero Integration" not in visible


def test_unknown_module_state_falls_back_to_availability():
    is_active = service.build_requirement_check(
        pack_available=lambda slug: True,
        module_available=lambda slug: True,
        enabled_modules=None,
    )

    assert is_active("module:xero")
