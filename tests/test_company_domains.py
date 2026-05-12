"""Tests for company email-domain normalisation and validation utilities."""
from __future__ import annotations

import pytest

from app.services.company_domains import (
    EMAIL_DOMAIN_PATTERN,
    EmailDomainError,
    normalise_email_domains,
    parse_email_domain_text,
)


# ---------------------------------------------------------------------------
# normalise_email_domains
# ---------------------------------------------------------------------------


def test_normalise_single_valid_domain():
    assert normalise_email_domains(["example.com"]) == ["example.com"]


def test_normalise_converts_to_lowercase():
    assert normalise_email_domains(["EXAMPLE.COM"]) == ["example.com"]
    assert normalise_email_domains(["Example.Com"]) == ["example.com"]


def test_normalise_strips_surrounding_whitespace():
    assert normalise_email_domains(["  example.com  "]) == ["example.com"]
    assert normalise_email_domains(["\texample.com\n"]) == ["example.com"]


def test_normalise_deduplicates_preserving_order():
    result = normalise_email_domains(["alpha.com", "beta.com", "alpha.com"])
    assert result == ["alpha.com", "beta.com"]


def test_normalise_deduplicates_case_insensitive():
    result = normalise_email_domains(["Alpha.com", "ALPHA.COM"])
    assert result == ["alpha.com"]


def test_normalise_skips_empty_strings():
    result = normalise_email_domains(["example.com", "", "  ", "other.org"])
    assert result == ["example.com", "other.org"]


def test_normalise_skips_none_entry():
    # None is coerced via `str(None or "")` → empty string → skipped silently
    result = normalise_email_domains([None])
    assert result == []


def test_normalise_empty_iterable():
    assert normalise_email_domains([]) == []


def test_normalise_rejects_domain_exceeding_255_chars():
    long_domain = "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 63 + "e"
    # > 255 chars
    assert len(long_domain) > 255
    with pytest.raises(EmailDomainError, match="255 characters"):
        normalise_email_domains([long_domain])


def test_normalise_rejects_domain_starting_with_hyphen():
    with pytest.raises(EmailDomainError):
        normalise_email_domains(["-invalid.com"])


def test_normalise_rejects_domain_ending_with_hyphen():
    with pytest.raises(EmailDomainError):
        normalise_email_domains(["invalid-.com"])


def test_normalise_rejects_plain_hostname_without_tld():
    with pytest.raises(EmailDomainError):
        normalise_email_domains(["localhost"])


def test_normalise_rejects_domain_with_spaces():
    with pytest.raises(EmailDomainError):
        normalise_email_domains(["exam ple.com"])


def test_normalise_rejects_domain_with_at_sign():
    with pytest.raises(EmailDomainError):
        normalise_email_domains(["user@example.com"])


def test_normalise_accepts_subdomain():
    result = normalise_email_domains(["mail.example.com"])
    assert result == ["mail.example.com"]


def test_normalise_accepts_hyphenated_domain():
    result = normalise_email_domains(["my-company.co.uk"])
    assert result == ["my-company.co.uk"]


def test_normalise_accepts_multiple_valid_domains():
    result = normalise_email_domains(["acme.com", "widgets.org", "test.net"])
    assert result == ["acme.com", "widgets.org", "test.net"]


# ---------------------------------------------------------------------------
# parse_email_domain_text
# ---------------------------------------------------------------------------


def test_parse_none_returns_empty():
    assert parse_email_domain_text(None) == []


def test_parse_empty_string_returns_empty():
    assert parse_email_domain_text("") == []


def test_parse_comma_separated():
    result = parse_email_domain_text("alpha.com, beta.org, gamma.net")
    assert result == ["alpha.com", "beta.org", "gamma.net"]


def test_parse_newline_separated():
    result = parse_email_domain_text("alpha.com\nbeta.org\ngamma.net")
    assert result == ["alpha.com", "beta.org", "gamma.net"]


def test_parse_mixed_separators():
    result = parse_email_domain_text("alpha.com,beta.org\ngamma.net")
    assert result == ["alpha.com", "beta.org", "gamma.net"]


def test_parse_deduplicates():
    result = parse_email_domain_text("alpha.com, alpha.com, ALPHA.COM")
    assert result == ["alpha.com"]


def test_parse_strips_whitespace_around_entries():
    result = parse_email_domain_text("  alpha.com  ,  beta.org  ")
    assert result == ["alpha.com", "beta.org"]


def test_parse_ignores_blank_lines():
    result = parse_email_domain_text("alpha.com\n\nbeta.org\n")
    assert result == ["alpha.com", "beta.org"]


def test_parse_raises_on_invalid_domain():
    with pytest.raises(EmailDomainError):
        parse_email_domain_text("good.com, -bad.com")


def test_parse_single_domain_no_separator():
    result = parse_email_domain_text("example.com")
    assert result == ["example.com"]
