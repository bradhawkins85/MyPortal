import pytest

from app.services.rag_urls import canonical_source_url


@pytest.mark.parametrize(
    ("source_type", "source_id", "metadata", "expected"),
    [
        ("knowledge_base", "7", {"slug": "reset-password"}, "/knowledge-base/articles/reset-password"),
        ("tickets", "7", {}, "/admin/tickets/7"),
        ("assets", "7", {}, "/admin/assets/7"),
        ("companies", "7", {}, "/admin/companies/7"),
        ("staff", "7", {}, "/admin/staff/7"),
        ("chats", "room 7", {}, "/chat/room%207"),
        ("issues", "7", {}, "/admin/issues/7"),
        ("products", "7", {}, "/shop/admin/product/7"),
        ("orders", "7", {"order_number": "PO 7"}, "/orders?search=PO+7"),
        ("reports", "7", {}, "/reports/company-overview"),
    ],
)
def test_canonical_source_url_resolves_linkable_sources(
    source_type, source_id, metadata, expected
):
    assert canonical_source_url(source_type, source_id, metadata=metadata) == expected


def test_canonical_source_url_rejects_external_indexed_url():
    assert canonical_source_url(
        "tickets", "8", supplied_url="https://evil.example/ticket"
    ) == "/admin/tickets/8"


def test_canonical_source_url_keeps_safe_local_indexed_url():
    assert canonical_source_url(
        "tickets", "8", supplied_url="/custom/tickets/8"
    ) == "/custom/tickets/8"
