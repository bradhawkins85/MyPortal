from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import re
from typing import Any, Mapping

import nh3

from app.repositories import resolution_step_reviews as review_repo
from app.services import knowledge_base, modules
from app.services.ai_prompt_security import UntrustedRecord, build_prompt


class _ListItemParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.current: list[str] = []
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "li":
            self.depth += 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "li" and self.depth:
            self.depth -= 1
            if not self.depth:
                text = " ".join("".join(self.current).split())
                if text:
                    self.items.append(text)
                self.current = []


def extract_steps(value: str) -> list[str]:
    parser = _ListItemParser()
    parser.feed(value or "")
    if parser.items:
        return parser.items
    plain = nh3.clean(value or "", tags=frozenset())
    return [line.strip(" -\t") for line in plain.splitlines() if line.strip(" -\t")]


def _response_text(result: Mapping[str, Any]) -> str:
    payload = result.get("response")
    if isinstance(payload, Mapping):
        return str(payload.get("response") or payload.get("message") or payload.get("text") or "")
    return str(payload or result.get("message") or "")


def _extract_title(text: str) -> str:
    """Return the first meaningful line of a model title response, max 20 words."""
    cleaned = re.sub(r"^```\w*\s*|\s*```$", "", text.strip())
    for line in cleaned.splitlines():
        candidate = re.sub(r"^(?:title\s*:\s*|#+\s*)", "", line.strip(), flags=re.IGNORECASE)
        candidate = candidate.strip().strip('"\'*`').strip()
        if candidate:
            return " ".join(candidate.split()[:20])
    return ""


async def _generate_title(records: list[UntrustedRecord], *, fallback: str) -> str:
    prompt = build_prompt(
        "Create a concise knowledge-base article title of no more than 20 words "
        "that names the problem solved. Return the title only, without quotation marks.",
        records,
        task="Return only the title.",
    )
    # Reasoning-capable models spend completion tokens before the final answer;
    # a 60 token cap can end before any title is emitted.
    result = await modules.trigger_module(
        "ollama", {"prompt": prompt, "temperature": 0.2, "max_tokens": 512}, background=False
    )
    if not modules.module_result_succeeded(result):
        raise ValueError("The configured LLM could not generate an article title")
    return _extract_title(_response_text(result)) or fallback or "Resolution guide"


async def generate_article(ticket_id: int, *, author_id: int) -> dict[str, Any]:
    entry = await review_repo.get_entry(ticket_id)
    if not entry:
        raise ValueError("Resolution Step entry not found")
    if entry.get("article_id"):
        raise ValueError("A knowledge-base article has already been generated for this entry")
    steps = extract_steps(str(entry.get("resolution_steps") or ""))
    if not steps:
        raise ValueError("No usable resolution steps were found")
    subject = str(entry.get("subject") or "Resolved support issue").strip()
    summary = " ".join(steps)[:1000]
    title = await _generate_title(
        [UntrustedRecord(
            f"ticket:{ticket_id}", "resolved helpdesk ticket",
            {"subject": subject, "resolution_summary": summary},
            "Use only to describe the problem in the title",
        )],
        fallback=subject,
    )
    sections = [
        {"heading": f"Step {index}", "content": f"<p>{escape(step)}</p>"}
        for index, step in enumerate(steps, start=1)
    ]
    article = await knowledge_base.create_article(
        {
            "slug": f"ticket-{ticket_id}-resolution",
            "title": title,
            "summary": summary,
            "sections": sections,
            "permission_scope": "super_admin",
            "is_published": False,
        },
        author_id=author_id,
    )
    await review_repo.link_article(ticket_id, int(article["id"]), author_id)
    return article


RECURRING_ISSUE_MIN_TICKETS = 3
# Bounds the title prompt; every ticket's steps still go into the article body.
_RECURRING_PROMPT_TICKET_LIMIT = 10


def _connected_groups(pairs: list[tuple[int, int]]) -> list[set[int]]:
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in pairs:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)
    groups: dict[int, set[int]] = {}
    for node in list(parent):
        groups.setdefault(find(node), set()).add(node)
    return list(groups.values())


