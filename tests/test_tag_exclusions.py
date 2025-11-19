import pytest

from app.services.tagging import (
    filter_helpful_slugs,
    filter_helpful_texts,
    is_helpful_slug,
    slugify_tag,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_is_helpful_slug_with_exclusions():
    """Test that is_helpful_slug respects custom exclusion set."""
    excluded = {"excluded-tag", "another-excluded"}
    
    assert is_helpful_slug("microsoft-office", excluded) is True
    assert is_helpful_slug("excluded-tag", excluded) is False
    assert is_helpful_slug("another-excluded", excluded) is False
    # Should still respect default rules
    assert is_helpful_slug("ab", excluded) is False  # Too short
    assert is_helpful_slug("123", excluded) is False  # Only digits


def test_is_helpful_slug_without_exclusions():
    """Test that is_helpful_slug works without custom exclusions."""
    assert is_helpful_slug("microsoft-office") is True
    assert is_helpful_slug("json") is False  # Default exclusion
    assert is_helpful_slug("tags") is False  # Default exclusion


def test_filter_helpful_slugs_with_exclusions():
    """Test filtering slugs with custom exclusions."""
    tags = ["microsoft-office", "excluded-tag", "email-issue", "excluded-tag"]
    excluded = {"excluded-tag"}
    
    result = filter_helpful_slugs(tags, excluded)
    
    assert result == ["microsoft-office", "email-issue"]


def test_filter_helpful_slugs_without_exclusions():
    """Test filtering slugs without custom exclusions."""
    tags = ["microsoft-office", "json", "email-issue", "tags"]
    
    result = filter_helpful_slugs(tags)
    
    assert result == ["microsoft-office", "email-issue"]


def test_filter_helpful_texts_with_exclusions():
    """Test filtering text tags with custom exclusions."""
    tags = ["Microsoft Office", "EXCLUDED TAG", "Email Issue"]
    excluded = {"excluded-tag"}
    
    result = filter_helpful_texts(tags, excluded)
    
    assert result == ["microsoft office", "email issue"]


def test_filter_helpful_texts_without_exclusions():
    """Test filtering text tags without custom exclusions."""
    tags = ["Microsoft Office", "JSON", "Email Issue", "Tags"]
    
    result = filter_helpful_texts(tags)
    
    assert result == ["microsoft office", "email issue"]


def test_slugify_tag_normalization():
    """Test that tag slugification works correctly."""
    assert slugify_tag("Valid Tag") == "valid-tag"
    assert slugify_tag("UPPERCASE TAG") == "uppercase-tag"
    assert slugify_tag("Special!@#Characters") == "specialcharacters"
    assert slugify_tag("Multiple   Spaces") == "multiple-spaces"
    assert slugify_tag("--dashes--") == "dashes"


def test_is_helpful_slug_rejects_tags_containing_excluded_words():
    """Test that tags containing excluded words as components are rejected.
    
    This addresses the issue where tags like 'tag-m365' should be rejected
    if 'tag' is an excluded word, even though the full slug isn't exactly 'tag'.
    """
    excluded = {"tag", "tags"}
    
    # Tags containing excluded words should be rejected
    assert is_helpful_slug("tag-m365", excluded) is False
    assert is_helpful_slug("tags-m365", excluded) is False
    assert is_helpful_slug("m365-tag", excluded) is False
    assert is_helpful_slug("m365-tags", excluded) is False
    assert is_helpful_slug("tag-office-365", excluded) is False
    assert is_helpful_slug("office-tags-365", excluded) is False
    
    # Tags without excluded words should be accepted
    assert is_helpful_slug("m365", excluded) is True
    assert is_helpful_slug("office-365", excluded) is True
    assert is_helpful_slug("microsoft-office", excluded) is True
    
    # Partial matches within a word should NOT trigger exclusion
    # (e.g., "storage" contains "tag" but shouldn't be excluded)
    assert is_helpful_slug("storage", excluded) is True
    assert is_helpful_slug("advantage", excluded) is True
    assert is_helpful_slug("hostage", excluded) is True


def test_filter_helpful_slugs_with_compound_exclusions():
    """Test filtering slugs where excluded words appear as components."""
    excluded = {"tag", "tags"}
    
    tags = [
        "tag-m365",      # Should be excluded - contains 'tag'
        "tags-m365",     # Should be excluded - contains 'tags'
        "m365",          # Should be kept
        "m365-tag",      # Should be excluded - contains 'tag'
        "office-365",    # Should be kept
        "tag",           # Should be excluded - exact match
        "tags",          # Should be excluded - exact match
    ]
    
    result = filter_helpful_slugs(tags, excluded)
    
    assert result == ["m365", "office-365"]


def test_filter_helpful_texts_with_compound_exclusions():
    """Test filtering text tags where excluded words appear as components."""
    excluded = {"tag", "tags"}
    
    tags = [
        "tag m365",      # Should be excluded
        "tags m365",     # Should be excluded
        "m365",          # Should be kept
        "m365 tag",      # Should be excluded
        "Office 365",    # Should be kept
    ]
    
    result = filter_helpful_texts(tags, excluded)
    
    # Normalized lowercase versions
    assert result == ["m365", "office 365"]
