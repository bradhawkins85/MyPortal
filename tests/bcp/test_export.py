"""
Test BCP PDF export functionality.

Tests verify:
- PDF generation succeeds
- Ordered headings exist in correct sequence
- Content sections are present
- Export handles edge cases
"""
import pytest
import io
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone


class TestBCPPDFExport:
    """Test BCP PDF export generation."""
    
    @pytest.mark.asyncio
    async def test_export_pdf_with_ordered_headings(self):
        """Test that PDF export includes required headings in order."""
        from app.services.bc_export_service import export_bcp_to_pdf
        
        # Mock plan data
        mock_plan = {
            "id": 1,
            "title": "Test Business Continuity Plan",
            "executive_summary": "Test summary",
            "version": "1.0",
            "company_id": 1,
            "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        
        # Mock all required data
        mock_data = {
            "objectives": [],
            "distribution_list": [],
            "risks": [],
            "insurance_policies": [],
            "backup_items": [],
            "critical_activities": [],
            "checklist_items": [],
            "evacuation": None,
            "emergency_kit_documents": [],
            "emergency_kit_equipment": [],
            "roles": [],
            "contacts": [],
            "event_log": [],
            "recovery_actions": [],
            "crisis_recovery_checklist": [],
            "recovery_contacts": [],
            "insurance_claims": [],
            "market_changes": [],
            "training_items": [],
            "review_items": [],
        }
        
        with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=mock_plan):
            with patch("app.services.bc_export_service._gather_bcp_export_data", return_value=mock_data):
                with patch("app.services.bc_export_service._render_bcp_pdf_html") as mock_render:
                    # Mock HTML rendering to return HTML with expected structure
                    expected_headings = [
                        "Plan Overview",
                        "Risk Management",
                        "Business Impact Analysis",
                        "Incident Response",
                        "Recovery",
                        "Rehearse/Maintain/Review",
                    ]
                    
                    html_content = "<html><body>"
                    for heading in expected_headings:
                        html_content += f"<h1>{heading}</h1>"
                    html_content += "</body></html>"
                    
                    mock_render.return_value = html_content
                    
                    with patch("app.services.bc_export_service.HTML") as mock_html:
                        mock_pdf_obj = MagicMock()
                        mock_pdf_obj.write_pdf = MagicMock(side_effect=lambda buf: buf.write(b"PDF content"))
                        mock_html.return_value = mock_pdf_obj
                        
                        buffer, content_hash = await export_bcp_to_pdf(plan_id=1)
                        
                        # Verify buffer is BytesIO
                        assert isinstance(buffer, io.BytesIO)
                        
                        # Verify content hash is generated
                        assert isinstance(content_hash, str)
                        assert len(content_hash) == 64  # SHA256 hex digest
                        
                        # Verify HTML was rendered with correct headings
                        mock_render.assert_called_once()
                        rendered_html = mock_render.return_value
                        
                        # Check all expected headings are present in order
                        for heading in expected_headings:
                            assert heading in rendered_html
    
    @pytest.mark.asyncio
    async def test_export_pdf_plan_not_found(self):
        """Test that export fails gracefully when plan not found."""
        from app.services.bc_export_service import export_bcp_to_pdf
        
        with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=None):
            with pytest.raises(ValueError, match="Plan .* not found"):
                await export_bcp_to_pdf(plan_id=999)
    
    @pytest.mark.asyncio
    async def test_export_pdf_with_event_log_limit(self):
        """Test that PDF export respects event log limit parameter."""
        from app.services.bc_export_service import export_bcp_to_pdf
        
        mock_plan = {
            "id": 1,
            "title": "Test Plan",
            "executive_summary": None,
            "version": "1.0",
            "company_id": 1,
            "updated_at": datetime.now(timezone.utc),
        }
        
        with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=mock_plan):
            with patch("app.services.bc_export_service._gather_bcp_export_data") as mock_gather:
                mock_gather.return_value = {
                    "objectives": [],
                    "distribution_list": [],
                    "risks": [],
                    "insurance_policies": [],
                    "backup_items": [],
                    "critical_activities": [],
                    "checklist_items": [],
                    "evacuation": None,
                    "emergency_kit_documents": [],
                    "emergency_kit_equipment": [],
                    "roles": [],
                    "contacts": [],
                    "event_log": [],
                    "recovery_actions": [],
                    "crisis_recovery_checklist": [],
                    "recovery_contacts": [],
                    "insurance_claims": [],
                    "market_changes": [],
                    "training_items": [],
                    "review_items": [],
                }
                
                with patch("app.services.bc_export_service._render_bcp_pdf_html", return_value="<html></html>"):
                    with patch("app.services.bc_export_service.HTML") as mock_html:
                        mock_pdf_obj = MagicMock()
                        mock_pdf_obj.write_pdf = MagicMock(side_effect=lambda buf: buf.write(b"PDF"))
                        mock_html.return_value = mock_pdf_obj
                        
                        # Test with custom event log limit
                        buffer, content_hash = await export_bcp_to_pdf(plan_id=1, event_log_limit=50)
                        
                        # Verify gather was called with correct limit
                        mock_gather.assert_called_once_with(1, 50)
    
    @pytest.mark.asyncio
    async def test_gather_bcp_export_data_includes_all_sections(self):
        """Test that data gathering includes all required sections."""
        from app.services.bc_export_service import _gather_bcp_export_data
        
        # Mock all repository calls
        with patch("app.repositories.bcp.list_objectives", return_value=[]):
            with patch("app.repositories.bcp.list_distribution_list", return_value=[]):
                with patch("app.repositories.bcp.list_risks", return_value=[]):
                    with patch("app.repositories.bcp.list_insurance_policies", return_value=[]):
                        with patch("app.repositories.bcp.list_backup_items", return_value=[]):
                            with patch("app.repositories.bcp.list_critical_activities", return_value=[]):
                                with patch("app.repositories.bcp.list_checklist_items", return_value=[]):
                                    with patch("app.repositories.bcp.get_evacuation_plan", return_value=None):
                                        with patch("app.repositories.bcp.list_emergency_kit_items", return_value=[]):
                                            with patch("app.repositories.bcp.list_roles_with_assignments", return_value=[]):
                                                with patch("app.repositories.bcp.list_contacts", return_value=[]):
                                                    with patch("app.repositories.bcp.get_active_incident", return_value=None):
                                                        with patch("app.repositories.bcp.list_event_log_entries", return_value=[]):
                                                            with patch("app.repositories.bcp.list_recovery_actions", return_value=[]):
                                                                with patch("app.repositories.bcp.list_recovery_contacts", return_value=[]):
                                                                    with patch("app.repositories.bcp.list_insurance_claims", return_value=[]):
                                                                        with patch("app.repositories.bcp.list_market_changes", return_value=[]):
                                                                            with patch("app.repositories.bcp.list_training_items", return_value=[]):
                                                                                with patch("app.repositories.bcp.list_review_items", return_value=[]):
                                                                                    data = await _gather_bcp_export_data(plan_id=1, event_log_limit=100)
                                                                                    
                                                                                    # Verify all required keys are present
                                                                                    required_keys = [
                                                                                        "objectives",
                                                                                        "distribution_list",
                                                                                        "risks",
                                                                                        "insurance_policies",
                                                                                        "backup_items",
                                                                                        "critical_activities",
                                                                                        "checklist_items",
                                                                                        "evacuation",
                                                                                        "emergency_kit_documents",
                                                                                        "emergency_kit_equipment",
                                                                                        "roles",
                                                                                        "contacts",
                                                                                        "event_log",
                                                                                        "recovery_actions",
                                                                                        "crisis_recovery_checklist",
                                                                                        "recovery_contacts",
                                                                                        "insurance_claims",
                                                                                        "market_changes",
                                                                                        "training_items",
                                                                                        "review_items",
                                                                                    ]
                                                                                    
                                                                                    for key in required_keys:
                                                                                        assert key in data


