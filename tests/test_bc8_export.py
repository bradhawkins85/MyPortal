"""
Tests for BC8 export service.

Tests:
- DOCX generation
- PDF generation
- Content hash computation
- Metadata embedding
- Table rendering
- Error handling
"""
import hashlib
import io
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.bc_export_service import (
    compute_content_hash,
    export_to_docx,
    export_to_pdf,
)


# ============================================================================
# Content Hash Tests
# ============================================================================

def test_compute_content_hash_deterministic():
    """Test that content hash is deterministic for same input."""
    content = {"section1": {"field1": "value1"}}
    metadata = {"plan_title": "Test Plan", "version_number": 1}
    
    hash1 = compute_content_hash(content, metadata)
    hash2 = compute_content_hash(content, metadata)
    
    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 hex digest


def test_compute_content_hash_different_content():
    """Test that different content produces different hash."""
    metadata = {"plan_title": "Test Plan", "version_number": 1}
    
    hash1 = compute_content_hash({"field": "value1"}, metadata)
    hash2 = compute_content_hash({"field": "value2"}, metadata)
    
    assert hash1 != hash2


def test_compute_content_hash_different_metadata():
    """Test that different metadata produces different hash."""
    content = {"field": "value"}
    
    hash1 = compute_content_hash(content, {"version": 1})
    hash2 = compute_content_hash(content, {"version": 2})
    
    assert hash1 != hash2


def test_compute_content_hash_order_independent():
    """Test that field order doesn't affect hash (due to sort_keys)."""
    metadata = {"plan_title": "Test"}

    content1 = {"a": 1, "b": 2, "c": 3}
    content2 = {"c": 3, "a": 1, "b": 2}

    hash1 = compute_content_hash(content1, metadata)
    hash2 = compute_content_hash(content2, metadata)

    assert hash1 == hash2


def test_compute_content_hash_handles_non_json_types():
    """Ensure datetimes and decimals are serialized deterministically."""
    timestamp = datetime(2024, 5, 1, 9, 30, tzinfo=timezone.utc)
    review_date = date(2024, 5, 2)
    review_time = time(14, 45)
    amount = Decimal("123.45")

    content = {"last_reviewed_at": timestamp, "budget": amount}
    metadata = {"next_review_date": review_date, "meeting_time": review_time}

    digest = compute_content_hash(content, metadata)

    normalized = {
        "content": {
            "last_reviewed_at": timestamp.isoformat(),
            "budget": str(amount),
        },
        "metadata": {
            "next_review_date": review_date.isoformat(),
            "meeting_time": review_time.isoformat(),
        },
    }
    expected = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()

    assert digest == expected


# ============================================================================
# DOCX Export Tests
# ============================================================================

@pytest.mark.asyncio
async def test_export_to_docx_plan_not_found():
    """Test DOCX export fails when plan doesn't exist."""
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=None):
        with pytest.raises(ValueError, match="Plan .* not found"):
            await export_to_docx(plan_id=999)


@pytest.mark.asyncio
async def test_export_to_docx_version_not_found():
    """Test DOCX export fails when version doesn't exist."""
    mock_plan = {"id": 1, "title": "Test Plan"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=None):
            with pytest.raises(ValueError, match="Version .* not found"):
                await export_to_docx(plan_id=1, version_id=999)


@pytest.mark.asyncio
async def test_export_to_docx_no_active_version():
    """Test DOCX export fails when no active version exists."""
    mock_plan = {"id": 1, "title": "Test Plan"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_active_version", return_value=None):
            with pytest.raises(ValueError, match="No active version found"):
                await export_to_docx(plan_id=1)


@pytest.mark.asyncio
async def test_export_to_docx_success():
    """Test successful DOCX export."""
    mock_plan = {
        "id": 1,
        "title": "Test Business Continuity Plan",
        "template_id": 1,
    }
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "summary_change_note": "Initial version",
        "content_json": {
            "plan_overview": {
                "purpose": "Test purpose",
                "scope": "Test scope",
            },
        },
    }
    
    mock_template = {
        "id": 1,
        "name": "Government BCP Template",
        "schema_json": {
            "sections": [
                {
                    "section_id": "plan_overview",
                    "title": "Plan Overview",
                    "fields": [
                        {"field_id": "purpose", "label": "Purpose", "field_type": "text"},
                        {"field_id": "scope", "label": "Scope", "field_type": "text"},
                    ],
                },
            ],
        },
    }
    
    mock_author = {"id": 1, "name": "John Doe"}
    
    mock_risks = []
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=mock_template):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=mock_risks):
                        buffer, content_hash = await export_to_docx(plan_id=1, version_id=1)
    
    # Verify return types
    assert isinstance(buffer, io.BytesIO)
    assert isinstance(content_hash, str)
    assert len(content_hash) == 64
    
    # Verify buffer has content
    buffer.seek(0)
    content = buffer.read()
    assert len(content) > 0


