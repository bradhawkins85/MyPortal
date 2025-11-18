"""Tests for knowledge base section-level company permissions."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.services.knowledge_base import (
    ArticleAccessContext,
    _section_visible,
    _serialise_article,
)


class TestSectionVisibility:
    """Tests for section visibility based on company permissions."""

    def test_section_without_restrictions_visible_to_all(self) -> None:
        """Test that sections without company restrictions are visible to everyone."""
        section = {"allowed_company_ids": []}
        
        # Should be visible to anonymous users
        assert _section_visible(section, None) is True
        
        # Should be visible to regular users
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={},
        )
        assert _section_visible(section, context) is True

    def test_section_with_restrictions_hidden_from_anonymous(self) -> None:
        """Test that restricted sections are hidden from anonymous users."""
        section = {"allowed_company_ids": [1, 2]}
        
        assert _section_visible(section, None) is False

    def test_section_visible_to_company_member(self) -> None:
        """Test that sections are visible to members of allowed companies."""
        section = {"allowed_company_ids": [1, 2]}
        
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={
                1: {"company_id": 1, "company_name": "Company A"},
            },
        )
        
        assert _section_visible(section, context) is True

    def test_section_hidden_from_non_member(self) -> None:
        """Test that sections are hidden from non-members."""
        section = {"allowed_company_ids": [1, 2]}
        
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={
                3: {"company_id": 3, "company_name": "Company C"},
            },
        )
        
        assert _section_visible(section, context) is False

    def test_section_visible_to_super_admin(self) -> None:
        """Test that super admins can see all sections."""
        section = {"allowed_company_ids": [1, 2]}
        
        context = ArticleAccessContext(
            user={"id": 1, "is_super_admin": True},
            user_id=1,
            is_super_admin=True,
            memberships={},
        )
        
        assert _section_visible(section, context) is True

    def test_section_visible_to_any_allowed_company(self) -> None:
        """Test that section is visible if user is in any allowed company."""
        section = {"allowed_company_ids": [1, 2, 3]}
        
        # User in company 2
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={
                2: {"company_id": 2, "company_name": "Company B"},
                4: {"company_id": 4, "company_name": "Company D"},
            },
        )
        
        assert _section_visible(section, context) is True


class TestArticleSerializationWithSectionPermissions:
    """Tests for article serialization with section-level permissions."""

    def test_admin_view_includes_all_sections(self) -> None:
        """Test that admin view includes all sections regardless of permissions."""
        article = {
            "id": 1,
            "slug": "test-article",
            "title": "Test Article",
            "summary": "Test summary",
            "ai_tags": [],
            "excluded_ai_tags": [],
            "permission_scope": "anonymous",
            "is_published": True,
            "updated_at_utc": None,
            "published_at_utc": None,
            "created_by": None,
            "created_at_utc": None,
            "sections": [
                {
                    "id": 1,
                    "heading": "Public Section",
                    "content": "<p>Public content</p>",
                    "position": 1,
                    "allowed_company_ids": [],
                },
                {
                    "id": 2,
                    "heading": "Restricted Section",
                    "content": "<p>Restricted content</p>",
                    "position": 2,
                    "allowed_company_ids": [1],
                },
            ],
        }
        
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={
                2: {"company_id": 2, "company_name": "Company B"},
            },
        )
        
        # Admin view should include all sections
        result = _serialise_article(
            article,
            include_content=True,
            include_permissions=True,
            context=context,
        )
        
        assert len(result["sections"]) == 2
        assert result["sections"][0]["heading"] == "Public Section"
        assert result["sections"][1]["heading"] == "Restricted Section"

    def test_user_view_filters_restricted_sections(self) -> None:
        """Test that user view filters out restricted sections."""
        article = {
            "id": 1,
            "slug": "test-article",
            "title": "Test Article",
            "summary": "Test summary",
            "ai_tags": [],
            "excluded_ai_tags": [],
            "permission_scope": "anonymous",
            "is_published": True,
            "updated_at_utc": None,
            "published_at_utc": None,
            "created_by": None,
            "created_at_utc": None,
            "sections": [
                {
                    "id": 1,
                    "heading": "Public Section",
                    "content": "<p>Public content</p>",
                    "position": 1,
                    "allowed_company_ids": [],
                },
                {
                    "id": 2,
                    "heading": "Restricted Section",
                    "content": "<p>Restricted content</p>",
                    "position": 2,
                    "allowed_company_ids": [1],
                },
            ],
        }
        
        # User in company 2 (not allowed to see section 2)
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={
                2: {"company_id": 2, "company_name": "Company B"},
            },
        )
        
        # User view should filter sections
        result = _serialise_article(
            article,
            include_content=True,
            include_permissions=False,
            context=context,
        )
        
        assert len(result["sections"]) == 1
        assert result["sections"][0]["heading"] == "Public Section"

    def test_user_view_shows_allowed_sections(self) -> None:
        """Test that user view shows sections they have access to."""
        article = {
            "id": 1,
            "slug": "test-article",
            "title": "Test Article",
            "summary": "Test summary",
            "ai_tags": [],
            "excluded_ai_tags": [],
            "permission_scope": "anonymous",
            "is_published": True,
            "updated_at_utc": None,
            "published_at_utc": None,
            "created_by": None,
            "created_at_utc": None,
            "sections": [
                {
                    "id": 1,
                    "heading": "Public Section",
                    "content": "<p>Public content</p>",
                    "position": 1,
                    "allowed_company_ids": [],
                },
                {
                    "id": 2,
                    "heading": "Company 1 Section",
                    "content": "<p>Company 1 content</p>",
                    "position": 2,
                    "allowed_company_ids": [1],
                },
                {
                    "id": 3,
                    "heading": "Company 2 Section",
                    "content": "<p>Company 2 content</p>",
                    "position": 3,
                    "allowed_company_ids": [2],
                },
            ],
        }
        
        # User in company 1
        context = ArticleAccessContext(
            user={"id": 1},
            user_id=1,
            is_super_admin=False,
            memberships={
                1: {"company_id": 1, "company_name": "Company A"},
            },
        )
        
        result = _serialise_article(
            article,
            include_content=True,
            include_permissions=False,
            context=context,
        )
        
        assert len(result["sections"]) == 2
        assert result["sections"][0]["heading"] == "Public Section"
        assert result["sections"][1]["heading"] == "Company 1 Section"

    def test_anonymous_user_sees_only_unrestricted_sections(self) -> None:
        """Test that anonymous users only see unrestricted sections."""
        article = {
            "id": 1,
            "slug": "test-article",
            "title": "Test Article",
            "summary": "Test summary",
            "ai_tags": [],
            "excluded_ai_tags": [],
            "permission_scope": "anonymous",
            "is_published": True,
            "updated_at_utc": None,
            "published_at_utc": None,
            "created_by": None,
            "created_at_utc": None,
            "sections": [
                {
                    "id": 1,
                    "heading": "Public Section",
                    "content": "<p>Public content</p>",
                    "position": 1,
                    "allowed_company_ids": [],
                },
                {
                    "id": 2,
                    "heading": "Restricted Section",
                    "content": "<p>Restricted content</p>",
                    "position": 2,
                    "allowed_company_ids": [1],
                },
            ],
        }
        
        result = _serialise_article(
            article,
            include_content=True,
            include_permissions=False,
            context=None,
        )
        
        assert len(result["sections"]) == 1
        assert result["sections"][0]["heading"] == "Public Section"
