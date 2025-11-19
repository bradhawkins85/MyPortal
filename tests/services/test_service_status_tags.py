"""Tests for service status tag parsing and serialization."""
from app.services.service_status import _parse_tags, _serialize_tags


def test_parse_tags_from_comma_separated_string():
    """Test parsing tags from a comma-separated string."""
    tags = _parse_tags("monitoring, cloud, infrastructure")
    assert tags == ["monitoring", "cloud", "infrastructure"]


def test_parse_tags_from_list():
    """Test parsing tags from a list."""
    tags = _parse_tags(["Monitoring", "Cloud", "Infrastructure"])
    assert tags == ["monitoring", "cloud", "infrastructure"]


def test_parse_tags_handles_empty_input():
    """Test that empty input returns empty list."""
    assert _parse_tags("") == []
    assert _parse_tags(None) == []
    assert _parse_tags([]) == []


def test_parse_tags_filters_long_tags():
    """Test that very long tags are filtered out."""
    long_tag = "a" * 51
    tags = _parse_tags(f"valid, {long_tag}, another")
    assert "valid" in tags
    assert "another" in tags
    assert long_tag.lower() not in tags


def test_parse_tags_strips_whitespace():
    """Test that extra whitespace is removed."""
    tags = _parse_tags("  monitoring  ,  cloud  ,  infrastructure  ")
    assert tags == ["monitoring", "cloud", "infrastructure"]


def test_serialize_tags_creates_comma_separated_string():
    """Test serializing a list of tags to a comma-separated string."""
    result = _serialize_tags(["monitoring", "cloud", "infrastructure"])
    assert result == "monitoring, cloud, infrastructure"


def test_serialize_tags_handles_empty_list():
    """Test that empty list returns empty string."""
    assert _serialize_tags([]) == ""
    assert _serialize_tags(None) == ""


def test_serialize_tags_filters_empty_tags():
    """Test that empty or whitespace-only tags are filtered out."""
    result = _serialize_tags(["monitoring", "", "  ", "cloud"])
    assert result == "monitoring, cloud"