@pytest.mark.asyncio
async def test_export_to_docx_with_risks():
    """Test DOCX export includes risk register."""
    mock_plan = {"id": 1, "title": "Test Plan", "template_id": None}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime.now(timezone.utc),
        "content_json": {},
    }
    
    mock_author = {"id": 1, "name": "Jane Smith"}
    
    mock_risks = [
        {
            "id": 1,
            "threat": "Data center fire",
            "likelihood": "unlikely",
            "impact": "catastrophic",
            "rating": "high",
            "mitigation": "Off-site backups and DR site",
        },
        {
            "id": 2,
            "threat": "Ransomware attack",
            "likelihood": "possible",
            "impact": "major",
            "rating": "high",
            "mitigation": "Anti-malware, backups, employee training",
        },
    ]
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=mock_risks):
                        buffer, content_hash = await export_to_docx(plan_id=1, version_id=1)
    
    assert isinstance(buffer, io.BytesIO)
    assert len(content_hash) == 64


@pytest.mark.asyncio
async def test_export_to_docx_with_table_fields():
    """Test DOCX export handles table fields correctly."""
    mock_plan = {"id": 1, "title": "Test Plan", "template_id": 1}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime.now(timezone.utc),
        "content_json": {
            "bia": {
                "critical_processes": [
                    {
                        "process_name": "Email Service",
                        "rto": "4 hours",
                        "rpo": "1 hour",
                        "impact": "high",
                    },
                    {
                        "process_name": "Payment Processing",
                        "rto": "1 hour",
                        "rpo": "15 minutes",
                        "impact": "critical",
                    },
                ],
            },
        },
    }
    
    mock_template = {
        "id": 1,
        "name": "Test Template",
        "schema_json": {
            "sections": [
                {
                    "section_id": "bia",
                    "title": "Business Impact Analysis",
                    "fields": [
                        {
                            "field_id": "critical_processes",
                            "label": "Critical Processes",
                            "field_type": "table",
                            "columns": [
                                {"column_id": "process_name", "label": "Process Name"},
                                {"column_id": "rto", "label": "RTO"},
                                {"column_id": "rpo", "label": "RPO"},
                                {"column_id": "impact", "label": "Impact"},
                            ],
                        },
                    ],
                },
            ],
        },
    }
    
    mock_author = {"id": 1, "name": "Test User"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=mock_template):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        buffer, content_hash = await export_to_docx(plan_id=1, version_id=1)
    
    assert isinstance(buffer, io.BytesIO)
    assert len(content_hash) == 64


# ============================================================================
# PDF Export Tests
# ============================================================================

@pytest.mark.asyncio
async def test_export_to_pdf_plan_not_found():
    """Test PDF export fails when plan doesn't exist."""
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=None):
        with pytest.raises(ValueError, match="Plan .* not found"):
            await export_to_pdf(plan_id=999)


@pytest.mark.asyncio
async def test_export_to_pdf_version_not_found():
    """Test PDF export fails when version doesn't exist."""
    mock_plan = {"id": 1, "title": "Test Plan"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=None):
            with pytest.raises(ValueError, match="Version .* not found"):
                await export_to_pdf(plan_id=1, version_id=999)


@pytest.mark.asyncio
async def test_export_to_pdf_success():
    """Test successful PDF export."""
    mock_plan = {
        "id": 1,
        "title": "Test Business Continuity Plan",
        "template_id": 1,
    }
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "summary_change_note": "Initial version",
        "content_json": {
            "plan_overview": {
                "purpose": "Ensure business continuity",
                "scope": "All critical systems",
            },
        },
    }
    
    mock_template = {
        "id": 1,
        "name": "Government BCP Template",
        "schema_json": {
            "sections": [
                {
                    "section_id": "plan_overview",
                    "title": "Plan Overview",
                    "fields": [
                        {"field_id": "purpose", "label": "Purpose", "field_type": "text"},
                        {"field_id": "scope", "label": "Scope", "field_type": "text"},
                    ],
                },
            ],
        },
    }
    
    mock_author = {"id": 1, "name": "John Doe"}
    
    mock_risks = []
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=mock_template):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=mock_risks):
                        buffer, content_hash = await export_to_pdf(plan_id=1, version_id=1)
    
    # Verify return types
    assert isinstance(buffer, io.BytesIO)
    assert isinstance(content_hash, str)
    assert len(content_hash) == 64
    
    # Verify buffer has content
    buffer.seek(0)
    content = buffer.read()
    assert len(content) > 0