async def _recurring_groups() -> list[dict[str, Any]]:
    """Group resolved tickets linked as DUPLICATE or KNOWN_ISSUE.

    Groups are keyed by their lowest ticket id. New tickets join with higher
    ids, so a group keeps its review state as it grows.
    """
    pairs = await review_repo.list_recurring_issue_links()
    if not pairs:
        return []
    entries = {entry["ticket_id"]: entry for entry in await review_repo.list_entries(include_ignored=True)}
    reviews = await review_repo.list_recurring_reviews()
    groups = []
    for members in _connected_groups(pairs):
        tickets = [entries[ticket_id] for ticket_id in sorted(members) if ticket_id in entries]
        if len(tickets) < RECURRING_ISSUE_MIN_TICKETS:
            continue
        anchor = tickets[0]["ticket_id"]
        review = reviews.get(anchor, {})
        tags: list[str] = []
        for ticket in tickets:
            tags.extend(tag for tag in ticket.get("ai_tags") or [] if tag not in tags)
        groups.append({
            "entry_type": "recurring_issue",
            "group_id": anchor,
            "subject": str(tickets[0].get("subject") or "Recurring issue"),
            "ticket_count": len(tickets),
            "ticket_ids": [ticket["ticket_id"] for ticket in tickets],
            "companies": sorted({str(ticket["company"]) for ticket in tickets if ticket.get("company")}),
            "ai_tags": tags,
            "ignored": bool(review.get("ignored")),
            "article_id": review.get("article_id"),
            "tickets": tickets,
        })
    groups.sort(key=lambda group: (-group["ticket_count"], group["group_id"]))
    return groups


async def list_recurring_issues(*, include_ignored: bool) -> list[dict[str, Any]]:
    groups = await _recurring_groups()
    return [
        {key: value for key, value in group.items() if key != "tickets"}
        for group in groups
        if include_ignored or not group["ignored"]
    ]


async def get_recurring_issue(group_id: int) -> dict[str, Any] | None:
    for group in await _recurring_groups():
        if group["group_id"] == group_id:
            return group
    return None


def _merge_steps(tickets: list[Mapping[str, Any]]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for ticket in tickets:
        for step in extract_steps(str(ticket.get("resolution_steps") or "")):
            key = " ".join(step.casefold().rstrip(".").split())
            if key and key not in seen:
                seen.add(key)
                merged.append(step)
    return merged


async def generate_recurring_article(group_id: int, *, author_id: int) -> dict[str, Any]:
    group = await get_recurring_issue(group_id)
    if not group:
        raise ValueError("Recurring issue entry not found")
    if group.get("article_id"):
        raise ValueError("A knowledge-base article has already been generated for this recurring issue")
    tickets = group["tickets"]
    steps = _merge_steps(tickets)
    if not steps:
        raise ValueError("No usable resolution steps were found")
    summary = (
        f"Recurring issue seen in {len(tickets)} tickets. " + " ".join(steps)
    )[:1000]
    title = await _generate_title(
        [
            UntrustedRecord(
                f"ticket:{ticket['ticket_id']}", "resolved helpdesk ticket for a recurring issue",
                {
                    "subject": str(ticket.get("subject") or ""),
                    "resolution_summary": " ".join(
                        extract_steps(str(ticket.get("resolution_steps") or ""))
                    )[:500],
                },
                "Use only to describe the shared problem in the title",
            )
            for ticket in tickets[:_RECURRING_PROMPT_TICKET_LIMIT]
        ],
        fallback=group["subject"],
    )
    sections = [
        {"heading": f"Step {index}", "content": f"<p>{escape(step)}</p>"}
        for index, step in enumerate(steps, start=1)
    ]
    # Ticket numbers only: subjects can carry customer details into a published article.
    source_items = "".join(f"<li>Ticket #{ticket['ticket_id']}</li>" for ticket in tickets)
    sections.append({"heading": "Source tickets", "content": f"<ul>{source_items}</ul>"})
    article = await knowledge_base.create_article(
        {
            "slug": f"recurring-issue-{group_id}-resolution",
            "title": title,
            "summary": summary,
            "sections": sections,
            "permission_scope": "super_admin",
            "is_published": False,
        },
        author_id=author_id,
    )
    await review_repo.link_recurring_article(group_id, int(article["id"]), group["ticket_ids"], author_id)
    return article