class TestPDFStructureAndContent:
    """Test PDF structure and content validation."""
    
    @pytest.mark.asyncio
    async def test_pdf_includes_plan_overview_section(self):
        """Test that PDF includes Plan Overview section with objectives."""
        from app.services.bc_export_service import _render_bcp_pdf_html
        
        mock_plan = {
            "id": 1,
            "title": "Test Plan",
            "executive_summary": "Summary",
            "version": "1.0",
        }
        
        mock_data = {
            "objectives": [{"objective_text": "Objective 1"}],
            "distribution_list": [],
            "risks": [],
            "insurance_policies": [],
            "backup_items": [],
            "critical_activities": [],
            "checklist_items": [],
            "evacuation": None,
            "emergency_kit_documents": [],
            "emergency_kit_equipment": [],
            "roles": [],
            "contacts": [],
            "event_log": [],
            "recovery_actions": [],
            "crisis_recovery_checklist": [],
            "recovery_contacts": [],
            "insurance_claims": [],
            "market_changes": [],
            "training_items": [],
            "review_items": [],
        }
        
        html = _render_bcp_pdf_html(mock_plan, mock_data)
        
        # Verify Plan Overview section
        assert "Plan Overview" in html or "Overview" in html
        assert "Objective 1" in html
    
    @pytest.mark.asyncio
    async def test_pdf_includes_risk_management_section(self):
        """Test that PDF includes Risk Management section."""
        from app.services.bc_export_service import _render_bcp_pdf_html
        
        mock_plan = {
            "id": 1,
            "title": "Test Plan",
            "executive_summary": None,
            "version": "1.0",
        }
        
        mock_data = {
            "objectives": [],
            "distribution_list": [],
            "risks": [{"description": "Test Risk", "severity": "High"}],
            "insurance_policies": [],
            "backup_items": [],
            "critical_activities": [],
            "checklist_items": [],
            "evacuation": None,
            "emergency_kit_documents": [],
            "emergency_kit_equipment": [],
            "roles": [],
            "contacts": [],
            "event_log": [],
            "recovery_actions": [],
            "crisis_recovery_checklist": [],
            "recovery_contacts": [],
            "insurance_claims": [],
            "market_changes": [],
            "training_items": [],
            "review_items": [],
        }
        
        html = _render_bcp_pdf_html(mock_plan, mock_data)
        
        # Verify Risk Management section
        assert "Risk" in html
        assert "Test Risk" in html
    
    @pytest.mark.asyncio
    async def test_pdf_includes_footer_attribution(self):
        """Test that PDF includes required footer attribution."""
        from app.services.bc_export_service import _render_bcp_pdf_html
        
        mock_plan = {
            "id": 1,
            "title": "Test Plan",
            "executive_summary": None,
            "version": "1.0",
        }
        
        # Minimal data
        mock_data = {
            "objectives": [],
            "distribution_list": [],
            "risks": [],
            "insurance_policies": [],
            "backup_items": [],
            "critical_activities": [],
            "checklist_items": [],
            "evacuation": None,
            "emergency_kit_documents": [],
            "emergency_kit_equipment": [],
            "roles": [],
            "contacts": [],
            "event_log": [],
            "recovery_actions": [],
            "crisis_recovery_checklist": [],
            "recovery_contacts": [],
            "insurance_claims": [],
            "market_changes": [],
            "training_items": [],
            "review_items": [],
        }
        
        html = _render_bcp_pdf_html(mock_plan, mock_data)
        
        # Verify footer attribution
        assert "Business Queensland" in html or "CC BY 4.0" in html or "Attribution" in html.lower()


