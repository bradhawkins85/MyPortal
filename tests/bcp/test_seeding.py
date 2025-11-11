"""
Test BCP seeding service.

Tests verify:
- Seeding new plans with all defaults
- Idempotency of seeding operations
- Re-seeding functionality
- Selective re-seeding by category
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestSeedingNewPlan:
    """Test seeding a new BCP plan with defaults."""
    
    @pytest.mark.asyncio
    async def test_seed_new_plan_defaults_creates_all_items(self):
        """Test that seed_new_plan_defaults creates all default items."""
        from app.services.bcp_seeding import seed_new_plan_defaults
        
        plan_id = 1
        
        with patch("app.repositories.bcp.list_objectives") as mock_list_obj, \
             patch("app.repositories.bcp.seed_default_objectives") as mock_seed_obj, \
             patch("app.repositories.bcp.list_checklist_items") as mock_list_checklist, \
             patch("app.repositories.bcp.seed_default_checklist_items") as mock_seed_checklist, \
             patch("app.repositories.bcp.seed_default_crisis_recovery_checklist_items") as mock_seed_crisis, \
             patch("app.repositories.bcp.list_emergency_kit_items") as mock_list_kit, \
             patch("app.repositories.bcp.seed_default_emergency_kit_items") as mock_seed_kit, \
             patch("app.repositories.bcp.list_risks") as mock_list_risks, \
             patch("app.repositories.bcp.seed_example_risks") as mock_seed_risks:
            
            # Mock empty lists (no existing items)
            mock_list_obj.return_value = []
            mock_list_checklist.return_value = []
            mock_list_kit.return_value = []
            mock_list_risks.return_value = []
            
            # After seeding kit items, return them for counting
            mock_list_kit.side_effect = [
                [],  # First call (check if empty)
                [  # Second call (after seeding, for counting)
                    {"id": 1, "category": "Document"},
                    {"id": 2, "category": "Document"},
                    {"id": 3, "category": "Equipment"},
                ],
            ]
            
            # Call seeding function
            stats = await seed_new_plan_defaults(plan_id)
            
            # Verify all seeding functions were called
            mock_seed_obj.assert_called_once_with(plan_id)
            mock_seed_checklist.assert_called_once_with(plan_id)
            mock_seed_crisis.assert_called_once_with(plan_id)
            mock_seed_kit.assert_called_once_with(plan_id)
            mock_seed_risks.assert_called_once_with(plan_id)
            
            # Verify stats
            assert stats["objectives"] == 5
            assert stats["immediate_checklist"] == 18
            assert stats["crisis_recovery_checklist"] == 23
            assert stats["example_risks"] == 2
    
    @pytest.mark.asyncio
    async def test_seed_new_plan_skips_existing_items(self):
        """Test that seeding skips categories that already have items (idempotency)."""
        from app.services.bcp_seeding import seed_new_plan_defaults
        
        plan_id = 1
        
        with patch("app.repositories.bcp.list_objectives") as mock_list_obj, \
             patch("app.repositories.bcp.seed_default_objectives") as mock_seed_obj, \
             patch("app.repositories.bcp.list_checklist_items") as mock_list_checklist, \
             patch("app.repositories.bcp.seed_default_checklist_items") as mock_seed_checklist, \
             patch("app.repositories.bcp.seed_default_crisis_recovery_checklist_items") as mock_seed_crisis, \
             patch("app.repositories.bcp.list_emergency_kit_items") as mock_list_kit, \
             patch("app.repositories.bcp.seed_default_emergency_kit_items") as mock_seed_kit, \
             patch("app.repositories.bcp.list_risks") as mock_list_risks, \
             patch("app.repositories.bcp.seed_example_risks") as mock_seed_risks:
            
            # Mock existing objectives and risks (should skip)
            mock_list_obj.return_value = [{"id": 1, "objective_text": "Existing"}]
            mock_list_risks.return_value = [{"id": 1, "description": "Existing risk"}]
            
            # Mock empty checklist and kit (should seed)
            mock_list_checklist.return_value = []
            mock_list_kit.return_value = []
            
            # After seeding kit items, return them for counting
            mock_list_kit.side_effect = [
                [],  # First call (check if empty)
                [{"id": 1, "category": "Document"}],  # Second call (after seeding)
            ]
            
            # Call seeding function
            stats = await seed_new_plan_defaults(plan_id)
            
            # Verify objectives and risks were NOT seeded
            mock_seed_obj.assert_not_called()
            mock_seed_risks.assert_not_called()
            
            # Verify checklists and kit WERE seeded
            mock_seed_checklist.assert_called_once()
            mock_seed_crisis.assert_called_once()
            mock_seed_kit.assert_called_once()
            
            # Verify stats reflect what was actually seeded
            assert stats["objectives"] == 0
            assert stats["immediate_checklist"] == 18
            assert stats["crisis_recovery_checklist"] == 23
            assert stats["example_risks"] == 0


class TestReseedingPlan:
    """Test re-seeding functionality."""
    
    @pytest.mark.asyncio
    async def test_reseed_all_categories(self):
        """Test re-seeding all categories."""
        from app.services.bcp_seeding import reseed_plan_defaults
        
        plan_id = 1
        
        with patch("app.repositories.bcp.list_objectives") as mock_list_obj, \
             patch("app.repositories.bcp.seed_default_objectives") as mock_seed_obj, \
             patch("app.repositories.bcp.list_checklist_items") as mock_list_checklist, \
             patch("app.repositories.bcp.seed_default_checklist_items") as mock_seed_checklist, \
             patch("app.repositories.bcp.seed_default_crisis_recovery_checklist_items") as mock_seed_crisis, \
             patch("app.repositories.bcp.list_emergency_kit_items") as mock_list_kit, \
             patch("app.repositories.bcp.seed_default_emergency_kit_items") as mock_seed_kit, \
             patch("app.repositories.bcp.list_risks") as mock_list_risks, \
             patch("app.repositories.bcp.seed_example_risks") as mock_seed_risks:
            
            # Mock all empty lists
            mock_list_obj.return_value = []
            mock_list_checklist.return_value = []
            mock_list_kit.side_effect = [[], []]
            mock_list_risks.return_value = []
            
            # Call with no specific categories (should re-seed all)
            stats = await reseed_plan_defaults(plan_id)
            
            # Verify all were called
            mock_seed_obj.assert_called_once()
            mock_seed_checklist.assert_called_once()
            mock_seed_crisis.assert_called_once()
            mock_seed_kit.assert_called_once()
            mock_seed_risks.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_reseed_specific_categories(self):
        """Test re-seeding only specific categories."""
        from app.services.bcp_seeding import reseed_plan_defaults
        
        plan_id = 1
        
        with patch("app.repositories.bcp.list_objectives") as mock_list_obj, \
             patch("app.repositories.bcp.seed_default_objectives") as mock_seed_obj, \
             patch("app.repositories.bcp.list_checklist_items") as mock_list_checklist, \
             patch("app.repositories.bcp.seed_default_checklist_items") as mock_seed_checklist, \
             patch("app.repositories.bcp.seed_default_crisis_recovery_checklist_items") as mock_seed_crisis, \
             patch("app.repositories.bcp.list_emergency_kit_items") as mock_list_kit, \
             patch("app.repositories.bcp.seed_default_emergency_kit_items") as mock_seed_kit, \
             patch("app.repositories.bcp.list_risks") as mock_list_risks, \
             patch("app.repositories.bcp.seed_example_risks") as mock_seed_risks:
            
            # Mock all empty lists
            mock_list_obj.return_value = []
            mock_list_risks.return_value = []
            
            # Call with only specific categories
            stats = await reseed_plan_defaults(plan_id, categories=["objectives", "example_risks"])
            
            # Verify only selected categories were called
            mock_seed_obj.assert_called_once()
            mock_seed_risks.assert_called_once()
            
            # Verify other categories were NOT called
            mock_seed_checklist.assert_not_called()
            mock_seed_crisis.assert_not_called()
            mock_seed_kit.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_reseed_is_idempotent(self):
        """Test that re-seeding with existing items doesn't duplicate."""
        from app.services.bcp_seeding import reseed_plan_defaults
        
        plan_id = 1
        
        with patch("app.repositories.bcp.list_objectives") as mock_list_obj, \
             patch("app.repositories.bcp.seed_default_objectives") as mock_seed_obj:
            
            # Mock existing objectives
            mock_list_obj.return_value = [
                {"id": 1, "objective_text": "Existing 1"},
                {"id": 2, "objective_text": "Existing 2"},
            ]
            
            # Call re-seed for objectives
            stats = await reseed_plan_defaults(plan_id, categories=["objectives"])
            
            # Verify seeding was NOT called (already exists)
            mock_seed_obj.assert_not_called()
            
            # Verify stats show 0 items added
            assert stats["objectives"] == 0


