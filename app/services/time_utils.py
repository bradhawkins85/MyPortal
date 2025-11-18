"""Utility functions for working with time and duration."""


def humanize_hours(hours: int | None) -> str:
    """
    Convert hours to human-readable format.
    
    Args:
        hours: Number of hours (can be None)
        
    Returns:
        Human-readable string (e.g., "2 hours", "3 days", "2 weeks")
        
    Examples:
        >>> humanize_hours(None)
        '-'
        >>> humanize_hours(0)
        'Immediate'
        >>> humanize_hours(1)
        '1 hour'
        >>> humanize_hours(2)
        '2 hours'
        >>> humanize_hours(24)
        '1 day'
        >>> humanize_hours(48)
        '2 days'
        >>> humanize_hours(168)
        '1 week'
        >>> humanize_hours(336)
        '2 weeks'
        >>> humanize_hours(730)
        '1 month'
        >>> humanize_hours(1460)
        '2 months'
    """
    if hours is None:
        return "-"
    
    if hours == 0:
        return "Immediate"
    
    # Convert to different units based on size
    if hours < 24:
        # Less than a day: show in hours
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    
    if hours < 168:  # Less than a week (7 days)
        days = hours // 24
        remainder_hours = hours % 24
        if remainder_hours == 0:
            return f"{days} day" if days == 1 else f"{days} days"
        else:
            day_str = f"{days} day" if days == 1 else f"{days} days"
            hour_str = f"{remainder_hours} hour" if remainder_hours == 1 else f"{remainder_hours} hours"
            return f"{day_str}, {hour_str}"
    
    if hours < 730:  # Less than ~30 days (1 month)
        weeks = hours // 168
        remainder_days = (hours % 168) // 24
        if remainder_days == 0:
            return f"{weeks} week" if weeks == 1 else f"{weeks} weeks"
        else:
            week_str = f"{weeks} week" if weeks == 1 else f"{weeks} weeks"
            day_str = f"{remainder_days} day" if remainder_days == 1 else f"{remainder_days} days"
            return f"{week_str}, {day_str}"
    
    # 730 hours or more: show in months
    months = hours // 730
    remainder_weeks = (hours % 730) // 168
    if remainder_weeks == 0:
        return f"{months} month" if months == 1 else f"{months} months"
    else:
        month_str = f"{months} month" if months == 1 else f"{months} months"
        week_str = f"{remainder_weeks} week" if remainder_weeks == 1 else f"{remainder_weeks} weeks"
        return f"{month_str}, {week_str}"
