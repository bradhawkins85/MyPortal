"""
Tests for time utility functions, especially RTO humanization.
"""
import pytest

# Import using importlib to avoid triggering app.__init__
import importlib.util
import os

# Load the module directly
spec = importlib.util.spec_from_file_location(
    "time_utils",
    os.path.join(os.path.dirname(__file__), "..", "app", "services", "time_utils.py")
)
time_utils_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(time_utils_module)

# Extract the function under test
humanize_hours = time_utils_module.humanize_hours


def test_humanize_hours_none():
    """Test humanizing None returns dash."""
    assert humanize_hours(None) == "-"


def test_humanize_hours_zero():
    """Test humanizing zero hours."""
    assert humanize_hours(0) == "Immediate"


def test_humanize_hours_single_hour():
    """Test humanizing 1 hour."""
    assert humanize_hours(1) == "1 hour"


def test_humanize_hours_multiple_hours():
    """Test humanizing multiple hours under 24."""
    assert humanize_hours(2) == "2 hours"
    assert humanize_hours(12) == "12 hours"
    assert humanize_hours(23) == "23 hours"


def test_humanize_hours_one_day():
    """Test humanizing exactly 1 day (24 hours)."""
    assert humanize_hours(24) == "1 day"


def test_humanize_hours_multiple_days():
    """Test humanizing multiple days."""
    assert humanize_hours(48) == "2 days"
    assert humanize_hours(72) == "3 days"
    assert humanize_hours(120) == "5 days"


def test_humanize_hours_days_with_remainder():
    """Test humanizing days with remaining hours."""
    assert humanize_hours(25) == "1 day, 1 hour"
    assert humanize_hours(26) == "1 day, 2 hours"
    assert humanize_hours(50) == "2 days, 2 hours"


def test_humanize_hours_one_week():
    """Test humanizing exactly 1 week (168 hours)."""
    assert humanize_hours(168) == "1 week"


def test_humanize_hours_multiple_weeks():
    """Test humanizing multiple weeks."""
    assert humanize_hours(336) == "2 weeks"
    assert humanize_hours(504) == "3 weeks"


def test_humanize_hours_weeks_with_remainder():
    """Test humanizing weeks with remaining days."""
    assert humanize_hours(192) == "1 week, 1 day"  # 168 + 24
    assert humanize_hours(216) == "1 week, 2 days"  # 168 + 48
    assert humanize_hours(360) == "2 weeks, 1 day"  # 336 + 24


def test_humanize_hours_one_month():
    """Test humanizing 1 month (~30 days = 730 hours)."""
    assert humanize_hours(730) == "1 month"


def test_humanize_hours_multiple_months():
    """Test humanizing multiple months."""
    assert humanize_hours(1460) == "2 months"
    assert humanize_hours(2190) == "3 months"


def test_humanize_hours_months_with_remainder():
    """Test humanizing months with remaining weeks."""
    assert humanize_hours(898) == "1 month, 1 week"  # 730 + 168
    assert humanize_hours(1066) == "1 month, 2 weeks"  # 730 + 336


def test_humanize_hours_edge_cases():
    """Test edge cases."""
    # Just under a day
    assert humanize_hours(23) == "23 hours"
    # Just over a day
    assert humanize_hours(25) == "1 day, 1 hour"
    # Just under a week
    assert humanize_hours(167) == "6 days, 23 hours"
    # Just over a week - note: remainders less than 24 hours are not shown for weeks
    result = humanize_hours(169)
    # 169 hours = 1 week + 1 hour, but our function only shows day remainders for weeks
    assert result == "1 week"  # This is the actual behavior


def test_humanize_hours_real_world_examples():
    """Test real-world RTO examples."""
    # 4 hours - email outage
    assert humanize_hours(4) == "4 hours"
    
    # 8 hours - one business day
    assert humanize_hours(8) == "8 hours"
    
    # 24 hours - one full day
    assert humanize_hours(24) == "1 day"
    
    # 48 hours - two days
    assert humanize_hours(48) == "2 days"
    
    # 1 week - major system recovery
    assert humanize_hours(168) == "1 week"
    
    # 2 weeks - complete infrastructure rebuild
    assert humanize_hours(336) == "2 weeks"
    
    # 30 days - disaster recovery scenario
    assert humanize_hours(720) == "4 weeks, 2 days"  # Close to a month
    assert humanize_hours(730) == "1 month"
