"""
Test BCP risk calculation with boundary cases for severity ratings.

Tests the risk_calculator module to ensure:
- Boundary cases for severity bands are correctly calculated
- Rating = likelihood × impact
- Severity bands: Low (1-4), Moderate (5-8), High (9-12), Severe (13-16)
"""
import pytest
from app.services.risk_calculator import calculate_risk


class TestRiskCalculationBoundaries:
    """Test boundary cases for risk severity calculation."""
    
    def test_low_severity_upper_boundary(self):
        """Test rating of 4 (upper boundary of Low severity)."""
        # Combinations that produce rating = 4
        rating, severity = calculate_risk(1, 4)
        assert rating == 4
        assert severity == "Low"
        
        rating, severity = calculate_risk(2, 2)
        assert rating == 4
        assert severity == "Low"
        
        rating, severity = calculate_risk(4, 1)
        assert rating == 4
        assert severity == "Low"
    
    def test_moderate_severity_upper_boundary(self):
        """Test rating of 8 (upper boundary of Moderate severity)."""
        # Combinations that produce rating = 8
        rating, severity = calculate_risk(2, 4)
        assert rating == 8
        assert severity == "Moderate"
        
        rating, severity = calculate_risk(4, 2)
        assert rating == 8
        assert severity == "Moderate"
    
    def test_high_severity_upper_boundary(self):
        """Test rating of 12 (upper boundary of High severity)."""
        # Combinations that produce rating = 12
        rating, severity = calculate_risk(3, 4)
        assert rating == 12
        assert severity == "High"
        
        rating, severity = calculate_risk(4, 3)
        assert rating == 12
        assert severity == "High"
    
    def test_severe_severity_upper_boundary(self):
        """Test rating of 16 (upper boundary of Severe severity)."""
        # Combination that produces rating = 16
        rating, severity = calculate_risk(4, 4)
        assert rating == 16
        assert severity == "Severe"
    
    def test_boundary_transitions(self):
        """Test transitions between severity bands."""
        # Low to Moderate transition (4 -> 5)
        rating4, severity4 = calculate_risk(2, 2)
        assert severity4 == "Low"
        
        # rating = 6 is Moderate (> 4)
        rating6, severity6 = calculate_risk(2, 3)
        assert severity6 == "Moderate"
        
        # Moderate to High transition (8 -> 9)
        rating8, severity8 = calculate_risk(2, 4)
        assert severity8 == "Moderate"
        
        rating9, severity9 = calculate_risk(3, 3)
        assert severity9 == "High"
        
        # High to Severe transition (12 -> 13)
        rating12, severity12 = calculate_risk(3, 4)
        assert severity12 == "High"
        
        # rating = 16 is Severe (> 12)
        rating16, severity16 = calculate_risk(4, 4)
        assert severity16 == "Severe"
    
    def test_all_severity_bands_coverage(self):
        """Test all four severity bands are correctly assigned."""
        # Low (1-4)
        assert calculate_risk(1, 1)[1] == "Low"  # rating = 1
        assert calculate_risk(1, 2)[1] == "Low"  # rating = 2
        assert calculate_risk(1, 3)[1] == "Low"  # rating = 3
        assert calculate_risk(1, 4)[1] == "Low"  # rating = 4
        
        # Moderate (5-8)
        assert calculate_risk(2, 3)[1] == "Moderate"  # rating = 6
        assert calculate_risk(2, 4)[1] == "Moderate"  # rating = 8
        assert calculate_risk(3, 2)[1] == "Moderate"  # rating = 6
        
        # High (9-12)
        assert calculate_risk(3, 3)[1] == "High"  # rating = 9
        assert calculate_risk(3, 4)[1] == "High"  # rating = 12
        assert calculate_risk(4, 3)[1] == "High"  # rating = 12
        
        # Severe (13-16)
        assert calculate_risk(4, 4)[1] == "Severe"  # rating = 16
    
    def test_rating_calculation(self):
        """Test that rating is correctly calculated as likelihood × impact."""
        for likelihood in range(1, 5):
            for impact in range(1, 5):
                rating, severity = calculate_risk(likelihood, impact)
                assert rating == likelihood * impact
    
    def test_invalid_likelihood_raises_error(self):
        """Test that invalid likelihood values raise ValueError."""
        with pytest.raises(ValueError, match="Likelihood must be between 1 and 4"):
            calculate_risk(0, 2)
        
        with pytest.raises(ValueError, match="Likelihood must be between 1 and 4"):
            calculate_risk(5, 2)
    
    def test_invalid_impact_raises_error(self):
        """Test that invalid impact values raise ValueError."""
        with pytest.raises(ValueError, match="Impact must be between 1 and 4"):
            calculate_risk(2, 0)
        
        with pytest.raises(ValueError, match="Impact must be between 1 and 4"):
            calculate_risk(2, 5)


class TestRiskCalculatorUtilityFunctions:
    """Test utility functions in risk_calculator module."""
    
    def test_get_severity_band_info(self):
        """Test that severity band info is correctly structured."""
        from app.services.risk_calculator import get_severity_band_info
        
        bands = get_severity_band_info()
        
        # Check all four bands exist
        assert len(bands) == 4
        assert "Low" in bands
        assert "Moderate" in bands
        assert "High" in bands
        assert "Severe" in bands
        
        # Check each band has required fields
        for band_name, band_info in bands.items():
            assert "range" in band_info
            assert "color" in band_info
            assert "action" in band_info
            assert "description" in band_info
        
        # Verify correct ranges
        assert bands["Low"]["range"] == "1-4"
        assert bands["Moderate"]["range"] == "5-8"
        assert bands["High"]["range"] == "9-12"
        assert bands["Severe"]["range"] == "13-16"
    
    def test_get_likelihood_scale(self):
        """Test that likelihood scale is correctly structured."""
        from app.services.risk_calculator import get_likelihood_scale
        
        scale = get_likelihood_scale()
        
        # Check we have 4 levels
        assert len(scale) == 4
        
        # Check each level has required fields
        for level in scale:
            assert "value" in level
            assert "label" in level
            assert "description" in level
        
        # Check values are 1-4
        values = [level["value"] for level in scale]
        assert values == [1, 2, 3, 4]
    
    def test_get_impact_scale(self):
        """Test that impact scale is correctly structured."""
        from app.services.risk_calculator import get_impact_scale
        
        scale = get_impact_scale()
        
        # Check we have 4 levels
        assert len(scale) == 4
        
        # Check each level has required fields
        for level in scale:
            assert "value" in level
            assert "label" in level
            assert "description" in level
        
        # Check values are 1-4
        values = [level["value"] for level in scale]
        assert values == [1, 2, 3, 4]
