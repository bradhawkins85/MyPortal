from pathlib import Path


def test_message_template_editor_sanitizes_rich_html_before_dom_preview():
    script = Path("app/static/js/message_templates.js").read_text()

    assert "function sanitizeHtml(html)" in script
    assert "name.startsWith('on')" in script
    assert "HTML_BLOCKED_TAGS" in script
    assert "parseFromString(`<body>${sanitizeHtml(html)}</body>`, 'text/html')" in script


def test_knowledge_base_admin_sanitizes_rich_content_before_rendering():
    script = Path("app/static/js/knowledge_base_admin.js").read_text()

    assert "function sanitizeRichHtml(html)" in script
    assert "RICH_HTML_BLOCKED_TAGS" in script
    assert "name.startsWith('on')" in script
    assert "setSanitizedHtml(editor, section && section.content ? section.content : '<p><br></p>');" in script
    assert "setSanitizedHtml(contentNode, version.content || '');" in script
