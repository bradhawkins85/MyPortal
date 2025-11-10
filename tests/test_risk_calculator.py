"""
Tests for risk calculation service.
"""
import pytest

from app.services.risk_calculator import (
    calculate_risk,
    get_severity_band_info,
    get_likelihood_scale,
    get_impact_scale,
)


class TestCalculateRisk:
    """Tests for the calculate_risk function."""
    
    def test_calculate_risk_low_severity(self):
        """Test calculation for low severity risks (rating 1-4)."""
        # 1x1 = 1 (Low)
        rating, severity = calculate_risk(1, 1)
        assert rating == 1
        assert severity == "Low"
        
        # 1x2 = 2 (Low)
        rating, severity = calculate_risk(1, 2)
        assert rating == 2
        assert severity == "Low"
        
        # 2x2 = 4 (Low)
        rating, severity = calculate_risk(2, 2)
        assert rating == 4
        assert severity == "Low"
    
    def test_calculate_risk_moderate_severity(self):
        """Test calculation for moderate severity risks (rating 5-8)."""
        # 2x3 = 6 (Moderate)
        rating, severity = calculate_risk(2, 3)
        assert rating == 6
        assert severity == "Moderate"
        
        # 2x4 = 8 (Moderate) - boundary case
        rating, severity = calculate_risk(2, 4)
        assert rating == 8
        assert severity == "Moderate"
        
        # 4x2 = 8 (Moderate) - boundary case reversed
        rating, severity = calculate_risk(4, 2)
        assert rating == 8
        assert severity == "Moderate"
    
    def test_calculate_risk_high_severity(self):
        """Test calculation for high severity risks (rating 9-12)."""
        # 3x3 = 9 (High) - boundary case
        rating, severity = calculate_risk(3, 3)
        assert rating == 9
        assert severity == "High"
        
        # 3x4 = 12 (High) - boundary case
        rating, severity = calculate_risk(3, 4)
        assert rating == 12
        assert severity == "High"
        
        # 4x3 = 12 (High) - boundary case reversed
        rating, severity = calculate_risk(4, 3)
        assert rating == 12
        assert severity == "High"
    
    def test_calculate_risk_severe_severity(self):
        """Test calculation for severe severity risks (rating 13-16)."""
        # 4x4 = 16 (Severe) - maximum
        rating, severity = calculate_risk(4, 4)
        assert rating == 16
        assert severity == "Severe"
    
    def test_calculate_risk_example_cases(self):
        """Test the example cases from the specification."""
        # Production interruption: L=2, I=4 => 8 (Moderate per spec, but High per bands)
        # Note: The spec says "High" but rating 8 falls in Moderate band (5-8)
        # We're implementing the bands as specified in the acceptance criteria
        rating, severity = calculate_risk(2, 4)
        assert rating == 8
        assert severity == "Moderate"
        
        # Burglary: L=3, I=3 => 9 (High)
        rating, severity = calculate_risk(3, 3)
        assert rating == 9
        assert severity == "High"
    
    def test_calculate_risk_invalid_likelihood(self):
        """Test that invalid likelihood values raise ValueError."""
        with pytest.raises(ValueError, match="Likelihood must be between 1 and 4"):
            calculate_risk(0, 2)
        
        with pytest.raises(ValueError, match="Likelihood must be between 1 and 4"):
            calculate_risk(5, 2)
        
        with pytest.raises(ValueError, match="Likelihood must be between 1 and 4"):
            calculate_risk(-1, 2)
    
    def test_calculate_risk_invalid_impact(self):
        """Test that invalid impact values raise ValueError."""
        with pytest.raises(ValueError, match="Impact must be between 1 and 4"):
            calculate_risk(2, 0)
        
        with pytest.raises(ValueError, match="Impact must be between 1 and 4"):
            calculate_risk(2, 5)
        
        with pytest.raises(ValueError, match="Impact must be between 1 and 4"):
            calculate_risk(2, -1)


class TestSeverityBandInfo:
    """Tests for severity band information."""
    
    def test_get_severity_band_info_structure(self):
        """Test that severity band info has correct structure."""
        bands = get_severity_band_info()
        
        assert "Low" in bands
        assert "Moderate" in bands
        assert "High" in bands
        assert "Severe" in bands
        
        for severity, info in bands.items():
            assert "range" in info
            assert "color" in info
            assert "action" in info
            assert "description" in info
    
    def test_get_severity_band_info_ranges(self):
        """Test that severity band ranges are correct."""
        bands = get_severity_band_info()
        
        assert bands["Low"]["range"] == "1-4"
        assert bands["Moderate"]["range"] == "5-8"
        assert bands["High"]["range"] == "9-12"
        assert bands["Severe"]["range"] == "13-16"


class TestLikelihoodScale:
    """Tests for likelihood scale."""
    
    def test_get_likelihood_scale_structure(self):
        """Test that likelihood scale has correct structure."""
        scale = get_likelihood_scale()
        
        assert len(scale) == 4
        
        for item in scale:
            assert "value" in item
            assert "label" in item
            assert "description" in item
    
    def test_get_likelihood_scale_values(self):
        """Test that likelihood scale has correct values."""
        scale = get_likelihood_scale()
        values = [item["value"] for item in scale]
        
        assert values == [1, 2, 3, 4]
    
    def test_get_likelihood_scale_labels(self):
        """Test that likelihood scale has appropriate labels."""
        scale = get_likelihood_scale()
        labels = [item["label"] for item in scale]
        
        assert "Low" in labels
        assert "Medium" in labels
        assert "High" in labels
        assert "Very High" in labels


class TestImpactScale:
    """Tests for impact scale."""
    
    def test_get_impact_scale_structure(self):
        """Test that impact scale has correct structure."""
        scale = get_impact_scale()
        
        assert len(scale) == 4
        
        for item in scale:
            assert "value" in item
            assert "label" in item
            assert "description" in item
    
    def test_get_impact_scale_values(self):
        """Test that impact scale has correct values."""
        scale = get_impact_scale()
        values = [item["value"] for item in scale]
        
        assert values == [1, 2, 3, 4]
    
    def test_get_impact_scale_labels(self):
        """Test that impact scale has appropriate labels."""
        scale = get_impact_scale()
        labels = [item["label"] for item in scale]
        
        assert "Low" in labels
        assert "Moderate" in labels
        assert "High" in labels
        assert "Severe" in labels
