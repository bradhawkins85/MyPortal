"""Tests for email tracking functionality."""

import quopri
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


def test_insert_tracking_pixel_word_html(mock_portal_url):
    """Test that tracking pixel is correctly inserted into Word-generated HTML."""
    # This is the quoted-printable encoded HTML from the problem statement
    qp_html = b"""<html xmlns:o=3D"urn:schemas-microsoft-com:office:office" xmlns:w=3D"urn:schema=
s-microsoft-com:office:word" xmlns:m=3D"http://schemas.microsoft.com/office/20=
04/12/omml" xmlns=3D"http://www.w3.org/TR/REC-html40"><head><meta http-equiv=3DC=
ontent-Type content=3D"text/html; charset=3Dutf-8"><meta name=3DGenerator content=3D=
"Microsoft Word 15 (filtered medium)"><style><!--
/* Font Definitions */
@font-face
\t{font-family:"Cambria Math";
\tpanose-1:2 4 5 3 5 4 6 3 2 4;}
@font-face
\t{font-family:Calibri;
\tpanose-1:2 15 5 2 2 2 4 3 2 4;}
@font-face
\t{font-family:Aptos;
\tpanose-1:2 11 0 4 2 2 2 2 2 4;}
@font-face
\t{font-family:Consolas;
\tpanose-1:2 11 6 9 2 2 4 3 2 4;}
/* Style Definitions */
pre
\t{mso-style-priority:99;
\tmso-style-link:"HTML Preformatted Char";
\tmargin:0cm;
\tfont-size:10.0pt;
\tfont-family:"Courier New";}
span.HTMLPreformattedChar
\t{mso-style-name:"HTML Preformatted Char";
\tmso-style-priority:99;
\tmso-style-link:"HTML Preformatted";
\tfont-family:Consolas;
\tmso-ligatures:none;
\tmso-fareast-language:EN-GB;}
.MsoChpDefault
\t{mso-style-type:export-only;
\tmso-fareast-language:EN-US;}
@page WordSection1
\t{size:612.0pt 792.0pt;
\tmargin:72.0pt 72.0pt 72.0pt 72.0pt;}
div.WordSection1
\t{page:WordSection1;}
--></style></head><body lang=3DEN-AU link=3D"#0563C1" vlink=3D"#954F72" style=3D'wo=
rd-wrap:break-word'><div class=3DWordSection1><pre>qwe123</pre></div></body><=
/html>"""
    
    # Decode quoted-printable (this is what email.get_payload(decode=True) would do)
    html_body = quopri.decodestring(qp_html).decode('utf-8')
    
    tracking_id = "test-tracking-word-123"
    
    result = email_tracking.insert_tracking_pixel(html_body, tracking_id)
    
    # Verify tracking pixel was inserted
    assert "test-tracking-word-123.gif" in result
    assert '<img src=' in result
    assert 'width="1" height="1"' in result
    assert 'display:none' in result
    # Pixel should be before closing body tag
    assert result.index('<img src=') < result.index('</body>')


def test_insert_tracking_pixel_simple_html(mock_portal_url):
    """Test that tracking pixel is correctly inserted into simple HTML without body tags."""
    html_body = "<p>This is a simple notification message</p>"
    tracking_id = "test-tracking-simple-456"
    
    result = email_tracking.insert_tracking_pixel(html_body, tracking_id)
    
    # Verify tracking pixel was inserted at the end
    assert "test-tracking-simple-456.gif" in result
    assert '<img src=' in result
    assert 'width="1" height="1"' in result
    assert 'display:none' in result
    assert result.endswith('"/>')


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


