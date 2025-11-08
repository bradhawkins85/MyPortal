"""Test that weakly related knowledge base articles are filtered out."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import knowledge_base as knowledge_base_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _stub_company_memberships(monkeypatch):
    monkeypatch.setattr(
        knowledge_base_service.company_access,
        "list_accessible_companies",
        AsyncMock(return_value=[]),
    )


def _article_factory(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "id": 1,
        "slug": "public",
        "title": "Public article",
        "summary": "",
        "content": "Public content",
        "sections": [
            {"position": 1, "heading": "Overview", "content": "<p>Public content</p>"}
        ],
        "permission_scope": "anonymous",
        "is_published": True,
        "ai_tags": ["public"],
        "excluded_ai_tags": [],
        "created_by": 1,
        "created_at": now,
        "updated_at": now,
        "published_at": now,
        "created_at_utc": now,
        "updated_at_utc": now,
        "published_at_utc": now,
        "allowed_user_ids": [],
        "company_ids": [],
        "company_admin_ids": [],
    }
    base.update(overrides)
    return base


@pytest.mark.anyio("asyncio")
async def test_search_filters_weakly_related_articles(monkeypatch):
    """
    Test that articles with only weak token overlap are filtered out.
    
    In this scenario, a user searches for "how to configure email server settings",
    and we have several articles:
    1. "Email Server Configuration Guide" - highly relevant (exact match)
    2. "Network Security Best Practices" - weakly related (has "server" token)
    3. "Office Printer Setup" - not related at all
    
    The weakly related article should NOT be returned as it's not actually about email.
    """
    articles = [
        _article_factory(
            id=1,
            slug="email-server-config",
            title="Email Server Configuration Guide",
            summary="Complete guide to setting up and configuring your email server",
            content="<p>This guide covers all aspects of email server configuration including SMTP, IMAP, and security settings.</p>",
            ai_tags=["email", "server", "configuration", "smtp"],
        ),
        _article_factory(
            id=2,
            slug="network-security",
            title="Network Security Best Practices",
            summary="Security hardening for your network infrastructure",
            content="<p>Secure your network infrastructure including routers, switches, and server hardware.</p>",
            ai_tags=["security", "network", "server"],
        ),
        _article_factory(
            id=3,
            slug="printer-setup",
            title="Office Printer Setup",
            summary="Quick guide for installing office printers",
            content="<p>Step by step instructions for setting up network printers in the office.</p>",
            ai_tags=["printer", "setup", "office"],
        ),
    ]

    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "list_articles",
        AsyncMock(return_value=articles),
    )

    context = await knowledge_base_service.build_access_context({"id": 1, "is_super_admin": False})
    result = await knowledge_base_service.search_articles(
        "how to configure email server settings",
        context,
        use_ollama=False
    )

    slugs = [item["slug"] for item in result["results"]]
    
    # The email server article should definitely be included
    assert "email-server-config" in slugs, "Email server article should be in results"
    
    # The network security article should be filtered out - it only matches "server" 
    # which is too weak of a connection to the actual query about email configuration
    assert "network-security" not in slugs, "Weakly related network security article should be filtered out"
    
    # Printer article should definitely not be there
    assert "printer-setup" not in slugs, "Unrelated printer article should not be in results"


@pytest.mark.anyio("asyncio")
async def test_search_single_word_query_with_weak_matches(monkeypatch):
    """
    Test filtering with a single-word query that might match many articles weakly.
    
    When searching for "backup", we want articles specifically about backup,
    not articles that just mention "backup" once in passing.
    """
    articles = [
        _article_factory(
            id=1,
            slug="backup-guide",
            title="Complete Backup Guide",
            summary="Everything you need to know about backups",
            content="<p>This comprehensive backup guide covers backup strategies, backup tools, and backup best practices.</p>",
            ai_tags=["backup", "recovery", "data protection"],
        ),
        _article_factory(
            id=2,
            slug="general-security",
            title="General Security Tips",
            summary="Basic security recommendations",
            content="<p>Enable two-factor authentication, use strong passwords, and keep a backup of important files.</p>",
            ai_tags=["security", "authentication", "passwords"],
        ),
        _article_factory(
            id=3,
            slug="network-config",
            title="Network Configuration",
            summary="Configure your network settings",
            content="<p>Before making changes, backup your current configuration files.</p>",
            ai_tags=["network", "configuration"],
        ),
    ]

    monkeypatch.setattr(
        knowledge_base_service.kb_repo,
        "list_articles",
        AsyncMock(return_value=articles),
    )

    context = await knowledge_base_service.build_access_context({"id": 1, "is_super_admin": False})
    result = await knowledge_base_service.search_articles("backup", context, use_ollama=False)

    slugs = [item["slug"] for item in result["results"]]
    
    # The dedicated backup guide should be included
    assert "backup-guide" in slugs, "Backup guide should be in results"
    
    # Articles that only mention "backup" once or twice in passing should be filtered out
    # They have weak relevance and would confuse users
    assert "general-security" not in slugs, "Article that only mentions backup in passing should be filtered"
    assert "network-config" not in slugs, "Article that only mentions backup in passing should be filtered"
