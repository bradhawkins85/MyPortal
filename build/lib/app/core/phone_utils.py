"""Utility functions for phone number handling and E.164 formatting."""
from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException


def normalize_to_e164(phone_number: str, default_region: str = "AU") -> str | None:
    """
    Normalize a phone number to E.164 format.
    
    E.164 format is the international standard for phone numbers: +[country code][number]
    Example: +61412345678 for Australian mobile, +14155551234 for US number
    
    Args:
        phone_number: The phone number to normalize (can be in various formats)
        default_region: ISO 3166-1 alpha-2 country code to use when parsing 
                       numbers without country code (default: "AU" for Australia)
    
    Returns:
        The phone number in E.164 format (with + prefix), or None if invalid
    
    Examples:
        >>> normalize_to_e164("0412345678")  # Australian mobile
        '+61412345678'
        >>> normalize_to_e164("+61 412 345 678")
        '+61412345678'
        >>> normalize_to_e164("(415) 555-1234", default_region="US")
        '+14155551234'
    """
    if not phone_number:
        return None
    
    try:
        # Parse the phone number
        parsed = phonenumbers.parse(phone_number, default_region)
        
        # Validate that it's a possible number
        if not phonenumbers.is_possible_number(parsed):
            return None
        
        # Format to E.164
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    
    except NumberParseException:
        # If parsing fails, return None
        return None