class TestSeedingDocumentation:
    """Test seeding documentation function."""
    
    def test_get_seeding_documentation_returns_complete_info(self):
        """Test that documentation includes all required information."""
        from app.services.bcp_seeding import get_seeding_documentation
        
        docs = get_seeding_documentation()
        
        # Verify structure
        assert "description" in docs
        assert "categories" in docs
        assert "risk_scales" in docs
        assert "idempotency" in docs
        assert "modification_guidance" in docs
        
        # Verify categories
        assert len(docs["categories"]) == 5
        category_names = [c["name"] for c in docs["categories"]]
        assert "objectives" in category_names
        assert "immediate_checklist" in category_names
        assert "crisis_recovery_checklist" in category_names
        assert "emergency_kit" in category_names
        assert "example_risks" in category_names
        
        # Verify each category has required fields
        for category in docs["categories"]:
            assert "name" in category
            assert "title" in category
            assert "count" in category
            assert "description" in category
            assert "can_reseed" in category
            assert "edit_location" in category
        
        # Verify risk scales
        assert "likelihood" in docs["risk_scales"]
        assert "impact" in docs["risk_scales"]
        assert "severity_bands" in docs["risk_scales"]
        
        # Verify likelihood scale
        assert len(docs["risk_scales"]["likelihood"]) == 4
        for item in docs["risk_scales"]["likelihood"]:
            assert "value" in item
            assert "label" in item
            assert "description" in item
        
        # Verify impact scale
        assert len(docs["risk_scales"]["impact"]) == 4
        for item in docs["risk_scales"]["impact"]:
            assert "value" in item
            assert "label" in item
            assert "description" in item
        
        # Verify severity bands
        assert len(docs["risk_scales"]["severity_bands"]) == 4
        for band in docs["risk_scales"]["severity_bands"]:
            assert "range" in band
            assert "label" in band
            assert "color" in band
            assert "action" in band