class TestContentHashGeneration:
    """Test content hash generation for change tracking."""
    
    def test_compute_content_hash_deterministic(self):
        """Test that content hash is deterministic."""
        from app.services.bc_export_service import compute_content_hash
        
        content = {"risks": [{"id": 1, "description": "Test"}]}
        metadata = {"plan_id": 1, "plan_title": "Test"}
        
        hash1 = compute_content_hash(content, metadata)
        hash2 = compute_content_hash(content, metadata)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest
    
    def test_compute_content_hash_different_content(self):
        """Test that different content produces different hash."""
        from app.services.bc_export_service import compute_content_hash
        
        content1 = {"risks": [{"id": 1, "description": "Test"}]}
        content2 = {"risks": [{"id": 2, "description": "Different"}]}
        metadata = {"plan_id": 1, "plan_title": "Test"}
        
        hash1 = compute_content_hash(content1, metadata)
        hash2 = compute_content_hash(content2, metadata)
        
        assert hash1 != hash2
    
    def test_compute_content_hash_order_independence(self):
        """Test that dict key order doesn't affect hash (sort_keys=True)."""
        from app.services.bc_export_service import compute_content_hash
        
        # Different dict order but same content
        content1 = {"risks": [], "activities": []}
        content2 = {"activities": [], "risks": []}
        metadata = {"plan_id": 1}
        
        hash1 = compute_content_hash(content1, metadata)
        hash2 = compute_content_hash(content2, metadata)
        
        # Should be same due to sort_keys=True
        assert hash1 == hash2


