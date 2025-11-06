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
    
    assert is_helpful_slug("valid-tag", excluded) is True
    assert is_helpful_slug("excluded-tag", excluded) is False
    assert is_helpful_slug("another-excluded", excluded) is False
    # Should still respect default rules
    assert is_helpful_slug("ab", excluded) is False  # Too short
    assert is_helpful_slug("123", excluded) is False  # Only digits


def test_is_helpful_slug_without_exclusions():
    """Test that is_helpful_slug works without custom exclusions."""
    assert is_helpful_slug("valid-tag") is True
    assert is_helpful_slug("json") is False  # Default exclusion
    assert is_helpful_slug("tags") is False  # Default exclusion


def test_filter_helpful_slugs_with_exclusions():
    """Test filtering slugs with custom exclusions."""
    tags = ["valid-tag", "excluded-tag", "another-tag", "excluded-tag"]
    excluded = {"excluded-tag"}
    
    result = filter_helpful_slugs(tags, excluded)
    
    assert result == ["valid-tag", "another-tag"]


def test_filter_helpful_slugs_without_exclusions():
    """Test filtering slugs without custom exclusions."""
    tags = ["valid-tag", "json", "another-tag", "tags"]
    
    result = filter_helpful_slugs(tags)
    
    assert result == ["valid-tag", "another-tag"]


def test_filter_helpful_texts_with_exclusions():
    """Test filtering text tags with custom exclusions."""
    tags = ["Valid Tag", "EXCLUDED TAG", "Another Tag"]
    excluded = {"excluded-tag"}
    
    result = filter_helpful_texts(tags, excluded)
    
    assert result == ["valid tag", "another tag"]


def test_filter_helpful_texts_without_exclusions():
    """Test filtering text tags without custom exclusions."""
    tags = ["Valid Tag", "JSON", "Another Tag", "Tags"]
    
    result = filter_helpful_texts(tags)
    
    assert result == ["valid tag", "another tag"]


def test_slugify_tag_normalization():
    """Test that tag slugification works correctly."""
    assert slugify_tag("Valid Tag") == "valid-tag"
    assert slugify_tag("UPPERCASE TAG") == "uppercase-tag"
    assert slugify_tag("Special!@#Characters") == "specialcharacters"
    assert slugify_tag("Multiple   Spaces") == "multiple-spaces"
    assert slugify_tag("--dashes--") == "dashes"
