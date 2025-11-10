"""
Tests for BCP risk repository functions.
"""
import pytest
from datetime import datetime

from app.repositories import bcp as bcp_repo
from app.services.risk_calculator import calculate_risk


@pytest.mark.asyncio
class TestRiskRepository:
    """Tests for risk CRUD operations."""
    
    async def test_create_and_get_risk(self):
        """Test creating and retrieving a risk."""
        # Create a test plan first
        plan = await bcp_repo.create_plan(
            company_id=1,
            title="Test Plan"
        )
        
        # Calculate risk
        rating, severity = calculate_risk(2, 4)
        
        # Create risk
        risk = await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Test risk description",
            likelihood=2,
            impact=4,
            rating=rating,
            severity=severity,
            preventative_actions="Test preventative actions",
            contingency_plans="Test contingency plans"
        )
        
        assert risk is not None
        assert risk["description"] == "Test risk description"
        assert risk["likelihood"] == 2
        assert risk["impact"] == 4
        assert risk["rating"] == 8
        assert risk["severity"] == "Moderate"
        assert risk["preventative_actions"] == "Test preventative actions"
        assert risk["contingency_plans"] == "Test contingency plans"
        
        # Retrieve the risk
        retrieved = await bcp_repo.get_risk_by_id(risk["id"])
        assert retrieved is not None
        assert retrieved["id"] == risk["id"]
        assert retrieved["description"] == risk["description"]
    
    async def test_list_risks(self):
        """Test listing all risks for a plan."""
        # Create a test plan
        plan = await bcp_repo.create_plan(
            company_id=1,
            title="Test Plan"
        )
        
        # Create multiple risks
        rating1, severity1 = calculate_risk(1, 1)
        risk1 = await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Low risk",
            likelihood=1,
            impact=1,
            rating=rating1,
            severity=severity1
        )
        
        rating2, severity2 = calculate_risk(4, 4)
        risk2 = await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="High risk",
            likelihood=4,
            impact=4,
            rating=rating2,
            severity=severity2
        )
        
        # List risks
        risks = await bcp_repo.list_risks(plan["id"])
        
        assert len(risks) >= 2
        # Risks should be ordered by rating DESC
        risk_ids = [r["id"] for r in risks]
        assert risk2["id"] in risk_ids
        assert risk1["id"] in risk_ids
    
    async def test_update_risk(self):
        """Test updating a risk."""
        # Create a test plan and risk
        plan = await bcp_repo.create_plan(
            company_id=1,
            title="Test Plan"
        )
        
        rating, severity = calculate_risk(2, 2)
        risk = await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Original description",
            likelihood=2,
            impact=2,
            rating=rating,
            severity=severity
        )
        
        # Update the risk
        new_rating, new_severity = calculate_risk(3, 3)
        updated = await bcp_repo.update_risk(
            risk["id"],
            description="Updated description",
            likelihood=3,
            impact=3,
            rating=new_rating,
            severity=new_severity
        )
        
        assert updated is not None
        assert updated["description"] == "Updated description"
        assert updated["likelihood"] == 3
        assert updated["impact"] == 3
        assert updated["rating"] == 9
        assert updated["severity"] == "High"
    
    async def test_delete_risk(self):
        """Test deleting a risk."""
        # Create a test plan and risk
        plan = await bcp_repo.create_plan(
            company_id=1,
            title="Test Plan"
        )
        
        rating, severity = calculate_risk(2, 2)
        risk = await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Risk to delete",
            likelihood=2,
            impact=2,
            rating=rating,
            severity=severity
        )
        
        # Delete the risk
        deleted = await bcp_repo.delete_risk(risk["id"])
        assert deleted is True
        
        # Verify it's gone
        retrieved = await bcp_repo.get_risk_by_id(risk["id"])
        assert retrieved is None
    
    async def test_get_risk_heatmap_data(self):
        """Test getting heatmap data."""
        # Create a test plan
        plan = await bcp_repo.create_plan(
            company_id=1,
            title="Test Plan"
        )
        
        # Create risks in different cells
        rating1, severity1 = calculate_risk(1, 1)
        await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Risk 1",
            likelihood=1,
            impact=1,
            rating=rating1,
            severity=severity1
        )
        
        rating2, severity2 = calculate_risk(1, 1)
        await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Risk 2",
            likelihood=1,
            impact=1,
            rating=rating2,
            severity=severity2
        )
        
        rating3, severity3 = calculate_risk(2, 3)
        await bcp_repo.create_risk(
            plan_id=plan["id"],
            description="Risk 3",
            likelihood=2,
            impact=3,
            rating=rating3,
            severity=severity3
        )
        
        # Get heatmap data
        heatmap = await bcp_repo.get_risk_heatmap_data(plan["id"])
        
        assert "cells" in heatmap
        assert "total" in heatmap
        assert heatmap["total"] >= 3
        assert heatmap["cells"]["1,1"] >= 2
        assert heatmap["cells"]["2,3"] >= 1
    
    async def test_seed_example_risks(self):
        """Test seeding example risks."""
        # Create a test plan
        plan = await bcp_repo.create_plan(
            company_id=1,
            title="Test Plan"
        )
        
        # Seed example risks
        await bcp_repo.seed_example_risks(plan["id"])
        
        # List risks
        risks = await bcp_repo.list_risks(plan["id"])
        
        # Should have at least 2 example risks
        assert len(risks) >= 2
        
        # Check that they match the example data
        descriptions = [r["description"] for r in risks]
        assert any("production processes" in d.lower() for d in descriptions)
        assert any("burglary" in d.lower() for d in descriptions)
        
        # Verify the example risks have correct ratings
        for risk in risks:
            if "production processes" in risk["description"].lower():
                assert risk["likelihood"] == 2
                assert risk["impact"] == 4
                assert risk["rating"] == 8
                assert risk["severity"] == "Moderate"
            elif "burglary" in risk["description"].lower():
                assert risk["likelihood"] == 3
                assert risk["impact"] == 3
                assert risk["rating"] == 9
                assert risk["severity"] == "High"