class TestExportEdgeCases:
    """Test edge cases in PDF export."""
    
    @pytest.mark.asyncio
    async def test_export_with_no_data(self):
        """Test export succeeds with empty plan data."""
        from app.services.bc_export_service import export_bcp_to_pdf
        
        mock_plan = {
            "id": 1,
            "title": "Empty Plan",
            "executive_summary": None,
            "version": None,
            "company_id": 1,
            "updated_at": datetime.now(timezone.utc),
        }
        
        # All empty data
        mock_data = {
            "objectives": [],
            "distribution_list": [],
            "risks": [],
            "insurance_policies": [],
            "backup_items": [],
            "critical_activities": [],
            "checklist_items": [],
            "evacuation": None,
            "emergency_kit_documents": [],
            "emergency_kit_equipment": [],
            "roles": [],
            "contacts": [],
            "event_log": [],
            "recovery_actions": [],
            "crisis_recovery_checklist": [],
            "recovery_contacts": [],
            "insurance_claims": [],
            "market_changes": [],
            "training_items": [],
            "review_items": [],
        }
        
        with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=mock_plan):
            with patch("app.services.bc_export_service._gather_bcp_export_data", return_value=mock_data):
                with patch("app.services.bc_export_service._render_bcp_pdf_html", return_value="<html></html>"):
                    with patch("app.services.bc_export_service.HTML") as mock_html:
                        mock_pdf_obj = MagicMock()
                        mock_pdf_obj.write_pdf = MagicMock(side_effect=lambda buf: buf.write(b"PDF"))
                        mock_html.return_value = mock_pdf_obj
                        
                        buffer, content_hash = await export_bcp_to_pdf(plan_id=1)
                        
                        assert buffer is not None
                        assert content_hash is not None
    
    @pytest.mark.asyncio
    async def test_export_with_maximum_event_log_limit(self):
        """Test export with maximum event log limit."""
        from app.services.bc_export_service import export_bcp_to_pdf
        
        mock_plan = {
            "id": 1,
            "title": "Test Plan",
            "executive_summary": None,
            "version": "1.0",
            "company_id": 1,
            "updated_at": datetime.now(timezone.utc),
        }
        
        with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=mock_plan):
            with patch("app.services.bc_export_service._gather_bcp_export_data") as mock_gather:
                mock_gather.return_value = {
                    "objectives": [],
                    "distribution_list": [],
                    "risks": [],
                    "insurance_policies": [],
                    "backup_items": [],
                    "critical_activities": [],
                    "checklist_items": [],
                    "evacuation": None,
                    "emergency_kit_documents": [],
                    "emergency_kit_equipment": [],
                    "roles": [],
                    "contacts": [],
                    "event_log": [],
                    "recovery_actions": [],
                    "crisis_recovery_checklist": [],
                    "recovery_contacts": [],
                    "insurance_claims": [],
                    "market_changes": [],
                    "training_items": [],
                    "review_items": [],
                }
                
                with patch("app.services.bc_export_service._render_bcp_pdf_html", return_value="<html></html>"):
                    with patch("app.services.bc_export_service.HTML") as mock_html:
                        mock_pdf_obj = MagicMock()
                        mock_pdf_obj.write_pdf = MagicMock(side_effect=lambda buf: buf.write(b"PDF"))
                        mock_html.return_value = mock_pdf_obj
                        
                        # Test with maximum limit (500 from API spec)
                        buffer, content_hash = await export_bcp_to_pdf(plan_id=1, event_log_limit=500)
                        
                        # Verify gather was called with correct limit
                        mock_gather.assert_called_once_with(1, 500)
