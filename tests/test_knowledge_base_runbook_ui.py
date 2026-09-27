from pathlib import Path


def test_runbook_editor_exposes_lifecycle_review_asset_and_attachment_controls():
    template = Path("app/templates/admin/knowledge_base_editor.html").read_text()
    script = Path("app/static/js/knowledge_base_admin.js").read_text()

    for control in (
        "kb-article-owner", "kb-article-lifecycle", "kb-article-review-due",
        "kb-article-assets", "data-kb-upload", "data-kb-preview", "data-kb-versions",
    ):
        assert control in template
    for field in ("owner_id", "lifecycle_status", "review_due_at", "asset_ids"):
        assert field in script
    assert "/customer-preview" in script
    assert "/versions" in script


def test_catalogue_and_asset_page_surface_runbook_review_state():
    catalogue = Path("app/templates/admin/knowledge_base.html").read_text()
    asset = Path("app/templates/assets/detail.html").read_text()

    assert 'data-column-key="review"' in catalogue
    assert "article.lifecycle_status" in asset
    assert "article.review_due_at_iso" in asset


def test_customer_preview_does_not_request_unpublished_content():
    route = Path("app/api/routes/knowledge_base.py").read_text()
    preview = route.split("async def preview_article_for_customer", 1)[1].split("@router.post", 1)[0]
    assert "include_unpublished=False" in preview


def test_create_payload_keeps_customer_role_audience():
    from app.schemas.knowledge_base import KnowledgeBaseArticleCreate

    payload = KnowledgeBaseArticleCreate(
        slug="vpn", title="VPN", permission_scope="company",
        allowed_company_ids=[1], allowed_role_ids=[3],
    )
    assert payload.dict()["allowed_role_ids"] == [3]


def test_editor_saves_in_place_and_supports_section_reordering():
    script = Path("app/static/js/knowledge_base_admin.js").read_text()

    assert "window.location.reload" not in script
    assert "history.replaceState" in script
    assert "dragstart" in script and "data-kb-drag-handle" in script
    assert "ensurePreviewMatchesForm" not in script
