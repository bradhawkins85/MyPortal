"""Tests for email tracking functionality."""

import pytest
from app.services import email_tracking
from app.core.config import get_settings


@pytest.fixture
def mock_portal_url(monkeypatch):
    """Mock the portal_url setting for tests."""
    settings = get_settings()
    monkeypatch.setattr(settings, "portal_url", "https://portal.example.com")
    return settings


def test_generate_tracking_id():
    """Test that tracking ID generation produces unique values."""
    tracking_id_1 = email_tracking.generate_tracking_id()
    tracking_id_2 = email_tracking.generate_tracking_id()
    
    assert tracking_id_1 != tracking_id_2
    assert len(tracking_id_1) > 20  # Should be a reasonable length
    assert isinstance(tracking_id_1, str)


def test_insert_tracking_pixel(mock_portal_url):
    """Test that tracking pixel is correctly inserted into HTML."""
    html_body = "<html><body><p>Test email</p></body></html>"
    tracking_id = "test-tracking-id-123"
    
    result = email_tracking.insert_tracking_pixel(html_body, tracking_id)
    
    assert "test-tracking-id-123.gif" in result
    assert '<img src=' in result
    assert 'width="1" height="1"' in result
    assert 'display:none' in result
    # Pixel should be before closing body tag
    assert result.index('<img src=') < result.index('</body>')


def test_insert_tracking_pixel_no_body_tag(mock_portal_url):
    """Test tracking pixel insertion when no body tag exists."""
    html_body = "<p>Test email without body tag</p>"
    tracking_id = "test-tracking-id-456"
    
    result = email_tracking.insert_tracking_pixel(html_body, tracking_id)
    
    assert "test-tracking-id-456.gif" in result
    assert '<img src=' in result
    # Pixel should be appended to end
    assert result.endswith('"/>')


def test_rewrite_links_for_tracking(mock_portal_url):
    """Test that links are correctly rewritten for tracking."""
    html_body = """
    <html>
    <body>
        <a href="https://example.com/page1">Link 1</a>
        <a href='https://example.com/page2'>Link 2</a>
        <a href="mailto:test@example.com">Email link</a>
    </body>
    </html>
    """
    tracking_id = "test-tracking-id-789"
    
    result = email_tracking.rewrite_links_for_tracking(html_body, tracking_id)
    
    # HTTP links should be rewritten
    assert "/api/email-tracking/click?" in result
    assert "tid=test-tracking-id-789" in result
    assert "url=https%3A%2F%2Fexample.com%2Fpage1" in result
    assert "url=https%3A%2F%2Fexample.com%2Fpage2" in result
    # Mailto links should not be rewritten
    assert 'href="mailto:test@example.com"' in result


def test_rewrite_links_preserves_tracking_pixel(mock_portal_url):
    """Test that tracking pixel URLs are not rewritten."""
    html_body = """
    <a href="https://example.com/api/email-tracking/pixel/abc123.gif">Should not rewrite</a>
    <a href="https://other.com/page">Should rewrite</a>
    """
    tracking_id = "test-id"
    
    result = email_tracking.rewrite_links_for_tracking(html_body, tracking_id)
    
    # Tracking pixel URL should not be rewritten
    assert 'href="https://example.com/api/email-tracking/pixel/abc123.gif"' in result
    # Other link should be rewritten
    assert "/api/email-tracking/click?" in result
    assert "url=https%3A%2F%2Fother.com%2Fpage" in result


def test_rewrite_links_no_links(mock_portal_url):
    """Test link rewriting with no links present."""
    html_body = "<html><body><p>No links here</p></body></html>"
    tracking_id = "test-id"
    
    result = email_tracking.rewrite_links_for_tracking(html_body, tracking_id)
    
    # Should return unchanged
    assert result == html_body


def test_insert_tracking_pixel_without_portal_url(monkeypatch):
    """Test that tracking pixel insertion fails gracefully without portal_url."""
    # Use monkeypatch to set portal_url to None for this test
    from app.core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "portal_url", None)
    
    html_body = "<html><body><p>Test email</p></body></html>"
    tracking_id = "test-tracking-id-123"
    
    result = email_tracking.insert_tracking_pixel(html_body, tracking_id)
    
    # Should return unchanged HTML when portal_url is not configured
    assert result == html_body
    assert "test-tracking-id-123.gif" not in result


def test_rewrite_links_without_portal_url(monkeypatch):
    """Test that link rewriting fails gracefully without portal_url."""
    # Use monkeypatch to set portal_url to None for this test
    from app.core.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "portal_url", None)
    
    html_body = """
    <html>
    <body>
        <a href="https://example.com/page1">Link 1</a>
        <a href='https://example.com/page2'>Link 2</a>
    </body>
    </html>
    """
    tracking_id = "test-tracking-id-789"
    
    result = email_tracking.rewrite_links_for_tracking(html_body, tracking_id)
    
    # Should return unchanged HTML when portal_url is not configured
    assert result == html_body
    assert "/api/email-tracking/click?" not in result


@pytest.mark.asyncio
async def test_record_email_sent(db_connection):
    """Test recording email sent metadata."""
    # This would require database setup
    # Skipping for now as it requires test database
    pass


@pytest.mark.asyncio
async def test_record_tracking_event(db_connection):
    """Test recording tracking events."""
    # This would require database setup
    # Skipping for now as it requires test database
    pass


@pytest.mark.asyncio
async def test_get_tracking_status(db_connection):
    """Test retrieving tracking status."""
    # This would require database setup
    # Skipping for now as it requires test database
    pass
