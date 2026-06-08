"""Tests for Essential 8 per-element upsell URL generation."""

from app.features.compliance import routes as compliance_routes


def test_slugify_essential8_element():
    assert (
        compliance_routes._slugify_essential8_element("Patch Applications & Software")
        == "patch-applications-software"
    )


def test_build_essential8_help_url_appends_element_query():
    assert (
        compliance_routes._build_essential8_help_url(
            "/marketing/essential8",
            "application-control",
        )
        == "/marketing/essential8?element=application-control"
    )


def test_build_essential8_help_url_preserves_existing_query():
    assert (
        compliance_routes._build_essential8_help_url(
            "/marketing/essential8?utm=campaign",
            "application-control",
        )
        == "/marketing/essential8?utm=campaign&element=application-control"
    )


def test_build_essential8_help_url_replaces_placeholder():
    assert (
        compliance_routes._build_essential8_help_url(
            "https://example.com/essential8/{element}",
            "application-control",
        )
        == "https://example.com/essential8/application-control"
    )

