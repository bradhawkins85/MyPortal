from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import re
from typing import Any, Mapping

import nh3

from app.repositories import resolution_step_reviews as review_repo
from app.services import knowledge_base, modules


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
    prompt = (
        "Create a concise knowledge-base article title of no more than 20 words. "
        "Return the title only, without quotation marks.\n"
        f"Ticket subject: {subject}\nResolution summary: {summary}"
    )
    result = await modules.trigger_module(
        "ollama", {"prompt": prompt, "temperature": 0.2, "max_tokens": 60}, background=False
    )
    if not modules.module_result_succeeded(result):
        raise ValueError("The configured LLM could not generate an article title")
    title = " ".join(_response_text(result).strip().strip('"\'').split())
    title = re.sub(r"^(title\s*:\s*)", "", title, flags=re.IGNORECASE)
    title = " ".join(title.split()[:20]).strip() or "Resolution guide"
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
