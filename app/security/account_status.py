"""Shared account status checks."""


def is_user_active(user: dict) -> bool:
    """Return False only when the account has been explicitly deactivated."""
    value = user.get("is_active", 1)
    if value is None:
        return True
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)
