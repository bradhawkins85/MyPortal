"""
Integration tests for BCP risk routes.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
class TestRiskRoutes:
    """Tests for risk management routes."""
    
    def test_risk_page_requires_authentication(self, client: TestClient):
        """Test that risk page requires authentication."""
        response = client.get("/bcp/risks")
        # Should redirect to login
        assert response.status_code in [302, 303, 401]
    
    def test_risk_calculator_module(self):
        """Test that risk calculator module works correctly."""
        from app.services.risk_calculator import (
            calculate_risk,
            get_severity_band_info,
            get_likelihood_scale,
            get_impact_scale,
        )
        
        # Test calculation
        rating, severity = calculate_risk(2, 4)
        assert rating == 8
        assert severity == "Moderate"
        
        # Test scales
        bands = get_severity_band_info()
        assert len(bands) == 4
        assert "Low" in bands
        assert "Moderate" in bands
        assert "High" in bands
        assert "Severe" in bands
        
        likelihood = get_likelihood_scale()
        assert len(likelihood) == 4
        
        impact = get_impact_scale()
        assert len(impact) == 4
    
    def test_risk_schema_validation(self):
        """Test risk schema validation."""
        from app.schemas.bcp_risk import RiskCreate, RiskUpdate
        
        # Valid risk creation
        risk_data = {
            "description": "Test risk",
            "likelihood": 2,
            "impact": 3,
            "preventative_actions": "Test actions",
            "contingency_plans": "Test plans"
        }
        risk = RiskCreate(**risk_data)
        assert risk.description == "Test risk"
        assert risk.likelihood == 2
        assert risk.impact == 3
        
        # Invalid likelihood
        with pytest.raises(Exception):
            RiskCreate(description="Test", likelihood=5, impact=2)
        
        # Invalid impact
        with pytest.raises(Exception):
            RiskCreate(description="Test", likelihood=2, impact=0)
    
    def test_severity_band_calculations(self):
        """Test that severity bands are calculated correctly."""
        from app.services.risk_calculator import calculate_risk
        
        # Test Low (1-4)
        assert calculate_risk(1, 1) == (1, "Low")
        assert calculate_risk(2, 2) == (4, "Low")
        
        # Test Moderate (5-8)
        assert calculate_risk(2, 3) == (6, "Moderate")
        assert calculate_risk(2, 4) == (8, "Moderate")
        
        # Test High (9-12)
        assert calculate_risk(3, 3) == (9, "High")
        assert calculate_risk(3, 4) == (12, "High")
        
        # Test Severe (13-16)
        assert calculate_risk(4, 4) == (16, "Severe")