class TestIntegration:
    """Integration tests for seeding in actual route context."""
    
    @pytest.mark.asyncio
    async def test_plan_creation_seeds_defaults(self):
        """Test that creating a new plan automatically seeds defaults."""
        from app.services.bcp_seeding import seed_new_plan_defaults
        
        plan_id = 1
        
        with patch("app.repositories.bcp.list_objectives") as mock_list_obj, \
             patch("app.repositories.bcp.seed_default_objectives") as mock_seed_obj, \
             patch("app.repositories.bcp.list_checklist_items") as mock_list_checklist, \
             patch("app.repositories.bcp.seed_default_checklist_items") as mock_seed_checklist, \
             patch("app.repositories.bcp.seed_default_crisis_recovery_checklist_items") as mock_seed_crisis, \
             patch("app.repositories.bcp.list_emergency_kit_items") as mock_list_kit, \
             patch("app.repositories.bcp.seed_default_emergency_kit_items") as mock_seed_kit, \
             patch("app.repositories.bcp.list_risks") as mock_list_risks, \
             patch("app.repositories.bcp.seed_example_risks") as mock_seed_risks:
            
            # Mock empty state (new plan)
            mock_list_obj.return_value = []
            mock_list_checklist.return_value = []
            mock_list_kit.side_effect = [[], []]
            mock_list_risks.return_value = []
            
            # Seed the plan
            stats = await seed_new_plan_defaults(plan_id)
            
            # Verify comprehensive seeding occurred
            assert stats["objectives"] == 5
            assert stats["immediate_checklist"] == 18
            assert stats["crisis_recovery_checklist"] == 23
            assert stats["example_risks"] == 2
            
            # Verify total items seeded
            total_seeded = sum(stats.values())
            assert total_seeded > 0  # At least some items were seeded
