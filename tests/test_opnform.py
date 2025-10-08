import pytest

from app.services.opnform import (
    OpnformValidationError,
    normalize_opnform_embed_code,
    normalize_opnform_form_url,
)


def test_normalize_form_url_enforces_allowed_host():
    with pytest.raises(OpnformValidationError):
        normalize_opnform_form_url(
            "https://example.org/forms/123",
            allowed_host="forms.example.com",
        )


def test_normalize_form_url_returns_canonical_url():
    result = normalize_opnform_form_url(
        "https://forms.example.com/forms/123?ref=abc#section",
        allowed_host="forms.example.com",
    )
    assert result == "https://forms.example.com/forms/123?ref=abc"


def test_normalize_embed_code_sanitizes_iframe_and_script():
    embed = (
        '<iframe src="https://forms.example.com/share/abc" style="width:100%;height:600px"></iframe>'
        '<script src="/embed.js"></script>'
    )
    normalized = normalize_opnform_embed_code(embed, allowed_host="forms.example.com")
    assert 'class="form-frame"' in normalized.sanitized_embed_code
    assert 'data-opnform-embed="support"' in normalized.sanitized_embed_code
    assert normalized.form_url == "https://forms.example.com/share/abc"


def test_normalize_embed_code_rejects_mismatched_script_host():
    embed = (
        '<iframe src="https://forms.example.com/share/abc"></iframe>'
        '<script src="https://cdn.bad.example/embed.js"></script>'
    )
    with pytest.raises(OpnformValidationError):
        normalize_opnform_embed_code(embed, allowed_host="forms.example.com")
