"""Form parsing helpers for ticket feature routes."""

from __future__ import annotations

from typing import Any


def get_last_form_value(form: Any, field_name: str, default: Any = None) -> Any:
    """Return the last submitted value for a repeated form field.

    Starlette's ``FormData.get()`` returns the first value for duplicate keys.
    Split-button reply forms can submit both the primary button's default
    status and the technician-selected menu status in some browser paths, so
    the final submitted value should win.
    """

    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        values = [value for value in getlist(field_name) if value not in (None, "")]
        if values:
            return values[-1]
    return form.get(field_name, default)