def test_invoke_smtp_with_ticket_reply_context(monkeypatch):
    """Test that _invoke_smtp enables tracking when ticket reply context is present."""
    import asyncio
    from app.services import modules as modules_service
    from app.services import email as email_service
    from app.services import webhook_monitor
    from app.core.config import get_settings
    
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    
    captured_email_params = {}
    captured_webhook_params = {}
    
    # Mock SMTP
    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def ehlo(self):
            pass
        def starttls(self, **kwargs):
            pass
        def login(self, *args):
            pass
        def send_message(self, message):
            pass
    
    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)
    
    # Mock send_email to capture parameters
    original_send_email = email_service.send_email
    async def mock_send_email(**kwargs):
        captured_email_params.update(kwargs)
        # Return success
        return True, {"id": 1}
    
    monkeypatch.setattr(email_service, "send_email", mock_send_email)
    
    # Mock webhook monitor
    async def mock_create_manual_event(**kwargs):
        captured_webhook_params.update(kwargs)
        return {"id": 123}
    
    async def mock_record_manual_success(*args, **kwargs):
        return {"id": 123, "status": "succeeded"}
    
    # Mock internal _record_success and _record_failure
    async def mock_record_success(*args, **kwargs):
        return {"id": 123, "status": "succeeded"}
    
    async def mock_record_failure(*args, **kwargs):
        return {"id": 123, "status": "failed"}
    
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    monkeypatch.setattr(webhook_monitor, "record_manual_success", mock_record_manual_success)
    monkeypatch.setattr(modules_service, "_record_success", mock_record_success)
    monkeypatch.setattr(modules_service, "_record_failure", mock_record_failure)
    
    # Test payload with ticket reply context (automation style with ticket.latest_reply)
    settings_payload = {}
    payload = {
        "subject": "Ticket Reply",
        "html": "<p>Your ticket has been updated</p>",
        "recipients": ["customer@example.com"],
        "context": {
            "ticket": {
                "id": 100,
                "number": "100",
                "latest_reply": {
                    "id": 456,
                    "body": "This is the reply",
                    "author_id": 1
                }
            }
        }
    }
    
    # Call _invoke_smtp
    result = asyncio.run(modules_service._invoke_smtp(settings_payload, payload))
    
    # Verify tracking was enabled
    assert captured_email_params.get("enable_tracking") is True
    assert captured_email_params.get("ticket_reply_id") == 456
    assert captured_email_params.get("subject") == "Ticket Reply"
    assert captured_email_params.get("recipients") == ["customer@example.com"]


def test_invoke_smtp_with_metadata_reply_id(monkeypatch):
    """Test that _invoke_smtp enables tracking when metadata has reply_id."""
    import asyncio
    from app.services import modules as modules_service
    from app.services import email as email_service
    from app.services import webhook_monitor
    from app.core.config import get_settings
    
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    
    captured_email_params = {}
    
    # Mock SMTP
    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def ehlo(self):
            pass
        def starttls(self, **kwargs):
            pass
        def login(self, *args):
            pass
        def send_message(self, message):
            pass
    
    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)
    
    # Mock send_email to capture parameters
    async def mock_send_email(**kwargs):
        captured_email_params.update(kwargs)
        return True, {"id": 1}
    
    monkeypatch.setattr(email_service, "send_email", mock_send_email)
    
    # Mock webhook monitor
    async def mock_create_manual_event(**kwargs):
        return {"id": 123}
    
    async def mock_record_success(*args, **kwargs):
        return {"id": 123, "status": "succeeded"}
    
    async def mock_record_failure(*args, **kwargs):
        return {"id": 123, "status": "failed"}
    
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    monkeypatch.setattr(modules_service, "_record_success", mock_record_success)
    monkeypatch.setattr(modules_service, "_record_failure", mock_record_failure)
    
    # Test payload with reply_id in metadata (notification style)
    settings_payload = {}
    payload = {
        "subject": "Ticket Reply",
        "html": "<p>Your ticket has been updated</p>",
        "recipients": ["customer@example.com"],
        "context": {
            "metadata": {
                "reply_id": 789
            }
        }
    }
    
    # Call _invoke_smtp
    result = asyncio.run(modules_service._invoke_smtp(settings_payload, payload))
    
    # Verify tracking was enabled
    assert captured_email_params.get("enable_tracking") is True
    assert captured_email_params.get("ticket_reply_id") == 789


