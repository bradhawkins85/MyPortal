"""Utilities for normalising and validating company email domains."""

from __future__ import annotations

import re
from typing import Iterable, List

EMAIL_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,255}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class EmailDomainError(ValueError):
    """Raised when an email domain fails validation."""


def normalise_email_domains(domains: Iterable[str]) -> list[str]:
    """Validate and normalise a collection of email domains.

    Domains are stripped of surrounding whitespace, converted to lowercase and
    deduplicated while preserving the original order. Invalid entries raise an
    :class:`EmailDomainError`.
    """

    normalised: list[str] = []
    seen: set[str] = set()

    for raw in domains:
        candidate = str(raw or "").strip().lower()
        if not candidate:
            continue
        if len(candidate) > 255:
            raise EmailDomainError("Email domain must be 255 characters or fewer")
        if not EMAIL_DOMAIN_PATTERN.fullmatch(candidate):
            raise EmailDomainError(f"Invalid email domain: {candidate}")
        if candidate not in seen:
            seen.add(candidate)
            normalised.append(candidate)
    return normalised


def parse_email_domain_text(value: str | None) -> list[str]:
    """Parse comma and newline separated text into validated domains."""

    if value is None:
        return []

    parts: List[str] = []
    for chunk in re.split(r"[\n,]", value):
        parts.append(chunk)
    return normalise_email_domains(parts)

