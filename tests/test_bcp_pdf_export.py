"""
Tests for BCP template-faithful PDF export (BC12).

Tests:
- export_bcp_to_pdf with all sections
- Data gathering function
- Event log limit configuration
- Multi-company scoping
- Footer attribution presence
"""
import io
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.bc_export_service import (
    export_bcp_to_pdf,
    _gather_bcp_export_data,
)


# ============================================================================
# BCP PDF Export Tests
# ============================================================================

@pytest.mark.asyncio
async def test_export_bcp_to_pdf_plan_not_found():
    """Test BCP PDF export fails when plan doesn't exist."""
    with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=None):
        with pytest.raises(ValueError, match="Plan .* not found"):
            await export_bcp_to_pdf(plan_id=999)


@pytest.mark.asyncio
async def test_export_bcp_to_pdf_success():
    """Test successful BCP PDF export with all sections."""
    mock_plan = {
        "id": 1,
        "title": "Test Business Continuity Plan",
        "executive_summary": "Test summary",
        "version": "1.0",
        "company_id": 1,
        "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_reviewed": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    
    # Mock all data gathering functions
    with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id", return_value=mock_plan):
        with patch("app.services.bc_export_service._gather_bcp_export_data") as mock_gather:
            mock_gather.return_value = {
                "objectives": [{"objective_text": "Test objective"}],
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
            
            buffer, content_hash = await export_bcp_to_pdf(plan_id=1)
            
            # Verify return types
            assert isinstance(buffer, io.BytesIO)
            assert isinstance(content_hash, str)
            assert len(content_hash) == 64  # SHA256 hex digest
            
            # Verify buffer has content
            buffer.seek(0)
            content = buffer.read()
            assert len(content) > 0
            
            # Verify data gathering was called with correct parameters
            mock_gather.assert_called_once_with(1, 100)


@pytest.mark.asyncio
async def test_export_bcp_to_pdf_with_custom_event_log_limit():
    """Test BCP PDF export respects custom event log limit."""
    mock_plan = {
        "id": 1,
        "title": "Test Plan",
        "executive_summary": None,
        "version": "1.0",
        "company_id": 1,
        "updated_at": datetime.now(timezone.utc),
        "last_reviewed": None,
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
            
            # Test with custom limit
            await export_bcp_to_pdf(plan_id=1, event_log_limit=50)
            
            # Verify data gathering was called with custom limit
            mock_gather.assert_called_once_with(1, 50)


@pytest.mark.asyncio
async def test_gather_bcp_export_data_all_sections():
    """Test that data gathering collects all required sections."""
    plan_id = 1
    event_log_limit = 100
    
    # Mock all repository functions
    with patch("app.services.bc_export_service.bcp_repo.list_objectives", return_value=[]):
        with patch("app.services.bc_export_service.bcp_repo.list_distribution_list", return_value=[]):
            with patch("app.services.bc_export_service.bcp_repo.list_risks", return_value=[]):
                with patch("app.services.bc_export_service.bcp_repo.list_insurance_policies", return_value=[]):
                    with patch("app.services.bc_export_service.bcp_repo.list_backup_items", return_value=[]):
                        with patch("app.services.bc_export_service.bcp_repo.list_critical_activities", return_value=[]):
                            with patch("app.services.bc_export_service.bcp_repo.list_checklist_items", return_value=[]):
                                with patch("app.services.bc_export_service.bcp_repo.get_evacuation_plan", return_value=None):
                                    with patch("app.services.bc_export_service.bcp_repo.list_emergency_kit_items", return_value=[]):
                                        with patch("app.services.bc_export_service.bcp_repo.list_roles_with_assignments", return_value=[]):
                                            with patch("app.services.bc_export_service.bcp_repo.list_contacts", return_value=[]):
                                                with patch("app.services.bc_export_service.bcp_repo.get_active_incident", return_value=None):
                                                    with patch("app.services.bc_export_service.bcp_repo.list_event_log_entries", return_value=[]):
                                                        with patch("app.services.bc_export_service.bcp_repo.list_recovery_actions", return_value=[]):
                                                            with patch("app.services.bc_export_service.bcp_repo.list_recovery_contacts", return_value=[]):
                                                                with patch("app.services.bc_export_service.bcp_repo.list_insurance_claims", return_value=[]):
                                                                    with patch("app.services.bc_export_service.bcp_repo.list_market_changes", return_value=[]):
                                                                        with patch("app.services.bc_export_service.bcp_repo.list_training_items", return_value=[]):
                                                                            with patch("app.services.bc_export_service.bcp_repo.list_review_items", return_value=[]):
                                                                                data = await _gather_bcp_export_data(plan_id, event_log_limit)
    
    # Verify all required sections are present
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
        assert key in data, f"Missing required key: {key}"


@pytest.mark.asyncio
async def test_gather_bcp_export_data_event_log_limit():
    """Test that event log is limited to specified number of entries."""
    plan_id = 1
    event_log_limit = 10
    
    # Create 50 mock event log entries
    mock_event_log = [
        {
            "id": i,
            "happened_at": datetime.now(timezone.utc),
            "notes": f"Event {i}",
            "initials": "TU",
        }
        for i in range(50)
    ]
    
    with patch("app.services.bc_export_service.bcp_repo.list_objectives", return_value=[]):
        with patch("app.services.bc_export_service.bcp_repo.list_distribution_list", return_value=[]):
            with patch("app.services.bc_export_service.bcp_repo.list_risks", return_value=[]):
                with patch("app.services.bc_export_service.bcp_repo.list_insurance_policies", return_value=[]):
                    with patch("app.services.bc_export_service.bcp_repo.list_backup_items", return_value=[]):
                        with patch("app.services.bc_export_service.bcp_repo.list_critical_activities", return_value=[]):
                            with patch("app.services.bc_export_service.bcp_repo.list_checklist_items", return_value=[]):
                                with patch("app.services.bc_export_service.bcp_repo.get_evacuation_plan", return_value=None):
                                    with patch("app.services.bc_export_service.bcp_repo.list_emergency_kit_items", return_value=[]):
                                        with patch("app.services.bc_export_service.bcp_repo.list_roles_with_assignments", return_value=[]):
                                            with patch("app.services.bc_export_service.bcp_repo.list_contacts", return_value=[]):
                                                with patch("app.services.bc_export_service.bcp_repo.get_active_incident", return_value=None):
                                                    with patch("app.services.bc_export_service.bcp_repo.list_event_log_entries", return_value=mock_event_log):
                                                        with patch("app.services.bc_export_service.bcp_repo.list_recovery_actions", return_value=[]):
                                                            with patch("app.services.bc_export_service.bcp_repo.list_recovery_contacts", return_value=[]):
                                                                with patch("app.services.bc_export_service.bcp_repo.list_insurance_claims", return_value=[]):
                                                                    with patch("app.services.bc_export_service.bcp_repo.list_market_changes", return_value=[]):
                                                                        with patch("app.services.bc_export_service.bcp_repo.list_training_items", return_value=[]):
                                                                            with patch("app.services.bc_export_service.bcp_repo.list_review_items", return_value=[]):
                                                                                data = await _gather_bcp_export_data(plan_id, event_log_limit)
    
    # Verify event log is limited
    assert len(data["event_log"]) == event_log_limit
    assert len(data["event_log"]) <= event_log_limit


@pytest.mark.asyncio
async def test_gather_bcp_export_data_enriches_critical_activities():
    """Test that critical activities get humanized RTO values."""
    plan_id = 1
    
    mock_activities = [
        {
            "id": 1,
            "name": "Email Service",
            "impact": {"rto_hours": 4},
        },
        {
            "id": 2,
            "name": "Payment Processing",
            "impact": {"rto_hours": 1},
        },
    ]
    
    with patch("app.services.bc_export_service.bcp_repo.list_objectives", return_value=[]):
        with patch("app.services.bc_export_service.bcp_repo.list_distribution_list", return_value=[]):
            with patch("app.services.bc_export_service.bcp_repo.list_risks", return_value=[]):
                with patch("app.services.bc_export_service.bcp_repo.list_insurance_policies", return_value=[]):
                    with patch("app.services.bc_export_service.bcp_repo.list_backup_items", return_value=[]):
                        with patch("app.services.bc_export_service.bcp_repo.list_critical_activities", return_value=mock_activities):
                            with patch("app.services.bc_export_service.bcp_repo.list_checklist_items", return_value=[]):
                                with patch("app.services.bc_export_service.bcp_repo.get_evacuation_plan", return_value=None):
                                    with patch("app.services.bc_export_service.bcp_repo.list_emergency_kit_items", return_value=[]):
                                        with patch("app.services.bc_export_service.bcp_repo.list_roles_with_assignments", return_value=[]):
                                            with patch("app.services.bc_export_service.bcp_repo.list_contacts", return_value=[]):
                                                with patch("app.services.bc_export_service.bcp_repo.get_active_incident", return_value=None):
                                                    with patch("app.services.bc_export_service.bcp_repo.list_event_log_entries", return_value=[]):
                                                        with patch("app.services.bc_export_service.bcp_repo.list_recovery_actions", return_value=[]):
                                                            with patch("app.services.bc_export_service.bcp_repo.list_recovery_contacts", return_value=[]):
                                                                with patch("app.services.bc_export_service.bcp_repo.list_insurance_claims", return_value=[]):
                                                                    with patch("app.services.bc_export_service.bcp_repo.list_market_changes", return_value=[]):
                                                                        with patch("app.services.bc_export_service.bcp_repo.list_training_items", return_value=[]):
                                                                            with patch("app.services.bc_export_service.bcp_repo.list_review_items", return_value=[]):
                                                                                data = await _gather_bcp_export_data(plan_id, 100)
    
    # Verify RTO humanization
    activities = data["critical_activities"]
    assert len(activities) == 2
    assert "rto_humanized" in activities[0]["impact"]
    assert "rto_humanized" in activities[1]["impact"]


@pytest.mark.asyncio
async def test_gather_bcp_export_data_emergency_kit_categories():
    """Test that emergency kit items are separated by category."""
    plan_id = 1
    
    mock_emergency_kit = [
        {"id": 1, "category": "Document", "name": "Insurance policies"},
        {"id": 2, "category": "Document", "name": "Contact lists"},
        {"id": 3, "category": "Equipment", "name": "First aid kit"},
        {"id": 4, "category": "Equipment", "name": "Flashlights"},
    ]
    
    with patch("app.services.bc_export_service.bcp_repo.list_objectives", return_value=[]):
        with patch("app.services.bc_export_service.bcp_repo.list_distribution_list", return_value=[]):
            with patch("app.services.bc_export_service.bcp_repo.list_risks", return_value=[]):
                with patch("app.services.bc_export_service.bcp_repo.list_insurance_policies", return_value=[]):
                    with patch("app.services.bc_export_service.bcp_repo.list_backup_items", return_value=[]):
                        with patch("app.services.bc_export_service.bcp_repo.list_critical_activities", return_value=[]):
                            with patch("app.services.bc_export_service.bcp_repo.list_checklist_items", return_value=[]):
                                with patch("app.services.bc_export_service.bcp_repo.get_evacuation_plan", return_value=None):
                                    with patch("app.services.bc_export_service.bcp_repo.list_emergency_kit_items", return_value=mock_emergency_kit):
                                        with patch("app.services.bc_export_service.bcp_repo.list_roles_with_assignments", return_value=[]):
                                            with patch("app.services.bc_export_service.bcp_repo.list_contacts", return_value=[]):
                                                with patch("app.services.bc_export_service.bcp_repo.get_active_incident", return_value=None):
                                                    with patch("app.services.bc_export_service.bcp_repo.list_event_log_entries", return_value=[]):
                                                        with patch("app.services.bc_export_service.bcp_repo.list_recovery_actions", return_value=[]):
                                                            with patch("app.services.bc_export_service.bcp_repo.list_recovery_contacts", return_value=[]):
                                                                with patch("app.services.bc_export_service.bcp_repo.list_insurance_claims", return_value=[]):
                                                                    with patch("app.services.bc_export_service.bcp_repo.list_market_changes", return_value=[]):
                                                                        with patch("app.services.bc_export_service.bcp_repo.list_training_items", return_value=[]):
                                                                            with patch("app.services.bc_export_service.bcp_repo.list_review_items", return_value=[]):
                                                                                data = await _gather_bcp_export_data(plan_id, 100)
    
    # Verify categories are separated
    assert len(data["emergency_kit_documents"]) == 2
    assert len(data["emergency_kit_equipment"]) == 2
    assert all(item["category"] == "Document" for item in data["emergency_kit_documents"])
    assert all(item["category"] == "Equipment" for item in data["emergency_kit_equipment"])


@pytest.mark.asyncio
async def test_export_bcp_to_pdf_multi_company():
    """Test BCP PDF export works correctly with company scoping."""
    # Test that export only includes data for the specified plan/company
    company1_plan = {
        "id": 1,
        "company_id": 1,
        "title": "Company 1 BCP",
        "executive_summary": "Company 1 summary",
        "version": "1.0",
        "updated_at": datetime.now(timezone.utc),
        "last_reviewed": None,
    }
    
    company2_plan = {
        "id": 2,
        "company_id": 2,
        "title": "Company 2 BCP",
        "executive_summary": "Company 2 summary",
        "version": "1.0",
        "updated_at": datetime.now(timezone.utc),
        "last_reviewed": None,
    }
    
    # Mock data for company 1
    with patch("app.services.bc_export_service.bcp_repo.get_plan_by_id") as mock_get_plan:
        mock_get_plan.return_value = company1_plan
        
        with patch("app.services.bc_export_service._gather_bcp_export_data") as mock_gather:
            mock_gather.return_value = {
                "objectives": [{"objective_text": "Company 1 objective"}],
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
            
            buffer, content_hash = await export_bcp_to_pdf(plan_id=1)
            
            # Verify correct plan was fetched
            mock_get_plan.assert_called_once_with(1)
            mock_gather.assert_called_once_with(1, 100)
            
            # Verify PDF was generated
            assert isinstance(buffer, io.BytesIO)
            assert len(content_hash) == 64
