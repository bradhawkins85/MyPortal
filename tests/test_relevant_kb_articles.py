"""Tests for finding relevant knowledge base articles based on ticket AI tags."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.repositories import knowledge_base as kb_repo


@pytest.mark.asyncio
class TestRelevantArticleFinding:
    """Test suite for finding relevant knowledge base articles."""

    async def test_find_relevant_articles_with_matching_tags(self):
        """Test finding articles with matching AI tags."""
        # Setup: Mock articles with AI tags
        mock_articles = [
            {
                "id": 1,
                "slug": "printer-troubleshooting",
                "title": "Printer Troubleshooting Guide",
                "summary": "How to fix common printer issues",
                "ai_tags": ["printer", "troubleshooting", "hardware"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
            {
                "id": 2,
                "slug": "network-setup",
                "title": "Network Setup Guide",
                "summary": "Setting up network connections",
                "ai_tags": ["network", "setup", "configuration"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 2, tzinfo=timezone.utc),
            },
            {
                "id": 3,
                "slug": "printer-installation",
                "title": "Printer Installation",
                "summary": "Installing a new printer",
                "ai_tags": ["printer", "installation", "setup"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 3, tzinfo=timezone.utc),
            },
        ]

        # Mock the list_articles function
        with patch.object(kb_repo, 'list_articles', new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_articles

            # Test with ticket tags that match some articles
            ticket_tags = ["printer", "troubleshooting"]
            results = await kb_repo.find_relevant_articles_for_ticket(
                ticket_ai_tags=ticket_tags,
                min_matching_tags=1,
            )

            # Should find articles with at least 1 matching tag
            assert len(results) == 2
            assert results[0]["id"] == 1  # Has 2 matching tags
            assert results[0]["matching_tags_count"] == 2
            assert results[1]["id"] == 3  # Has 1 matching tag
            assert results[1]["matching_tags_count"] == 1

    async def test_find_relevant_articles_with_threshold(self):
        """Test finding articles with minimum tag threshold."""
        mock_articles = [
            {
                "id": 1,
                "slug": "article-1",
                "title": "Article 1",
                "summary": "Test article",
                "ai_tags": ["printer", "troubleshooting", "hardware"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
            {
                "id": 2,
                "slug": "article-2",
                "title": "Article 2",
                "summary": "Test article",
                "ai_tags": ["printer"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 2, tzinfo=timezone.utc),
            },
        ]

        with patch.object(kb_repo, 'list_articles', new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_articles

            ticket_tags = ["printer", "troubleshooting"]
            
            # With threshold of 2, only article 1 should match
            results = await kb_repo.find_relevant_articles_for_ticket(
                ticket_ai_tags=ticket_tags,
                min_matching_tags=2,
            )

            assert len(results) == 1
            assert results[0]["id"] == 1
            assert results[0]["matching_tags_count"] == 2

    async def test_find_relevant_articles_excludes_with_excluded_tags(self):
        """Test that articles with excluded tags are filtered out."""
        mock_articles = [
            {
                "id": 1,
                "slug": "article-1",
                "title": "Article 1",
                "summary": "Test article",
                "ai_tags": ["printer", "troubleshooting"],
                "excluded_ai_tags": ["error"],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
            {
                "id": 2,
                "slug": "article-2",
                "title": "Article 2",
                "summary": "Test article",
                "ai_tags": ["printer", "installation"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 2, tzinfo=timezone.utc),
            },
        ]

        with patch.object(kb_repo, 'list_articles', new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_articles

            # Ticket has "error" tag, should exclude article 1
            ticket_tags = ["printer", "error"]
            results = await kb_repo.find_relevant_articles_for_ticket(
                ticket_ai_tags=ticket_tags,
                min_matching_tags=1,
            )

            assert len(results) == 1
            assert results[0]["id"] == 2

    async def test_find_relevant_articles_case_insensitive(self):
        """Test that tag matching is case-insensitive."""
        mock_articles = [
            {
                "id": 1,
                "slug": "article-1",
                "title": "Article 1",
                "summary": "Test article",
                "ai_tags": ["PRINTER", "Troubleshooting"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
        ]

        with patch.object(kb_repo, 'list_articles', new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_articles

            ticket_tags = ["printer", "troubleshooting"]
            results = await kb_repo.find_relevant_articles_for_ticket(
                ticket_ai_tags=ticket_tags,
                min_matching_tags=1,
            )

            assert len(results) == 1
            assert results[0]["matching_tags_count"] == 2

    async def test_find_relevant_articles_empty_tags(self):
        """Test with empty ticket tags."""
        results = await kb_repo.find_relevant_articles_for_ticket(
            ticket_ai_tags=[],
            min_matching_tags=1,
        )

        assert len(results) == 0

    async def test_find_relevant_articles_no_matches(self):
        """Test when no articles match the ticket tags."""
        mock_articles = [
            {
                "id": 1,
                "slug": "article-1",
                "title": "Article 1",
                "summary": "Test article",
                "ai_tags": ["network", "setup"],
                "excluded_ai_tags": [],
                "is_published": True,
                "updated_at_utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
        ]

        with patch.object(kb_repo, 'list_articles', new_callable=AsyncMock) as mock_list:
            mock_list.return_value = mock_articles

            ticket_tags = ["printer", "troubleshooting"]
            results = await kb_repo.find_relevant_articles_for_ticket(
                ticket_ai_tags=ticket_tags,
                min_matching_tags=1,
            )

            assert len(results) == 0
