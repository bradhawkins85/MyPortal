from pathlib import Path


def test_ai_tag_synonyms_admin_menu_link_is_visible():
    template = Path("app/templates/base.html").read_text()

    assert "/admin/chat/ai-tag-synonyms" in template
    assert "AI Tag Synonyms" in template


def test_ai_tag_synonyms_page_targets_admin_ui_routes():
    template = Path("app/templates/admin/matrix_chat_configuration.html").read_text()

    assert "AI Tag Synonyms" in template
    assert "/api/chat/ai-tag-synonyms" in template
    assert "action=\"/admin/chat/ai-tag-synonyms\"" in template
    assert "action=\"/admin/chat/ai-tag-synonyms/{{ group.id }}\"" in template
    assert "action=\"/admin/chat/ai-tag-synonyms/{{ group.id }}/delete\"" in template
