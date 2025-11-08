"""Integration tests for knowledge base conditional logic."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.knowledge_base import (
    ArticleAccessContext,
    _serialise_article,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _article_with_conditionals(**overrides):
    """Factory to create test articles with conditional content."""
    now = datetime.now(timezone.utc)
    base = {
        "id": 1,
        "slug": "test-conditional",
        "title": "Test Conditional Article",
        "summary": "Testing conditionals",
        "permission_scope": "anonymous",
        "is_published": True,
        "ai_tags": [],
        "excluded_ai_tags": [],
        "allowed_user_ids": [],
        "company_ids": [],
        "company_admin_ids": [],
        "created_by": 1,
        "created_at_utc": now,
        "updated_at_utc": now,
        "published_at_utc": now,
        "sections": [
            {
                "id": 1,
                "heading": "Introduction",
                "content": "<p>Common content</p>",
                "position": 1,
            },
            {
                "id": 2,
                "heading": "Company Specific",
                "content": (
                    '<p>Default content</p>'
                    '<kb-if company="ACME Corp">'
                    '<p>ACME-specific instructions</p>'
                    '<img src="/acme-logo.png" alt="ACME" />'
                    '</kb-if>'
                    '<kb-if company="XYZ Inc">'
                    '<p>XYZ-specific instructions</p>'
                    '</kb-if>'
                ),
                "position": 2,
            },
        ],
    }
    base.update(overrides)
    return base


def test_conditional_content_rendered_for_matching_company() -> None:
    """Test that conditional content is rendered when company matches."""
    article = _article_with_conditionals()
    
    # Create a context for ACME Corp user
    acme_context = ArticleAccessContext(
        user={"id": 1, "email": "user@acme.com"},
        user_id=1,
        is_super_admin=False,
        memberships={
            1: {"company_id": 1, "company_name": "ACME Corp", "is_admin": False}
        },
    )
    
    # Serialize the article with ACME context
    serialized = _serialise_article(
        article,
        include_content=True,
        include_permissions=False,
        context=acme_context,
    )
    
    sections = serialized.get("sections", [])
    assert len(sections) == 2
    
    # Check that the conditional section has ACME content but not XYZ
    conditional_section = sections[1]
    content = conditional_section["content"]
    
    assert "ACME-specific instructions" in content
    assert 'src="/acme-logo.png"' in content
    assert "XYZ-specific instructions" not in content
    
    # Create a context for XYZ Inc user
    xyz_context = ArticleAccessContext(
        user={"id": 2, "email": "user@xyz.com"},
        user_id=2,
        is_super_admin=False,
        memberships={
            2: {"company_id": 2, "company_name": "XYZ Inc", "is_admin": False}
        },
    )
    
    # Serialize the article with XYZ context
    serialized = _serialise_article(
        article,
        include_content=True,
        include_permissions=False,
        context=xyz_context,
    )
    
    sections = serialized.get("sections", [])
    conditional_section = sections[1]
    content = conditional_section["content"]
    
    # Check that the conditional section has XYZ content but not ACME
    assert "XYZ-specific instructions" in content
    assert "ACME-specific instructions" not in content
    assert 'src="/acme-logo.png"' not in content


def test_conditional_content_removed_for_non_matching_company() -> None:
    """Test that conditional content is removed when company doesn't match."""
    article = _article_with_conditionals(
        sections=[
            {
                "id": 1,
                "heading": "Test",
                "content": (
                    '<p>Before</p>'
                    '<kb-if company="Other Company">'
                    '<p>Hidden content</p>'
                    '</kb-if>'
                    '<p>After</p>'
                ),
                "position": 1,
            },
        ],
    )
    
    # Create a context for a user from a different company
    context = ArticleAccessContext(
        user={"id": 1, "email": "user@acme.com"},
        user_id=1,
        is_super_admin=False,
        memberships={
            1: {"company_id": 1, "company_name": "ACME Corp", "is_admin": False}
        },
    )
    
    serialized = _serialise_article(
        article,
        include_content=True,
        include_permissions=False,
        context=context,
    )
    
    sections = serialized.get("sections", [])
    content = sections[0]["content"]
    
    # Hidden content should not be present
    assert "Hidden content" not in content
    # But the surrounding content should still be there
    assert "Before" in content
    assert "After" in content


def test_admin_view_shows_all_conditional_blocks() -> None:
    """Test that admin view includes all conditional blocks without processing."""
    article = _article_with_conditionals(
        sections=[
            {
                "id": 1,
                "heading": "Test",
                "content": (
                    '<kb-if company="Company A">Content A</kb-if>'
                    '<kb-if company="Company B">Content B</kb-if>'
                ),
                "position": 1,
            },
        ],
    )
    
    # Create a super admin context
    admin_context = ArticleAccessContext(
        user={"id": 1, "email": "admin@example.com", "is_super_admin": True},
        user_id=1,
        is_super_admin=True,
        memberships={},
    )
    
    # Serialize with include_permissions=True (admin edit mode)
    serialized = _serialise_article(
        article,
        include_content=True,
        include_permissions=True,
        context=admin_context,
    )
    
    sections = serialized.get("sections", [])
    content = sections[0]["content"]
    
    # Admin should see all conditional blocks unprocessed
    assert '<kb-if company="Company A">Content A</kb-if>' in content
    assert '<kb-if company="Company B">Content B</kb-if>' in content


def test_case_insensitive_company_matching() -> None:
    """Test that company name matching is case-insensitive."""
    article = _article_with_conditionals(
        sections=[
            {
                "id": 1,
                "heading": "Test",
                "content": '<kb-if company="Acme Corp">ACME content</kb-if>',
                "position": 1,
            },
        ],
    )
    
    # Create context with differently-cased company name
    context = ArticleAccessContext(
        user={"id": 1, "email": "user@acme.com"},
        user_id=1,
        is_super_admin=False,
        memberships={
            1: {"company_id": 1, "company_name": "acme corp", "is_admin": False}
        },
    )
    
    serialized = _serialise_article(
        article,
        include_content=True,
        include_permissions=False,
        context=context,
    )
    
    sections = serialized.get("sections", [])
    content = sections[0]["content"]
    
    # Content should match despite case difference
    assert "ACME content" in content
    assert "kb-if" not in content  # Tag should be removed


def test_no_company_context_removes_all_conditionals() -> None:
    """Test that users with no company see no conditional content."""
    article = _article_with_conditionals()
    
    # Create context with no company memberships
    context = ArticleAccessContext(
        user={"id": 1, "email": "user@example.com"},
        user_id=1,
        is_super_admin=False,
        memberships={},
    )
    
    serialized = _serialise_article(
        article,
        include_content=True,
        include_permissions=False,
        context=context,
    )
    
    sections = serialized.get("sections", [])
    conditional_section = sections[1]
    content = conditional_section["content"]
    
    # No conditional content should be present
    assert "ACME-specific instructions" not in content
    assert "XYZ-specific instructions" not in content
    # But default content should still be there
    assert "Default content" in content

