from unittest.mock import AsyncMock

import pytest

from app.services import knowledge_base
from app.security.menu_permissions import compact_menu_permissions


@pytest.mark.anyio
async def test_company_article_requires_content_permission_and_record_audience(monkeypatch):
    article = {
        "id": 41,
        "permission_scope": "company",
        "company_ids": [7],
        "company_admin_ids": [],
    }
    membership = {"company_id": 7, "role_id": 3, "menu_permissions": {"content.knowledge_base": "read"}}
    context = knowledge_base.ArticleAccessContext(user={"id": 9}, user_id=9, is_super_admin=False, memberships={7: membership})
    allowed = AsyncMock(return_value=True)
    monkeypatch.setattr(knowledge_base.audience_repo, "role_can_access", allowed)

    assert knowledge_base._article_visible(article, context) is True
    assert await knowledge_base._role_audience_visible(article, context) is True
    allowed.assert_awaited_once_with(7, "knowledge_base", 41, 3)

    membership["menu_permissions"] = {"content.knowledge_base": "none"}
    assert await knowledge_base._role_audience_visible(article, context) is False


def test_customer_content_permissions_are_assignable_role_permissions():
    permissions = compact_menu_permissions({"content.knowledge_base": "read", "content.assets": "write"})
    assert permissions == {"content.knowledge_base": "read", "content.assets": "write"}
