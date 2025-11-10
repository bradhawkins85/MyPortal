"""
Risk calculation service for BCP risk assessment.

Implements the risk matrix calculation:
- Likelihood (1-4): 1=Low, 2=Medium (~every 10 years), 3=High (~once a year), 4=Very High (>once a year)
- Impact (1-4): 1=Low (minimal loss), 2=Moderate, 3=High (major loss), 4=Severe (could stop trading)
- Rating = Likelihood × Impact
- Severity bands: 1-4=Low, 5-8=Moderate, 9-12=High, 13-16=Severe
"""
from typing import Tuple


def calculate_risk(likelihood: int, impact: int) -> Tuple[int, str]:
    """
    Calculate risk rating and severity from likelihood and impact.
    
    Args:
        likelihood: Likelihood rating from 1-4
        impact: Impact rating from 1-4
        
    Returns:
        Tuple of (rating, severity) where:
            rating: int (1-16) = likelihood × impact
            severity: str ("Low", "Moderate", "High", "Severe")
            
    Raises:
        ValueError: If likelihood or impact is not in range 1-4
    """
    if not (1 <= likelihood <= 4):
        raise ValueError(f"Likelihood must be between 1 and 4, got {likelihood}")
    if not (1 <= impact <= 4):
        raise ValueError(f"Impact must be between 1 and 4, got {impact}")
    
    rating = likelihood * impact
    
    # Determine severity based on rating
    if rating <= 4:
        severity = "Low"
    elif rating <= 8:
        severity = "Moderate"
    elif rating <= 12:
        severity = "High"
    else:  # 13-16
        severity = "Severe"
    
    return rating, severity


def get_severity_band_info() -> dict:
    """
    Get information about severity bands for display in the UI.
    
    Returns:
        Dictionary with severity band details including:
            - band name
            - rating range
            - color
            - suggested action urgency
    """
    return {
        "Low": {
            "range": "1-4",
            "color": "#22c55e",  # green
            "action": "Monitor and review periodically",
            "description": "Low risk - acceptable with standard controls"
        },
        "Moderate": {
            "range": "5-8",
            "color": "#eab308",  # yellow
            "action": "Plan and implement mitigations",
            "description": "Moderate risk - requires active management"
        },
        "High": {
            "range": "9-12",
            "color": "#f97316",  # orange
            "action": "Urgent action required",
            "description": "High risk - needs immediate attention"
        },
        "Severe": {
            "range": "13-16",
            "color": "#dc2626",  # red
            "action": "Critical - immediate action required",
            "description": "Severe risk - critical business impact"
        }
    }


def get_likelihood_scale() -> list[dict]:
    """
    Get the likelihood scale for display in the UI.
    
    Returns:
        List of likelihood levels with descriptions
    """
    return [
        {"value": 1, "label": "Low", "description": "Rare - unlikely to occur"},
        {"value": 2, "label": "Medium", "description": "Occasional - approximately every 10 years"},
        {"value": 3, "label": "High", "description": "Likely - approximately once a year"},
        {"value": 4, "label": "Very High", "description": "Very likely - more than once a year"}
    ]


def get_impact_scale() -> list[dict]:
    """
    Get the impact scale for display in the UI.
    
    Returns:
        List of impact levels with descriptions
    """
    return [
        {"value": 1, "label": "Low", "description": "Minimal loss - minor disruption"},
        {"value": 2, "label": "Moderate", "description": "Moderate loss - noticeable impact"},
        {"value": 3, "label": "High", "description": "Major loss - significant disruption"},
        {"value": 4, "label": "Severe", "description": "Severe loss - could stop trading/major business impact"}
    ]