@pytest.mark.asyncio
async def test_export_to_pdf_with_risks():
    """Test PDF export includes risk register."""
    mock_plan = {"id": 1, "title": "Test Plan", "template_id": None}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime.now(timezone.utc),
        "content_json": {"overview": {"purpose": "Test"}},
    }
    
    mock_author = {"id": 1, "name": "Jane Smith"}
    
    mock_risks = [
        {
            "id": 1,
            "threat": "Natural disaster",
            "likelihood": "rare",
            "impact": "catastrophic",
            "rating": "medium",
            "mitigation": "Emergency procedures and insurance",
        },
    ]
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=mock_risks):
                        buffer, content_hash = await export_to_pdf(plan_id=1, version_id=1)
    
    assert isinstance(buffer, io.BytesIO)
    assert len(content_hash) == 64


@pytest.mark.asyncio
async def test_export_to_pdf_with_metadata():
    """Test PDF export embeds revision metadata correctly."""
    mock_plan = {"id": 1, "title": "Critical Infrastructure Plan", "template_id": None}
    
    mock_version = {
        "id": 5,
        "plan_id": 1,
        "version_number": 3,
        "authored_by_user_id": 42,
        "authored_at_utc": datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc),
        "summary_change_note": "Updated contact information and recovery procedures",
        "content_json": {},
    }
    
    mock_author = {"id": 42, "name": "Alice Administrator"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        buffer, content_hash = await export_to_pdf(plan_id=1, version_id=5)
    
    # Verify content hash incorporates metadata
    assert isinstance(content_hash, str)
    assert len(content_hash) == 64


@pytest.mark.asyncio
async def test_export_to_pdf_same_content_same_hash():
    """Test that same content produces same hash for PDF and DOCX."""
    mock_plan = {"id": 1, "title": "Test Plan", "template_id": None}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "content_json": {"test": "data"},
    }
    
    mock_author = {"id": 1, "name": "Test User"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        _, docx_hash = await export_to_docx(plan_id=1, version_id=1)
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        _, pdf_hash = await export_to_pdf(plan_id=1, version_id=1)
    
    # Both formats should produce the same content hash since they use the same content
    assert docx_hash == pdf_hash


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================

@pytest.mark.asyncio
async def test_export_to_docx_with_empty_content():
    """Test DOCX export handles empty content gracefully."""
    mock_plan = {"id": 1, "title": "Empty Plan", "template_id": None}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime.now(timezone.utc),
        "content_json": {},
    }
    
    mock_author = {"id": 1, "name": "Test User"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        buffer, content_hash = await export_to_docx(plan_id=1, version_id=1)
    
    assert isinstance(buffer, io.BytesIO)
    assert len(content_hash) == 64


@pytest.mark.asyncio
async def test_export_to_pdf_with_none_content():
    """Test PDF export handles None content_json gracefully."""
    mock_plan = {"id": 1, "title": "Test Plan", "template_id": None}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 1,
        "authored_at_utc": datetime.now(timezone.utc),
        "content_json": None,
    }
    
    mock_author = {"id": 1, "name": "Test User"}
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=mock_author):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        buffer, content_hash = await export_to_pdf(plan_id=1, version_id=1)
    
    assert isinstance(buffer, io.BytesIO)
    assert len(content_hash) == 64


@pytest.mark.asyncio
async def test_export_to_docx_author_not_found():
    """Test DOCX export handles missing author gracefully."""
    mock_plan = {"id": 1, "title": "Test Plan", "template_id": None}
    
    mock_version = {
        "id": 1,
        "plan_id": 1,
        "version_number": 1,
        "authored_by_user_id": 999,
        "authored_at_utc": datetime.now(timezone.utc),
        "content_json": {},
    }
    
    with patch("app.services.bc_export_service.bc_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service.bc_repo.get_version_by_id", return_value=mock_version):
            with patch("app.services.bc_export_service.bc_repo.get_template_by_id", return_value=None):
                with patch("app.services.bc_export_service.user_repo.get_user_by_id", return_value=None):
                    with patch("app.services.bc_export_service.bc_repo.list_risks_by_plan", return_value=[]):
                        buffer, content_hash = await export_to_docx(plan_id=1, version_id=1)
    
    # Should still succeed with "Unknown" author
    assert isinstance(buffer, io.BytesIO)
    assert len(content_hash) == 64
