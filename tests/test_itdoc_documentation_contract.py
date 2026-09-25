"""Architecture guardrails for the ITDOC documentation programme."""

from pathlib import Path


CONTRACT = Path("docs/architecture/itdoc-documentation-contract.md")


def _contract_text() -> str:
    return " ".join(CONTRACT.read_text(encoding="utf-8").split())


def test_contract_keeps_assets_as_the_only_asset_store_and_page():
    text = _contract_text()

    assert "sole asset store and asset experience" in text
    assert "must not introduce another asset table" in text
    assert "`assets.id`" in text
    assert "`/assets`" in text


def test_contract_preserves_existing_data_and_hudu_configuration():
    text = _contract_text()

    for invariant in (
        "asset IDs",
        "custom fields",
        "ticket links",
        "sync identifiers",
        "KB content",
        "Hudu settings/mappings",
    ):
        assert invariant in text
    assert "Do not delete or blank Hudu credentials" in text


def test_contract_requires_company_flags_and_surface_authorisation_tests():
    text = _contract_text()

    assert "`company_documentation_settings`" in text
    assert "Absence means disabled" in text
    for surface in ("Pages/direct URLs", "APIs", "Search/RAG", "Links", "Exports"):
        assert surface in text
    assert "substitutes a company-B record ID in a direct URL or API call" in text


def test_contract_prohibits_secrets_but_allows_safe_manager_references():
    text = _contract_text()

    assert "must never contain passwords" in text
    assert "non-sensitive label or URL identifying an entry" in text
    assert "No username, password, token, key, recovery material" in text
