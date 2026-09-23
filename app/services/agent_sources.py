"""Canonical source catalogue for Agent filtering, routing, and retrieval."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSource:
    label: str
    cap: int
    weight: float
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


SOURCE_REGISTRY: dict[str, AgentSource] = {
    "knowledge_base": AgentSource(
        "Knowledge Base", 4, 0.90, ("kb",), ("guide", "article", "knowledge")
    ),
    "tickets": AgentSource(
        "Tickets", 4, 1.00, ("ticket",), ("ticket", "case", "support")
    ),
    "ticket_comments": AgentSource(
        "Ticket comments",
        6,
        0.95,
        ("ticket_reply", "ticket_replies"),
        ("reply", "comment", "note"),
    ),
    "products": AgentSource(
        "Products", 4, 0.60, ("product",), ("product", "sku", "price", "buy")
    ),
    "packages": AgentSource("Packages", 3, 0.60, ("package",), ("package", "bundle")),
    "chats": AgentSource(
        "Chats", 4, 0.95, ("chat",), ("chat", "conversation", "message")
    ),
    "orders": AgentSource(
        "Orders", 3, 0.85, ("order",), ("order", "purchase", "shipping", "consignment")
    ),
    "assets": AgentSource(
        "Assets", 3, 0.80, ("asset",), ("asset", "device", "computer", "serial")
    ),
    "companies": AgentSource(
        "Companies", 3, 0.75, ("company",), ("company", "customer", "client")
    ),
    "staff": AgentSource(
        "Staff", 3, 0.75, ("employee", "employees"), ("staff", "employee", "contact")
    ),
    "issues": AgentSource(
        "Issues", 3, 0.75, ("issue",), ("issue", "problem", "incident")
    ),
    "service_status": AgentSource(
        "Service status",
        3,
        0.80,
        ("status",),
        ("service", "outage", "status", "availability"),
    ),
    "backup_jobs": AgentSource(
        "Backup jobs", 3, 0.80, ("backups", "backup"), ("backup", "restore", "job")
    ),
    "reports": AgentSource(
        "Reports", 3, 0.70, ("report",), ("report", "summary", "metrics")
    ),
    "mailboxes": AgentSource(
        "Microsoft 365 mailboxes",
        3,
        0.75,
        ("mailbox",),
        ("mailbox", "email", "exchange", "m365"),
    ),
    "best_practices": AgentSource(
        "Best practices",
        2,
        0.50,
        ("best_practice",),
        ("best practice", "recommendation", "compliance"),
    ),
    "feature_packs": AgentSource(
        "Feature packs", 3, 0.65, ("features",), ("feature pack", "extension")
    ),
}

SOURCE_ALIASES = {
    alias: source_type
    for source_type, definition in SOURCE_REGISTRY.items()
    for alias in definition.aliases
}
SOURCE_TYPE_CAPS = {key: value.cap for key, value in SOURCE_REGISTRY.items()}
SOURCE_WEIGHTS = {key: value.weight for key, value in SOURCE_REGISTRY.items()}


def canonical_source_type(value: str) -> str:
    normalised = str(value or "").strip().casefold()
    return SOURCE_ALIASES.get(normalised, normalised)


def validate_source_filters(values: list[str]) -> list[str]:
    canonical = [canonical_source_type(value) for value in values]
    unknown = sorted({value for value in canonical if value not in SOURCE_REGISTRY})
    if unknown:
        raise ValueError("Unknown Agent source filter(s): " + ", ".join(unknown))
    return list(dict.fromkeys(canonical))
