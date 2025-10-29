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
