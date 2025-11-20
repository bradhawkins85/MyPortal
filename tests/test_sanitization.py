from app.services.sanitization import sanitize_rich_text


def test_sanitize_rich_text_keeps_images_with_safe_attributes():
    content = '<img src="https://example.com/image.png" alt="Example" />'

    result = sanitize_rich_text(content)

    assert "<img" in result.html
    assert 'src="https://example.com/image.png"' in result.html
    assert "alt=\"Example\"" in result.html
    assert result.text_content == ""
    assert result.has_rich_content is True


def test_sanitize_rich_text_strips_unsafe_image_attributes():
    content = '<img src="javascript:alert(1)" onerror="alert(2)" />'

    result = sanitize_rich_text(content)

    assert "javascript" not in result.html
    assert "onerror" not in result.html
    assert result.has_rich_content is False


def test_sanitize_rich_text_marks_plain_text_as_content():
    result = sanitize_rich_text("Hello world")

    assert result.html == "Hello world"
    assert result.text_content == "Hello world"
    assert result.has_rich_content is True


def test_sanitize_rich_text_strips_quoted_email_headers_and_styles():
    content = """
        <p>Latest reply</p>
        <style>P { margin-top: 0; margin-bottom: 0; }</style>
        <p>From: Example Sender &lt;sender@example.com&gt;</p>
        <p>Sent: Monday, 1 January 2025 10:00</p>
        <p>To: Support Team &lt;support@example.com&gt;</p>
        <p>Subject: RE: Ticket update</p>
        <p>Earlier thread content that should be trimmed</p>
    """

    result = sanitize_rich_text(content)

    assert "Latest reply" in result.html
    assert "Earlier thread content" not in result.html
    assert "From:" not in result.html
    assert "Sent:" not in result.html
    assert "Subject:" not in result.html
    assert "margin-top" not in result.html
