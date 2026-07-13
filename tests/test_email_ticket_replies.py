"""
Tests for email reply handling in ticket system.

This test suite verifies that email replies are correctly appended to existing
tickets instead of creating new tickets.
"""

import pytest
from app.services.imap import (
    _extract_ticket_number_from_subject,
    _normalize_subject_for_matching,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestTicketNumberExtraction:
    """Test extraction of ticket numbers from email subjects."""

    def test_extract_basic_hash_number(self):
        """Test extraction of #123 pattern."""
        subject = "Support Request #123"
        result = _extract_ticket_number_from_subject(subject)
        assert result == "123"

    def test_extract_hash_number_with_re_prefix(self):
        """Test extraction from RE: prefixed subject."""
        subject = "RE: Support Request #456"
        result = _extract_ticket_number_from_subject(subject)
        assert result == "456"

    def test_extract_hash_number_with_multiple_re_prefixes(self):
        """Test extraction from multiple RE: prefixes."""
        subject = "RE: RE: Support Request #789"
        result = _extract_ticket_number_from_subject(subject)
        assert result == "789"

    def test_extract_ticket_colon_pattern(self):
        """Test extraction of 'Ticket: 123' pattern."""
        subject = "Ticket: 999 - Network Issue"
        result = _extract_ticket_number_from_subject(subject)
        assert result == "999"

    def test_extract_from_bracketed_number(self):
        """Test extraction from [#123] pattern."""
        subject = "[#555] Database Error"
        result = _extract_ticket_number_from_subject(subject)
        assert result == "555"

    def test_extract_first_number_when_multiple(self):
        """Test that first ticket number is extracted when multiple present."""
        subject = "RE: Support #111 and #222"
        result = _extract_ticket_number_from_subject(subject)
        assert result == "111"

    def test_no_number_in_subject(self):
        """Test that None is returned when no ticket number present."""
        subject = "Support Request"
        result = _extract_ticket_number_from_subject(subject)
        assert result is None

    def test_empty_subject(self):
        """Test that None is returned for empty subject."""
        subject = ""
        result = _extract_ticket_number_from_subject(subject)
        assert result is None

    def test_none_subject(self):
        """Test that None is returned for None subject."""
        subject = None
        result = _extract_ticket_number_from_subject(subject)
        assert result is None


class TestSubjectNormalization:
    """Test normalization of email subjects for matching."""

    def test_remove_re_prefix(self):
        """Test removal of RE: prefix."""
        subject = "RE: Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_multiple_re_prefixes(self):
        """Test removal of multiple RE: prefixes."""
        subject = "RE: RE: RE: Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_fw_prefix(self):
        """Test removal of FW: prefix."""
        subject = "FW: Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_fwd_prefix(self):
        """Test removal of FWD: prefix."""
        subject = "FWD: Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_mixed_prefixes(self):
        """Test removal of mixed RE/FW prefixes."""
        subject = "RE: FW: RE: Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_case_insensitive_prefix_removal(self):
        """Test that prefix removal is case insensitive."""
        subject = "re: fw: Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_external_tag(self):
        """Test removal of [External] tag."""
        subject = "[External] Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_ticket_number_hash(self):
        """Test removal of ticket number #123."""
        subject = "Support Request #123"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_remove_ticket_colon_pattern(self):
        """Test removal of 'Ticket: 123' pattern."""
        subject = "Ticket: 456 Support Request"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request"

    def test_normalize_whitespace(self):
        """Test normalization of extra whitespace."""
        subject = "Support   Request   With   Spaces"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request With Spaces"

    def test_complex_normalization(self):
        """Test complex normalization with multiple elements."""
        subject = "RE: [External] Support Request #789 - Network Issue"
        result = _normalize_subject_for_matching(subject)
        assert result == "Support Request - Network Issue"

    def test_empty_subject(self):
        """Test that empty string is returned for empty subject."""
        subject = ""
        result = _normalize_subject_for_matching(subject)
        assert result == ""

    def test_none_subject(self):
        """Test that empty string is returned for None subject."""
        subject = None
        result = _normalize_subject_for_matching(subject)
        assert result == ""

    def test_subject_with_only_prefixes(self):
        """Test subject with only prefixes returns empty after normalization."""
        subject = "RE: FW: #123"
        result = _normalize_subject_for_matching(subject)
        assert result == ""

def test_inbound_reply_body_falls_back_when_header_stripping_removes_content():
    """Inbound replies with real text should still create a safe body if header stripping is too aggressive."""
    from app.services.imap import _sanitize_inbound_reply_body

    body = _sanitize_inbound_reply_body(
        "From: Customer <customer@example.com>\n"
        "Sent: Friday, 10 July 2026 10:31 AM\n"
        "Subject: Re: Website management\n"
        "Please see the attached update <script>alert(1)</script>"
    )

    assert body.has_rich_content is True
    assert "Please see the attached update" in body.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body.html
    assert "<script>" not in body.html


def test_attachment_only_reply_body_escapes_sender_and_subject():
    """Attachment-only inbound replies should still produce safe conversation text."""
    from app.services.imap import _build_attachment_only_reply_body

    body = _build_attachment_only_reply_body(
        from_address='Customer <bad@example.com><script>alert(1)</script>',
        subject='Re: Website <management>',
    )

    assert "Email reply received with attachment(s)" in body
    assert "Customer &lt;bad@example.com&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "Re: Website &lt;management&gt;" in body
    assert "<script>" not in body


@pytest.mark.anyio
class TestFindExistingTicket:
    """Test finding existing tickets for email replies."""

    async def test_find_ticket_by_number_in_subject(self, monkeypatch):
        """Test finding ticket by ticket number in subject."""
        from app.services import imap
        from app.core import database
        
        # Mock database to return a ticket when queried by ticket_number
        async def mock_fetch_all(query, params):
            if "ticket_number" in query and params[0] == "123":
                return [{
                    "id": 1,
                    "ticket_number": "123",
                    "subject": "Support Request",
                    "status": "open",
                    "requester_id": 5,
                    "company_id": 3,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }]
            return []
        
        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)
        
        # Test finding by ticket number
        result = await imap._find_existing_ticket_for_reply(
            subject="RE: Support Request #123",
            from_email="user@example.com",
            requester_id=5,
        )
        
        assert result is not None
        assert result["id"] == 1
        assert result["ticket_number"] == "123"

    async def test_no_ticket_found_without_number(self, monkeypatch):
        """Test that no ticket is found when subject is too short."""
        from app.services import imap
        from app.core import database
        
        # Mock database to return empty results
        async def mock_fetch_all(query, params):
            return []
        
        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)
        
        # Test with very short subject
        result = await imap._find_existing_ticket_for_reply(
            subject="RE: Hi",
            from_email="user@example.com",
            requester_id=5,
        )
        
        assert result is None

    async def test_find_ticket_by_subject_match(self, monkeypatch):
        """Test finding ticket by matching normalized subject."""
        from app.services import imap
        from app.core import database

        # Mock database to return tickets with matching subjects
        async def mock_fetch_all(query, params):
            # When searching by ticket_number, return empty
            if "ticket_number" in query:
                return []
            # When searching by subject/requester, return a ticket
            if "requester_id" in query or "EXISTS" in query:
                return [{
                    "id": 2,
                    "ticket_number": "456",
                    "subject": "Network Connection Issue",
                    "status": "open",
                    "requester_id": 5,
                    "company_id": 3,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }]
            return []
        
        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)
        
        # Test finding by subject match
        result = await imap._find_existing_ticket_for_reply(
            subject="RE: Network Connection Issue",
            from_email="user@example.com",
            requester_id=5,
        )
        
        assert result is not None
        assert result["id"] == 2
        assert result["subject"] == "Network Connection Issue"


    async def test_find_ticket_by_subject_and_sender_in_description(self, monkeypatch):
        """External sender tickets can be matched without a local requester user."""
        from app.core import database
        from app.services import imap

        captured: dict[str, object] = {}

        async def mock_fetch_all(query, params):
            captured["query"] = query
            captured["params"] = params
            if "ticket_number" in query:
                return []
            return [
                {
                    "id": 12,
                    "ticket_number": "25006",
                    "subject": "We have moved Support requests to Ideagen Luminate",
                    "description": "From: Ideagen Support <support@ideagen.example>\n\nHello",
                    "status": "open",
                    "requester_id": None,
                    "company_id": 3,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }
            ]

        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)

        result = await imap._find_existing_ticket_for_reply(
            subject="We have moved Support requests to Ideagen Luminate",
            from_email="support@ideagen.example",
            requester_id=None,
        )

        assert result is not None
        assert result["id"] == 12
        assert "COALESCE(t.description" in str(captured["query"])
        assert "%support@ideagen.example%" in captured["params"]

    async def test_find_ticket_by_in_reply_to_header(self, monkeypatch):
        """Test finding ticket by matching Message-ID references."""

        from app.services import imap
        from app.core import database

        async def mock_get_ticket_by_external_reference(external_reference):
            if external_reference == "message-123@example.com":
                return {
                    "id": 7,
                    "ticket_number": "777",
                    "subject": "Existing Ticket",
                    "status": "open",
                    "requester_id": 5,
                    "company_id": 3,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }
            return None

        async def mock_fetch_all(query, params):
            return []

        monkeypatch.setattr(
            imap.tickets_repo,
            "get_ticket_by_external_reference",
            mock_get_ticket_by_external_reference,
        )
        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)

        result = await imap._find_existing_ticket_for_reply(
            subject="Re: Something else",
            from_email="user@example.com",
            requester_id=5,
            related_message_ids=["message-123@example.com"],
        )

        assert result is not None
        assert result["id"] == 7

    async def test_find_ticket_by_reply_external_reference(self, monkeypatch):
        """Test finding ticket by reply external_reference when ticket lookup fails."""

        from app.services import imap
        from app.core import database

        async def mock_get_ticket_by_external_reference(external_reference):
            return None

        async def mock_fetch_all(query, params):
            if params == ("reply-message@example.com",):
                return [
                    {
                        "id": 9,
                        "ticket_number": "999",
                        "subject": "Outbound Message",
                        "status": "open",
                        "requester_id": 5,
                        "company_id": 3,
                        "created_at": None,
                        "updated_at": None,
                        "closed_at": None,
                        "ai_summary_updated_at": None,
                        "ai_tags": None,
                        "ai_tags_updated_at": None,
                    }
                ]
            return []

        monkeypatch.setattr(
            imap.tickets_repo,
            "get_ticket_by_external_reference",
            mock_get_ticket_by_external_reference,
        )
        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)

        result = await imap._find_existing_ticket_for_reply(
            subject="Re: Outbound Message",
            from_email="user@example.com",
            requester_id=5,
            related_message_ids=["reply-message@example.com"],
        )

        assert result is not None
        assert result["id"] == 9

    async def test_find_ticket_by_syncro_message_id(self, monkeypatch):
        """Test finding ticket by embedded Syncro message id in the email body."""

        from app.services import imap

        async def mock_get_ticket_by_external_reference(external_reference):
            if external_reference == "101748802":
                return {
                    "id": 11,
                    "ticket_number": "101748802",
                    "external_reference": "101748802",
                    "subject": "Syncro Ticket 101748802",
                    "status": "open",
                    "requester_id": 5,
                    "company_id": 3,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }
            return None

        monkeypatch.setattr(
            imap.tickets_repo,
            "get_ticket_by_external_reference",
            mock_get_ticket_by_external_reference,
        )

        result = await imap._find_existing_ticket_for_reply(
            subject="Re: Syncro reply",
            from_email="user@example.com",
            requester_id=5,
            related_message_ids=[],
            message_body="Please see details (message id: 101748802)",
        )

        assert result is not None
        assert result["external_reference"] == "101748802"

    async def test_closed_ticket_skipped_for_syncro_message_id(self, monkeypatch):
        """Ensure closed Syncro tickets are not matched by embedded message id."""

        from app.services import imap

        async def mock_get_ticket_by_external_reference(external_reference):
            return {
                "id": 12,
                "ticket_number": external_reference,
                "external_reference": external_reference,
                "subject": "Closed Syncro Ticket",
                "status": "closed",
                "requester_id": 5,
                "company_id": 3,
                "created_at": None,
                "updated_at": None,
                "closed_at": None,
                "ai_summary_updated_at": None,
                "ai_tags": None,
                "ai_tags_updated_at": None,
            }

        monkeypatch.setattr(
            imap.tickets_repo,
            "get_ticket_by_external_reference",
            mock_get_ticket_by_external_reference,
        )

        result = await imap._find_existing_ticket_for_reply(
            subject="Re: Syncro reply",
            from_email="user@example.com",
            requester_id=5,
            related_message_ids=[],
            message_body="Following up (message id: 222333444)",
        )

        assert result is None

    async def test_closed_ticket_not_matched(self, monkeypatch):
        """Test that closed tickets are not matched, forcing creation of new ticket."""
        from app.services import imap
        from app.core import database
        
        # Mock database to return a closed ticket when queried by ticket_number
        async def mock_fetch_all(query, params):
            if "ticket_number" in query and params[0] == "789":
                return [{
                    "id": 3,
                    "ticket_number": "789",
                    "subject": "Resolved Issue",
                    "status": "closed",
                    "requester_id": 5,
                    "company_id": 3,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }]
            return []
        
        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)
        
        # Test that closed ticket is not matched
        result = await imap._find_existing_ticket_for_reply(
            subject="RE: Resolved Issue #789",
            from_email="user@example.com",
            requester_id=5,
        )
        
        assert result is None, "Closed tickets should not be matched for replies"

    async def test_find_ticket_by_subject_for_known_user_not_watcher(self, monkeypatch):
        """Known users who are not watchers can still match a ticket if their email
        appears in the ticket description (e.g. tickets originally created from their
        email before they had a local user account)."""
        from app.services import imap
        from app.core import database

        captured: dict[str, object] = {}

        async def mock_fetch_all(query, params):
            captured["query"] = query
            captured["params"] = params
            if "ticket_number" in query:
                return []
            # Return a ticket where requester_id=None but description contains the
            # sender's email (ticket was created from an email before user existed)
            return [
                {
                    "id": 20,
                    "ticket_number": "30001",
                    "subject": "Printer not working",
                    "description": "From: alice@example.com\n\nPrinter is jammed",
                    "status": "open",
                    "requester_id": None,
                    "company_id": 4,
                    "created_at": None,
                    "updated_at": None,
                    "closed_at": None,
                    "ai_summary_updated_at": None,
                    "ai_tags": None,
                    "ai_tags_updated_at": None,
                }
            ]

        monkeypatch.setattr(database.db, "fetch_all", mock_fetch_all)

        # Sender is a known user (requester_id=99) but is NOT a watcher on the ticket.
        # Their email appears in the ticket description so it should still match.
        result = await imap._find_existing_ticket_for_reply(
            subject="Re: Printer not working",
            from_email="alice@example.com",
            requester_id=99,
        )

        assert result is not None, (
            "Ticket should be matched even when the known user is not a watcher, "
            "provided their email appears in the ticket description"
        )
        assert result["id"] == 20
        # The query must include the description fallback when requester_id is set
        assert "COALESCE(t.description" in str(captured["query"])
        assert "%alice@example.com%" in captured["params"]



