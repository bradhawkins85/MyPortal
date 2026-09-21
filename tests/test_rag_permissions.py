import pytest

from app.services import rag_index
from app.services.rag_permissions import can_access_candidate


def candidate(scope):
    return {"source_type": "knowledge_base", "permission_scope": scope}


def test_unknown_and_legacy_scopes_fail_closed_even_for_super_admin():
    assert not can_access_candidate(
        candidate({}), user={"id": 1, "is_super_admin": True}, memberships=[]
    )
    assert not can_access_candidate(
        candidate({"version": 1, "visibility": "invented"}),
        user={"id": 1, "is_super_admin": True},
        memberships=[],
    )


def test_anonymous_knowledge_base_scope_is_public():
    scope = rag_index._permission_scope_for_source(
        "knowledge_base", {"article_permission_scope": "anonymous"}
    )
    assert can_access_candidate(candidate(scope), user={}, memberships=[])


def test_company_and_company_admin_scopes_use_current_memberships():
    company_scope = {"version": 1, "visibility": "company", "company_ids": [8]}
    admin_scope = {"version": 1, "visibility": "company_admin", "company_ids": [8]}
    user = {"id": 22}
    assert not can_access_candidate(
        candidate(company_scope), user=user, memberships=[{"company_id": 9}]
    )
    assert can_access_candidate(
        candidate(company_scope), user=user, memberships=[{"company_id": 8}]
    )
    assert not can_access_candidate(
        candidate(admin_scope),
        user=user,
        memberships=[{"company_id": 8, "is_admin": False}],
    )
    assert can_access_candidate(
        candidate(admin_scope),
        user=user,
        memberships=[{"company_id": 8, "is_admin": True}],
    )


def test_user_scope_and_required_feature_permissions_are_reauthorised():
    user_scope = {"version": 1, "visibility": "user", "user_ids": [22]}
    assert can_access_candidate(candidate(user_scope), user={"id": 22}, memberships=[])
    assert not can_access_candidate(
        candidate(user_scope), user={"id": 23}, memberships=[]
    )
    mailbox_scope = {
        "version": 1,
        "visibility": "company",
        "company_ids": [8],
        "required_any": ["can_view_m365_user_mailboxes"],
    }
    assert not can_access_candidate(
        candidate(mailbox_scope), user={"id": 22}, memberships=[{"company_id": 8}]
    )
    assert can_access_candidate(
        candidate(mailbox_scope),
        user={"id": 22},
        memberships=[{"company_id": 8, "can_view_m365_user_mailboxes": True}],
    )


@pytest.mark.parametrize(
    "source_type,item",
    [
        ("knowledge_base", {"article_permission_scope": "anonymous"}),
        ("reports", {}),
        ("service_status", {}),
        ("tickets", {"company_id": 1}),
        ("ticket_comments", {"company_id": 1}),
        ("companies", {"company_id": 1}),
        ("backup_jobs", {"company_id": 1}),
        ("products", {"company_id": 1}),
        ("packages", {}),
        ("chats", {"company_id": 1}),
        ("orders", {"company_id": 1}),
        ("assets", {"company_id": 1}),
        ("staff", {"company_id": 1}),
        ("issues", {"allowed_company_ids": [1]}),
        ("mailboxes", {"company_id": 1}),
        ("best_practices", {"company_id": 1}),
        (
            "feature:demo",
            {"permission_scope": {"version": 1, "visibility": "super_admin"}},
        ),
    ],
)
def test_every_indexed_source_type_has_an_explicit_policy(source_type, item):
    scope = rag_index._permission_scope_for_source(source_type, item)
    assert scope and scope["version"] == 1


def test_unknown_index_source_has_no_policy():
    assert rag_index._permission_scope_for_source("new_sensitive_source", {}) is None
