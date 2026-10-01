"""Validation and croniter conversion for MyPortal cron expressions."""

from __future__ import annotations

import re

from croniter import croniter

MIN_YEAR = 1970
MAX_YEAR = 2099

_YEAR_VALUE = re.compile(r"^\d{4}$")


def _validate_year_field(field: str) -> None:
    if field == "*":
        return
    if "/" in field:
        raise ValueError("year step expressions are not supported")
    if not field or field.startswith(",") or field.endswith(",") or ",," in field:
        raise ValueError("year must be '*' or a comma-separated list of years and ranges")

    for item in field.split(","):
        bounds = item.split("-")
        if len(bounds) > 2 or any(not _YEAR_VALUE.fullmatch(value) for value in bounds):
            raise ValueError("years must be four digits and may use inclusive ranges")
        start = int(bounds[0])
        end = int(bounds[-1])
        if not MIN_YEAR <= start <= MAX_YEAR or not MIN_YEAR <= end <= MAX_YEAR:
            raise ValueError(f"years must be between {MIN_YEAR} and {MAX_YEAR}")
        if start > end:
            raise ValueError("year ranges must be in ascending order")


def parse(expression: str) -> tuple[str, str, str, str, str, str]:
    """Validate and return minute through year, defaulting an omitted year to ``*``."""

    fields = expression.strip().split()
    if len(fields) not in {5, 6}:
        raise ValueError(
            f"cron expression must contain 5 or 6 fields; received {len(fields)}"
        )
    if len(fields) == 5:
        fields.append("*")
    _validate_year_field(fields[5])
    if not croniter.is_valid(" ".join(fields[:5])):
        raise ValueError("invalid minute, hour, day-of-month, month, or day-of-week field")
    return tuple(fields)  # type: ignore[return-value]


def validate(expression: str) -> str:
    """Return a trimmed valid expression or raise a descriptive ``ValueError``."""

    value = expression.strip()
    parse(value)
    return value


def for_croniter(expression: str) -> str:
    """Convert MyPortal's optional trailing year into croniter's seven-field form."""

    minute, hour, day, month, weekday, year = parse(expression)
    return f"{minute} {hour} {day} {month} {weekday} 0 {year}"