def test_long_email_external_references_are_compacted_for_ticket_columns():
    from app.services.imap import (
        _expand_ticket_external_references,
        _normalise_ticket_external_reference,
    )

    long_reference = "<" + "a" * 180 + "@example.com>"

    stored = _normalise_ticket_external_reference(long_reference)

    assert stored is not None
    assert len(stored) == 128
    assert ":sha256:" in stored
    assert len(stored.rsplit(":sha256:", 1)[1]) == 32
    assert long_reference in _expand_ticket_external_references([long_reference])
    assert stored in _expand_ticket_external_references([long_reference])


def test_short_email_external_references_are_stored_unchanged():
    from app.services.imap import _normalise_ticket_external_reference

    assert _normalise_ticket_external_reference("<short@example.com>") == "<short@example.com>"


@pytest.mark.anyio
async def test_add_email_cc_watchers_adds_users_and_external_addresses(monkeypatch):
    """Original CC recipients should become ticket watchers for future replies."""
    from app.services import imap

    added: list[tuple[int, int | None, str | None]] = []

    async def fake_get_user_by_email(email: str):
        if email == "known@example.com":
            return {"id": 42, "email": email}
        return None

    async def fake_add_watcher(ticket_id: int, user_id=None, email=None):
        added.append((ticket_id, user_id, email))

    monkeypatch.setattr(imap.users_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(imap.tickets_repo, "add_watcher", fake_add_watcher)

    await imap._add_email_cc_watchers(
        123,
        ["Known@Example.com", "external@example.com", "known@example.com", "requester@example.com"],
        exclude_addresses=["requester@example.com"],
    )

    assert added == [
        (123, 42, None),
        (123, None, "external@example.com"),
    ]



def test_extract_body_embeds_url_encoded_content_id_image():
    """Inline images referenced by URL-encoded cid values should render in ticket bodies."""
    from email.message import EmailMessage

    from app.services.imap import _extract_body_and_attachments

    message = EmailMessage()
    message.set_content("Please see photo of the back of her phone.")
    message.add_alternative(
        (
            '<p>Please see photo of the back of her phone.</p>'
            '<img src="cid:image001.png%40abc123" width="480" height="640">'
        ),
        subtype="html",
    )
    html_part = message.get_payload()[1]
    html_part.add_related(
        b"fake-png-data",
        maintype="image",
        subtype="png",
        cid="<image001.png@abc123>",
        filename="image001.png",
    )

    body, attachments = _extract_body_and_attachments(message)

    assert 'src="data:image/png;base64,' in body
    assert "cid:image001" not in body
    assert attachments == []


def test_extract_body_embeds_content_location_image():
    """Some clients identify inline body images by Content-Location instead of Content-ID."""
    from email.message import EmailMessage
    from email.mime.image import MIMEImage

    from app.services.imap import _extract_body_and_attachments

    message = EmailMessage()
    message.set_content("Please see photo.")
    message.add_alternative(
        '<p>Please see photo.</p><img src="cid:phone-back.png">', subtype="html"
    )
    image = MIMEImage(b"fake-png-data", _subtype="png")
    image.add_header("Content-Disposition", "inline", filename="phone-back.png")
    image.add_header("Content-Location", "phone-back.png")
    message.get_payload()[1].make_related()
    message.get_payload()[1].attach(image)

    body, attachments = _extract_body_and_attachments(message)

    assert 'src="data:image/png;base64,' in body
    assert "cid:phone-back.png" not in body
    assert attachments == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
