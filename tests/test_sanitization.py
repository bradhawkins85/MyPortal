import base64

import pytest

from app.services import ticket_attachments as ticket_attachments_service
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


def test_sanitize_rich_text_keeps_non_quoted_header_like_content():
    content = """
        You have received a new voice mail from "61419685556 - 61419685556 "

        From: 61419685556
        To: "17501" - "Brad" "Hawkins"
        Received:"Tuesday, March 24, 2026 1:36:56 PM"
        Duration:"00:00:37"
        File:"vmail_61419685556_17501_20260324033656"
    """

    result = sanitize_rich_text(content)

    assert "voice mail from" in result.html
    assert "Duration" in result.html
    assert "Received" in result.html


@pytest.mark.asyncio
async def test_persist_inline_images_for_ticket_body_saves_data_uri(monkeypatch, tmp_path):
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    created = []

    monkeypatch.setattr(ticket_attachments_service, "_get_upload_directory", lambda: tmp_path)

    async def fake_create_attachment(**kwargs):
        record = {"id": 42, **kwargs}
        created.append(record)
        return record

    monkeypatch.setattr(ticket_attachments_service.attachments_repo, "create_attachment", fake_create_attachment)

    body = f'<p>Screenshot</p><img src="data:image/png;base64,{base64.b64encode(png_bytes).decode()}" alt="shot">'

    rewritten, attachments = await ticket_attachments_service.persist_inline_images_for_ticket_body(
        123,
        body,
        access_level="closed",
        uploaded_by_user_id=7,
    )

    assert "data:image" not in rewritten
    assert 'src="/api/tickets/123/attachments/42/download"' in rewritten
    assert attachments == created
    assert created[0]["ticket_id"] == 123
    assert created[0]["original_filename"] == "inline-image.png"
    assert created[0]["access_level"] == "closed"
    assert created[0]["uploaded_by_user_id"] == 7
    assert (tmp_path / created[0]["filename"]).read_bytes() == png_bytes
