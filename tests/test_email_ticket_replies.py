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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
