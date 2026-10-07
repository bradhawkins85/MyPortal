"""Utilities for normalising and validating company email domains."""

from __future__ import annotations

import re
from typing import Iterable, List

from app.core.config import get_settings
from app.core.logging import log_info

EMAIL_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,255}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class EmailDomainError(ValueError):
    """Raised when an email domain fails validation."""


def get_blocked_email_domains() -> tuple[str, ...]:
    """Return the normalised domains configured in ``BLOCKED_EMAIL_DOMAINS``."""

    raw = get_settings().blocked_email_domains or ""
    blocked: list[str] = []
    for chunk in re.split(r"[\s,;]+", raw):
        candidate = chunk.strip().strip(".").lower()
        if candidate.startswith("*."):
            candidate = candidate[2:]
        if candidate and candidate not in blocked:
            blocked.append(candidate)
    return tuple(blocked)


def is_blocked_email_domain(
    domain: str, blocked: Iterable[str] | None = None
) -> bool:
    """Return ``True`` when *domain* is, or is a subdomain of, a blocked domain."""

    candidate = str(domain or "").strip().strip(".").lower()
    if not candidate:
        return False
    entries = get_blocked_email_domains() if blocked is None else blocked
    return any(
        candidate == entry or candidate.endswith(f".{entry}") for entry in entries
    )


def normalise_email_domains(domains: Iterable[str]) -> list[str]:
    """Validate and normalise a collection of email domains.

    Domains are stripped of surrounding whitespace, converted to lowercase and
    deduplicated while preserving the original order. Invalid entries raise an
    :class:`EmailDomainError`. Domains matching ``BLOCKED_EMAIL_DOMAINS`` are
    dropped without raising so bulk imports still save the remaining domains.
    """

    normalised: list[str] = []
    seen: set[str] = set()
    blocked = get_blocked_email_domains()
    skipped: list[str] = []

    for raw in domains:
        candidate = str(raw or "").strip().lower()
        if not candidate:
            continue
        if len(candidate) > 255:
            raise EmailDomainError("Email domain must be 255 characters or fewer")
        if not EMAIL_DOMAIN_PATTERN.fullmatch(candidate):
            raise EmailDomainError(f"Invalid email domain: {candidate}")
        if candidate in seen:
            continue
        seen.add(candidate)
        if is_blocked_email_domain(candidate, blocked):
            skipped.append(candidate)
            continue
        normalised.append(candidate)
    if skipped:
        log_info("Skipped blocked company email domains", domains=skipped)
    return normalised


def parse_email_domain_text(value: str | None) -> list[str]:
    """Parse comma and newline separated text into validated domains."""

    if value is None:
        return []

    parts: List[str] = []
    for chunk in re.split(r"[\n,]", value):
        parts.append(chunk)
    return normalise_email_domains(parts)

