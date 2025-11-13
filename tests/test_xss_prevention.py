"""Tests for input validation and XSS prevention."""
from __future__ import annotations

import pytest
from app.services.sanitization import sanitize_rich_text


def test_sanitize_rich_text_removes_script_tags():
    """Test that script tags are removed from user input."""
    malicious_input = '<script>alert("XSS")</script><p>Hello</p>'
    result = sanitize_rich_text(malicious_input)
    
    # Script tags should be removed, but content might remain as plain text
    assert "<script>" not in result.html
    assert "</script>" not in result.html
    # The important part is that the script can't execute
    assert "<p>Hello</p>" in result.html


def test_sanitize_rich_text_removes_onclick_attributes():
    """Test that onclick and other event handlers are removed."""
    malicious_input = '<a href="#" onclick="alert(\'XSS\')">Click me</a>'
    result = sanitize_rich_text(malicious_input)
    
    assert "onclick" not in result.html
    assert "alert" not in result.html
    assert '<a href="#">Click me</a>' in result.html


def test_sanitize_rich_text_removes_javascript_protocol():
    """Test that javascript: protocol links are removed."""
    malicious_input = '<a href="javascript:alert(\'XSS\')">Click me</a>'
    result = sanitize_rich_text(malicious_input)
    
    assert "javascript:" not in result.html
    # Link should be removed entirely or href sanitized
    assert "alert" not in result.html


def test_sanitize_rich_text_allows_safe_html():
    """Test that safe HTML tags and attributes are preserved."""
    safe_input = '''
        <h1>Title</h1>
        <p>Paragraph with <strong>bold</strong> and <em>italic</em> text.</p>
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
        <a href="https://example.com">Link</a>
    '''
    result = sanitize_rich_text(safe_input)
    
    assert "<h1>Title</h1>" in result.html
    assert "<strong>bold</strong>" in result.html
    assert "<em>italic</em>" in result.html
    assert "<ul>" in result.html
    assert "<li>Item 1</li>" in result.html
    assert 'href="https://example.com"' in result.html


def test_sanitize_rich_text_removes_style_tags():
    """Test that style tags are removed."""
    malicious_input = '<style>body { display: none; }</style><p>Content</p>'
    result = sanitize_rich_text(malicious_input)
    
    # Style tags should be removed (not executed)
    assert "<style>" not in result.html
    assert "</style>" not in result.html
    # Content should remain
    assert "<p>Content</p>" in result.html


def test_sanitize_rich_text_removes_iframe():
    """Test that iframe tags are removed."""
    malicious_input = '<iframe src="https://evil.com"></iframe><p>Content</p>'
    result = sanitize_rich_text(malicious_input)
    
    assert "<iframe" not in result.html
    assert "evil.com" not in result.html


def test_sanitize_rich_text_removes_object_and_embed():
    """Test that object and embed tags are removed."""
    malicious_input = '<object data="evil.swf"></object><embed src="evil.swf">'
    result = sanitize_rich_text(malicious_input)
    
    assert "<object" not in result.html
    assert "<embed" not in result.html


def test_sanitize_rich_text_handles_svg_xss():
    """Test that SVG-based XSS attempts are blocked."""
    malicious_input = '<svg onload="alert(\'XSS\')"><circle r="10"/></svg>'
    result = sanitize_rich_text(malicious_input)
    
    # SVG is not in allowed tags, should be removed
    assert "<svg" not in result.html
    assert "onload" not in result.html
    assert "alert" not in result.html


def test_sanitize_rich_text_handles_data_uri_xss():
    """Test that data URIs in images are handled safely."""
    # Data URIs are allowed for images, but should be validated
    safe_data_uri = '<img src="data:image/png;base64,iVBORw0KGg..." alt="Image">'
    result = sanitize_rich_text(safe_data_uri)
    
    # Should allow data: protocol for images
    assert "data:image/png" in result.html
    

def test_sanitize_rich_text_preserves_safe_img_attributes():
    """Test that safe image attributes are preserved."""
    safe_input = '<img src="https://example.com/image.png" alt="Description" width="100" height="100">'
    result = sanitize_rich_text(safe_input)
    
    assert 'src="https://example.com/image.png"' in result.html
    assert 'alt="Description"' in result.html
    assert 'width="100"' in result.html
    assert 'height="100"' in result.html


def test_sanitize_rich_text_handles_empty_input():
    """Test that empty input is handled correctly."""
    result = sanitize_rich_text("")
    
    assert result.html == ""
    assert result.text_content == ""
    assert result.has_rich_content is False


def test_sanitize_rich_text_handles_none_input():
    """Test that None input is handled correctly."""
    result = sanitize_rich_text(None)
    
    assert result.html == ""
    assert result.text_content == ""
    assert result.has_rich_content is False


def test_sanitize_rich_text_converts_plain_text_newlines():
    """Test that plain text newlines are converted to br tags."""
    plain_text = "Line 1\nLine 2\nLine 3"
    result = sanitize_rich_text(plain_text)
    
    assert "Line 1<br />Line 2<br />Line 3" in result.html


def test_sanitize_rich_text_extracts_text_content():
    """Test that text content is extracted from HTML."""
    html_input = "<h1>Title</h1><p>Content with <strong>bold</strong> text</p>"
    result = sanitize_rich_text(html_input)
    
    assert "Title" in result.text_content
    assert "Content with bold text" in result.text_content
    assert "<h1>" not in result.text_content
    assert "<strong>" not in result.text_content


def test_sanitize_rich_text_detects_has_content():
    """Test that has_rich_content flag is set correctly."""
    # With content
    result_with_content = sanitize_rich_text("<p>Hello</p>")
    assert result_with_content.has_rich_content is True
    
    # With image (no text)
    result_with_image = sanitize_rich_text('<img src="test.png" alt="Test">')
    assert result_with_image.has_rich_content is True
    
    # Empty
    result_empty = sanitize_rich_text("")
    assert result_empty.has_rich_content is False
    
    # Only whitespace
    result_whitespace = sanitize_rich_text("   \n   ")
    assert result_whitespace.has_rich_content is False


def test_sanitize_rich_text_prevents_html_entity_encoding_bypass():
    """Test that HTML entity encoding doesn't bypass XSS prevention."""
    # Try to use HTML entities to bypass filters
    malicious_input = '&lt;script&gt;alert("XSS")&lt;/script&gt;'
    result = sanitize_rich_text(malicious_input)
    
    # bleach should handle entities correctly and not execute them
    assert "alert" not in result.html or "&lt;script&gt;" in result.html
