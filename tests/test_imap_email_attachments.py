"""Tests for IMAP email attachment extraction functionality."""
from __future__ import annotations

import base64
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def test_extract_body_and_attachments_with_pdf():
    """Test that PDF attachments are extracted separately from body."""
    # Import here to avoid loading the full app
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    
    # Import only the function we need
    from app.services.imap import _extract_body_and_attachments
    
    # Create a multipart message with text and PDF attachment
    root = MIMEMultipart("mixed")
    root["Subject"] = "Test with attachment"
    
    # Add text part
    text_part = MIMEText("<p>This is the email body</p>", "html")
    root.attach(text_part)
    
    # Add PDF attachment
    pdf_content = b"PDF file content here"
    pdf_part = MIMEApplication(pdf_content, _subtype="pdf")
    pdf_part.add_header("Content-Disposition", "attachment", filename="document.pdf")
    root.attach(pdf_part)
    
    # Extract body and attachments
    body, attachments = _extract_body_and_attachments(root)
    
    # Verify body
    assert "<p>This is the email body</p>" in body
    
    # Verify attachments
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "document.pdf"
    assert attachments[0]["content_type"] == "application/pdf"
    assert attachments[0]["payload"] == pdf_content


def test_extract_body_and_attachments_inline_images_not_in_attachments():
    """Test that inline images are NOT included in attachments list."""
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    
    from app.services.imap import _extract_body_and_attachments
    
    # Create a message with inline image
    root = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    
    cid = "image1"
    alternative.attach(MIMEText(f"<p><img src=\"cid:{cid}\" alt=\"Inline\"></p>", "html"))
    root.attach(alternative)
    
    # Add inline image with Content-ID
    image = MIMEImage(b"PNGDATA", _subtype="png")
    image.add_header("Content-ID", f"<{cid}>")
    image.add_header("Content-Disposition", "inline", filename="image.png")
    root.attach(image)
    
    # Extract body and attachments
    body, attachments = _extract_body_and_attachments(root)
    
    # Verify inline image is embedded in body
    assert "data:image/png;base64" in body
    
    # Verify inline image is NOT in attachments list
    assert len(attachments) == 0


def test_extract_body_and_attachments_with_multiple_files():
    """Test extraction of multiple attachments of different types."""
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    
    from app.services.imap import _extract_body_and_attachments
    
    # Create message with multiple attachments
    root = MIMEMultipart("mixed")
    root.attach(MIMEText("<p>Email with multiple attachments</p>", "html"))
    
    # Add PDF
    pdf_part = MIMEApplication(b"PDF content", _subtype="pdf")
    pdf_part.add_header("Content-Disposition", "attachment", filename="report.pdf")
    root.attach(pdf_part)
    
    # Add Word document
    docx_part = MIMEApplication(b"DOCX content", _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document")
    docx_part.add_header("Content-Disposition", "attachment", filename="document.docx")
    root.attach(docx_part)
    
    # Add ZIP file
    zip_part = MIMEApplication(b"ZIP content", _subtype="zip")
    zip_part.add_header("Content-Disposition", "attachment", filename="archive.zip")
    root.attach(zip_part)
    
    # Extract
    body, attachments = _extract_body_and_attachments(root)
    
    # Verify we got all 3 attachments
    assert len(attachments) == 3
    
    filenames = {att["filename"] for att in attachments}
    assert "report.pdf" in filenames
    assert "document.docx" in filenames
    assert "archive.zip" in filenames


def test_extract_body_and_attachments_no_attachments():
    """Test that messages without attachments return empty list."""
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    
    from app.services.imap import _extract_body_and_attachments
    
    # Simple message with just text
    message = EmailMessage()
    message["Subject"] = "Test"
    message.set_content("Plain text body")
    message.add_alternative("<p>HTML body</p>", subtype="html")
    
    body, attachments = _extract_body_and_attachments(message)
    
    assert "HTML body" in body
    assert len(attachments) == 0


def test_backward_compatible_extract_body():
    """Test that the old _extract_body function still works."""
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    
    from app.services.imap import _extract_body
    
    # Create a simple message
    message = EmailMessage()
    message.set_content("Plain text")
    message.add_alternative("<p><strong>HTML</strong></p>", subtype="html")
    
    # Use old function
    body = _extract_body(message)
    
    # Should return just the body string
    assert isinstance(body, str)
    assert "<p><strong>HTML</strong></p>" in body