def test_invoke_smtp_without_ticket_reply_context(monkeypatch):
    """Test that _invoke_smtp does not enable tracking when no ticket reply context is present."""
    import asyncio
    from app.services import modules as modules_service
    from app.services import email as email_service
    from app.services import webhook_monitor
    from app.core.config import get_settings
    
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    
    captured_email_params = {}
    
    # Mock SMTP
    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def ehlo(self):
            pass
        def starttls(self, **kwargs):
            pass
        def login(self, *args):
            pass
        def send_message(self, message):
            pass
    
    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)
    
    # Mock send_email to capture parameters
    async def mock_send_email(**kwargs):
        captured_email_params.update(kwargs)
        return True, {"id": 1}
    
    monkeypatch.setattr(email_service, "send_email", mock_send_email)
    
    # Mock webhook monitor
    async def mock_create_manual_event(**kwargs):
        return {"id": 123}
    
    async def mock_record_manual_success(*args, **kwargs):
        return {"id": 123, "status": "succeeded"}
    
    # Mock internal functions
    async def mock_record_success(*args, **kwargs):
        return {"id": 123, "status": "succeeded"}
    
    async def mock_record_failure(*args, **kwargs):
        return {"id": 123, "status": "failed"}
    
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    monkeypatch.setattr(webhook_monitor, "record_manual_success", mock_record_manual_success)
    monkeypatch.setattr(modules_service, "_record_success", mock_record_success)
    monkeypatch.setattr(modules_service, "_record_failure", mock_record_failure)
    
    # Test payload WITHOUT ticket reply context
    settings_payload = {}
    payload = {
        "subject": "Generic Notification",
        "html": "<p>This is a notification</p>",
        "recipients": ["user@example.com"],
    }
    
    # Call _invoke_smtp
    result = asyncio.run(modules_service._invoke_smtp(settings_payload, payload))
    
    # Verify tracking was NOT enabled
    assert captured_email_params.get("enable_tracking") is False
    assert captured_email_params.get("ticket_reply_id") is None
    assert captured_email_params.get("subject") == "Generic Notification"


@pytest.mark.asyncio
async def test_emit_notification_with_ticket_reply_metadata(monkeypatch):
    """Test that emit_notification enables tracking when ticket reply metadata is present."""
    from app.services import notifications
    from app.services import email as email_service
    from app.services import webhook_monitor
    from app.repositories import notification_preferences as preferences_repo
    from app.repositories import users as user_repo
    from app.repositories import notifications as notifications_repo
    from app.services import notification_event_settings
    from app.core.config import get_settings
    
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "app_name", "TestPortal")
    
    captured_email_params = {}
    
    # Mock SMTP
    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def ehlo(self):
            pass
        def starttls(self, **kwargs):
            pass
        def login(self, *args):
            pass
        def send_message(self, message):
            pass
    
    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)
    
    # Mock dependencies
    async def mock_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Test Event",
            "message_template": "{{ message }}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }
    
    async def mock_get_preference(user_id, event_type):
        return None
    
    async def mock_get_user_by_id(user_id):
        return {
            "id": user_id,
            "email": "customer@example.com",
            "display_name": "Test User"
        }
    
    async def mock_create_notification(**kwargs):
        return {"id": 1}
    
    async def mock_send_email(**kwargs):
        captured_email_params.update(kwargs)
        return True, {"id": 1}
    
    async def mock_create_manual_event(**kwargs):
        return {"id": 1}
    
    async def mock_record_manual_success(*args, **kwargs):
        return {"id": 1}
    
    monkeypatch.setattr(notification_event_settings, "get_event_setting", mock_get_event_setting)
    monkeypatch.setattr(preferences_repo, "get_preference", mock_get_preference)
    monkeypatch.setattr(user_repo, "get_user_by_id", mock_get_user_by_id)
    monkeypatch.setattr(notifications_repo, "create_notification", mock_create_notification)
    monkeypatch.setattr(email_service, "send_email", mock_send_email)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    monkeypatch.setattr(webhook_monitor, "record_manual_success", mock_record_manual_success)
    
    # Call emit_notification with ticket reply metadata
    await notifications.emit_notification(
        event_type="ticket.reply.created",
        message="New reply on your ticket",
        user_id=1,
        metadata={"reply_id": 789}
    )
    
    # Verify tracking was enabled
    assert captured_email_params.get("enable_tracking") is True
    assert captured_email_params.get("ticket_reply_id") == 789
