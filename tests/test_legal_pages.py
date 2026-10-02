"""Policy pages are public, linked from sign-in, and render for signed-in users."""

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


@pytest.fixture
def client(monkeypatch):
    async def no_session(request):
        return None

    async def existing_user_count():
        return 1

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    monkeypatch.setattr(main_module.user_repo, "count_users", existing_user_count)
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("/legal/privacy", "Privacy Policy"),
        ("/legal/acceptable-use", "Acceptable Use Policy"),
        ("/legal/terms", "Terms and Conditions"),
    ],
)
def test_policy_pages_render_without_signing_in(client, path, heading):
    response = client.get(path)

    assert response.status_code == 200
    assert f'id="legal-document-title">{heading}</h2>' in response.text
    assert 'aria-current="page">' + heading in response.text


def test_legal_overview_lists_every_policy(client):
    response = client.get("/legal")

    assert response.status_code == 200
    for slug in ("privacy", "acceptable-use", "terms"):
        assert f'href="/legal/{slug}"' in response.text


def test_unknown_policy_returns_404(client):
    response = client.get("/legal/not-a-policy")

    assert response.status_code == 404


def test_privacy_policy_uses_configured_operator_details(client, monkeypatch):
    monkeypatch.setattr(main_module.settings, "legal_entity_name", "Example IT Pty Ltd")
    monkeypatch.setattr(main_module.settings, "legal_contact_email", "privacy@example.com")

    response = client.get("/legal/privacy")

    assert "Example IT Pty Ltd" in response.text
    assert 'href="mailto:privacy@example.com"' in response.text


def test_privacy_policy_falls_back_without_contact_email(client, monkeypatch):
    monkeypatch.setattr(main_module.settings, "legal_contact_email", "")
    monkeypatch.setattr(main_module.settings, "smtp_from", None)

    response = client.get("/legal/privacy")

    assert response.status_code == 200
    assert "mailto:" not in response.text
    assert "raising a ticket in" in response.text


def test_privacy_policy_matches_profile_self_service_options(client):
    response = client.get("/legal/privacy")

    assert response.status_code == 200
    assert "view your details and update your password, two-factor and passkey settings, mobile number and notification preferences from your profile" in response.text
    assert "to change your name or email address, contact us or your organisation's administrator" in response.text


@pytest.mark.parametrize("path", ["/login", "/register", "/forgot-password"])
def test_sign_in_pages_link_to_policies(client, path):
    response = client.get(path)

    assert response.status_code == 200
    for slug in ("privacy", "acceptable-use", "terms"):
        assert f'href="/legal/{slug}"' in response.text


def test_policy_page_uses_signed_in_context_for_signed_in_user(client, monkeypatch):
    user = {"id": 7, "email": "user@example.com", "is_super_admin": False}
    seen_users = []

    async def fake_optional_user(request):
        return user, None

    async def fake_base_context(request, current_user, *, extra=None):
        seen_users.append(current_user)
        return await main_module._build_public_context(request, extra=extra)

    monkeypatch.setattr(main_module, "_get_optional_user", fake_optional_user)
    monkeypatch.setattr(main_module, "_build_base_context", fake_base_context)

    response = client.get("/legal/terms")

    assert response.status_code == 200
    assert seen_users == [user]
    assert 'id="legal-document-title">Terms and Conditions</h2>' in response.text


@pytest.mark.parametrize("path", ["/legal", "/legal/privacy", "/legal/acceptable-use", "/legal/terms"])
def test_every_policy_page_shows_no_warranty_notice(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert 'provided "as is", without warranty of any kind' in response.text
    assert "No liability lies with the developers or contributors" in response.text


def test_privacy_policy_mentions_ticket_email_tracking(client):
    """The privacy page must disclose open/click tracking on ticket notification emails."""
    response = client.get("/legal/privacy")

    assert response.status_code == 200
    # Section 2: ticket emails are tracked
    assert "emails about your tickets and replies" in response.text
    assert "tracking image and tracked links" in response.text
    # What is recorded for ticket emails
    assert "IP address" in response.text
    assert "browser and device details" in response.text
    # Section 3: purpose — confirming support updates received
    assert "confirm that support updates about your tickets have been received" in response.text
    # Section 6: tracking image is not a cookie; blocking remote images prevents open tracking
    assert "it does not rely on cookies" in response.text
    assert "block remote images" in response.text


def test_privacy_policy_lists_ticket_reply_draft_cookie(client):
    """The cookie list must include the encrypted ticket reply draft cookie."""
    response = client.get("/legal/privacy")

    assert response.status_code == 200
    # Section 6: the reply draft cookie is listed
    assert "Reply draft cookie" in response.text
    assert "encrypted copy of a ticket reply you haven't sent yet" in response.text
    assert "expires after 7 days" in response.text
    # The draft is kept in a cookie, not local storage, so it must not be
    # described as part of the browser's local storage.
    assert "unsent reply drafts" not in response.text


def test_terms_disclaim_warranty_and_developer_liability(client):
    response = client.get("/legal/terms")

    assert 'id="terms-liability"' in response.text
    assert "No warranty and limitation of liability" in response.text
    assert "No liability lies with the developers or contributors" in response.text


@pytest.mark.parametrize(
    ("path", "section_id", "heading"),
    [
        ("/legal/privacy", "privacy-ai", "Artificial intelligence (AI) features"),
        ("/legal/acceptable-use", "aup-ai", "Using AI features"),
        ("/legal/terms", "terms-ai", "Artificial intelligence features"),
    ],
)
def test_every_policy_covers_ai_use(client, path, section_id, heading):
    response = client.get(path)

    assert response.status_code == 200
    assert f'<section id="{section_id}"' in response.text
    assert f'href="#{section_id}"' in response.text
    assert heading in response.text
