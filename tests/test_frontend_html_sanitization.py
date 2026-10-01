from pathlib import Path


def test_message_template_editor_sanitizes_rich_html_before_dom_preview():
    script = Path("app/static/js/message_templates.js").read_text()

    assert "function sanitizeHtml(html)" in script
    assert "window.DOMPurify" in script
    assert "HTML_BLOCKED_TAGS" in script
    assert "FORBID_TAGS: Array.from(HTML_BLOCKED_TAGS)" in script
    assert "parseFromString(`<body>${sanitizeHtml(html)}</body>`, 'text/html')" in script


def test_knowledge_base_admin_sanitizes_rich_content_before_rendering():
    script = Path("app/static/js/knowledge_base_admin.js").read_text()

    assert "function sanitizeRichHtml(html)" in script
    assert "RICH_HTML_BLOCKED_TAGS" in script
    assert "window.DOMPurify" in script
    assert "setSanitizedHtml(editor, section && section.content ? section.content : '<p><br></p>');" in script
    assert "setSanitizedHtml(contentNode, version.content || '');" in script


def test_admin_views_load_purify_for_rich_html_sanitization():
    kb_editor = Path("app/templates/admin/knowledge_base_editor.html").read_text()
    mt_list = Path("app/templates/admin/message_templates.html").read_text()
    mt_form = Path("app/templates/admin/message_template_form.html").read_text()

    assert "static/js/purify.min.js" in kb_editor
    assert "static/js/purify.min.js" in mt_list
    assert "static/js/purify.min.js" in mt_form
