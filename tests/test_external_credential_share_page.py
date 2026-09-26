from pathlib import Path


def test_public_share_page_never_embeds_token_or_plaintext_and_is_accessible():
    template = Path("app/templates/credential_share.html").read_text()
    assert "share_token" not in template
    assert "plaintext" not in template
    assert 'name="robots" content="noindex,nofollow,noarchive,nosnippet"' in template
    assert 'name="referrer" content="no-referrer"' in template
    assert 'aria-live="polite"' in template
    assert 'for="verification-code"' in template
    assert "Reveal credential once" in template
    assert "contact the sender" in template.lower()
    assert "http://" not in template and "https://" not in template


def test_share_script_sanitizes_history_and_keeps_failures_non_enumerable():
    script = Path("app/static/js/credential_share.js").read_text()
    assert "window.location.hash" in script
    assert "history.replaceState(null, '', '/credential-share')" in script
    assert "window.location.search" not in script
    assert "credentials: 'omit'" in script
    assert "referrerPolicy: 'no-referrer'" in script
    assert "response.json()" in script  # only executed after the successful reveal
    assert "expired, revoked, already used, or the code may be invalid" in script


def test_external_share_urls_put_secret_token_in_fragment_only():
    service = Path("app/services/workflow_credential_shares.py").read_text()
    assert "/credential-share#{quote(token" in service
    assert "/credential-share/{quote(token" not in service
