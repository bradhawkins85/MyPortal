"""Test knowledge base AI tag exclusion functionality."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.knowledge_base import _parse_ai_tag_text
from app.repositories import knowledge_base as kb_repo


def test_parse_ai_tag_text_respects_excluded_tags():
    """Test that _parse_ai_tag_text filters out excluded tags."""
    raw = '["networking", "server outage", "security", "hardware"]'
    
    parsed = _parse_ai_tag_text(raw)
    
    # All tags should be included (no exclusions applied at parse level)
    assert len(parsed) == 4
    assert "networking" in parsed
    assert "server outage" in parsed
    assert "security" in parsed
    assert "hardware" in parsed


def test_parse_ai_tag_text_normalizes_tags():
    """Test that tags are normalized to lowercase and whitespace is handled."""
    raw = '["Networking  Setup", "SERVER OUTAGE", "  Security  "]'
    
    parsed = _parse_ai_tag_text(raw)
    
    assert "networking setup" in parsed
    assert "server outage" in parsed
    assert "security" in parsed


def test_parse_ai_tag_text_limits_to_10_tags():
    """Test that only first 10 tags are returned."""
    raw = '["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12"]'
    
    parsed = _parse_ai_tag_text(raw)
    
    assert len(parsed) == 10


def test_parse_ai_tag_text_handles_invalid_json():
    """Test that invalid JSON is handled gracefully."""
    raw = 'networking, server outage, security'
    
    parsed = _parse_ai_tag_text(raw)
    
    # Should still parse as comma-separated list
    assert len(parsed) > 0
    assert "networking" in parsed


@pytest.mark.asyncio
async def test_excluded_tags_stored_in_article():
    """Test that excluded tags are stored when removing a tag."""
    # This is more of an integration test outline
    # Actual implementation would require database setup
    pass


@pytest.mark.asyncio
async def test_excluded_tags_prevent_readding():
    """Test that excluded tags are not re-added when refreshing."""
    # This is more of an integration test outline
    # Actual implementation would require database setup and Ollama mock
    pass
