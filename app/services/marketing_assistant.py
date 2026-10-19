"""Marketing Assistant: writes marketing page sections from listed websites.

An admin lists the websites (sources) a marketing page should be built from
and runs the assistant from the page editor. A run:

1. Reads every source with the outbound URL guard (no private, loopback or
   metadata addresses, checked on each redirect hop), keeping its text and the
   images it shows.
2. Asks the language model (``MARKETING_ASSISTANT_LLM_*``, else the
   troubleshooter's LLM) to draft the article as short statements, each citing
   the sources it came from. A *refresh* also shows the model the sections the
   assistant wrote before so it can keep their structure.
3. Cross-checks every statement: the cited sources must exist, every number
   in it must appear in a confirming source, and a second, independent model
   pass must find it stated in at least ``MARKETING_ASSISTANT_MIN_SOURCES_PER_CLAIM``
   sources without any source contradicting it. Statements that fail are left
   out and listed in the run report; nothing is written from the model's own
   knowledge.
4. Copies the images it placed (only images shown on the sources; JPEG, PNG,
   GIF or WebP, checked with Pillow) into the database, so the public page
   serves them itself, and credits the site each came from.
5. Replaces the sections the assistant wrote before (hand-written sections are
   kept) after saving a snapshot of the page, so the run can be undone.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info, log_warning
from app.repositories import marketing as marketing_repo
from app.repositories import marketing_assistant as repo
from app.services import ai_prompt_security
from app.services import llm_usage
from app.services import tray as tray_service
from app.services.monitored_http import monitored_client
from app.services.outbound_url_guard import (
    UnsafeOutboundURLError,
    redirect_guard_hooks,
    validate_outbound_url_async,
)
from app.services.troubleshoot_web_search import html_to_text

LLM_FEATURE = "marketing_assistant"
USER_AGENT = "MyPortal-MarketingAssistant/1.0 (+https://github.com/bradhawkins85/MyPortal)"

MODES = ("generate", "refresh")

_PAGE_TIMEOUT_SECONDS = 20
_IMAGE_TIMEOUT_SECONDS = 20
_LLM_TIMEOUT_SECONDS = 300
_LLM_RESPONSE_LIMIT_CHARS = 80_000
_MAX_REDIRECTS = 3
_MAX_PAGE_BYTES = 2 * 1024 * 1024
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MIN_IMAGE_BYTES = 2 * 1024
_MIN_IMAGE_WIDTH = 240
_MIN_IMAGE_HEIGHT = 140
_MAX_IMAGE_PIXELS = 40_000_000
_TOTAL_SOURCE_CHARS = 60_000
_MAX_SOURCE_CHARS = 15_000
_MAX_IMAGE_CANDIDATES_PER_SOURCE = 8
_MAX_IMAGE_CANDIDATES = 30
_MAX_SECTIONS = 8
_MAX_PARAGRAPHS = 4
_MAX_STATEMENTS_PER_PARAGRAPH = 5
_MAX_INTRO_STATEMENTS = 4
_MAX_STATEMENT_CHARS = 600
_VERIFY_BATCH_SIZE = 40
_STALE_RUN_MINUTES = 60
_URL_MAX_CHARS = 2048

_IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}
_SKIP_IMAGE_HINTS = ("sprite", "pixel", "spacer", "tracking", "favicon", "blank.", "loader", "spinner")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WHITESPACE_RE = re.compile(r"\s+")

_background_tasks: set[asyncio.Task[Any]] = set()


class AssistantError(Exception):
    """A run could not finish; the message is shown to the admin."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def build_llm_config() -> dict[str, str]:
    """``MARKETING_ASSISTANT_LLM_*`` when any is set, otherwise the troubleshooter's LLM."""

    settings = get_settings()
    base_url = str(settings.marketing_assistant_llm_base_url or "").strip()
    model = str(settings.marketing_assistant_llm_model or "").strip()
    api_key = str(settings.marketing_assistant_llm_api_key or "").strip()
    if not (base_url or model or api_key):
        return tray_service.build_troubleshoot_llm_config()
    return {"base_url": (base_url or "http://127.0.0.1:11434").rstrip("/"), "model": model or "llama3",
            "api_key": api_key}


def max_sources() -> int:
    return max(1, min(25, int(get_settings().marketing_assistant_max_sources or 10)))


def max_images() -> int:
    return max(0, min(12, int(get_settings().marketing_assistant_max_images)))


def min_sources_per_claim() -> int:
    return max(1, min(5, int(get_settings().marketing_assistant_min_sources_per_claim or 1)))


async def normalise_source_url(raw: str) -> str:
    """Validate an admin-entered source URL; raises ``ValueError`` with a reason."""

    url = str(raw or "").strip()
    if not url:
        raise ValueError("Enter a website address.")
    if len(url) > _URL_MAX_CHARS:
        raise ValueError("Website addresses must be 2048 characters or fewer.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{url} is not an http:// or https:// address.")
    if parsed.username or parsed.password:
        raise ValueError(f"{url} must not contain a user name or password.")
    try:
        await validate_outbound_url_async(url, allow_private=False)
    except UnsafeOutboundURLError as exc:
        raise ValueError(f"{url} cannot be used: {exc}") from exc
    return url


# --------------------------------------------------------------------------- #
# Reading sources
# --------------------------------------------------------------------------- #


@dataclass
class ImageCandidate:
    image_id: str
    url: str
    alt: str
    source_id: str
    source_url: str


@dataclass
class Source:
    source_id: str
    db_id: int | None
    url: str
    title: str = ""
    text: str = ""
    error: str | None = None
    images: list[ImageCandidate] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.text.strip()) and not self.error


class _ImageExtractor(HTMLParser):
    """Collect ``og:image`` and ``<img>`` references with their alt text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[str, str]] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in {"nav", "footer", "header", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "meta":
            prop = (values.get("property") or values.get("name") or "").lower()
            if prop in {"og:image", "og:image:url", "og:image:secure_url", "twitter:image"} and values.get("content"):
                self.images.append((values["content"], ""))
            return
        if tag != "img" or self._skip_depth:
            return
        src = values.get("src") or values.get("data-src") or values.get("data-lazy-src") or ""
        if (not src or src.startswith("data:")) and values.get("srcset"):
            src = values["srcset"].split(",")[0].strip().split(" ")[0]
        if not src or src.startswith("data:"):
            return
        for dimension in ("width", "height"):
            raw = values.get(dimension, "").strip().rstrip("px")
            if raw.isdigit() and int(raw) < 100:
                return
        self.images.append((src, values.get("alt", "")))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"nav", "footer", "header", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)


def extract_images(markup: str, base_url: str) -> list[tuple[str, str]]:
    """Absolute ``(url, alt)`` pairs for the usable images a page shows."""

    parser = _ImageExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup keeps what was parsed
        pass
    seen: set[str] = set()
    images: list[tuple[str, str]] = []
    for src, alt in parser.images:
        url = urljoin(base_url, src.strip())
        parsed = urlparse(url)
        lowered = parsed.path.lower()
        if parsed.scheme not in {"http", "https"} or url in seen or len(url) > _URL_MAX_CHARS:
            continue
        if lowered.endswith((".svg", ".ico")) or any(hint in lowered for hint in _SKIP_IMAGE_HINTS):
            continue
        seen.add(url)
        images.append((url, _clip(alt, 200)))
    return images


def _clip(value: Any, limit: int) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()[:limit]


async def _fetch(url: str, *, accept: str, max_bytes: int, timeout: float) -> tuple[bytes, str, str]:
    """GET a public URL; returns ``(body, content_type, encoding)``. Raises on failure."""

    await validate_outbound_url_async(url, allow_private=False)
    async with monitored_client(
        httpx.AsyncClient,
        timeout=timeout,
        follow_redirects=True,
        max_redirects=_MAX_REDIRECTS,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
        event_hooks=redirect_guard_hooks(allow_private=False),
    ) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    break
            return b"".join(chunks), content_type, response.encoding or "utf-8"


async def read_source(source_id: str, url: str, *, db_id: int | None = None, text_limit: int) -> Source:
    """Fetch one source page; failures are recorded on the returned source."""

    source = Source(source_id=source_id, db_id=db_id, url=url)
    try:
        body, content_type, encoding = await _fetch(
            url, accept="text/html,text/plain;q=0.9", max_bytes=_MAX_PAGE_BYTES, timeout=_PAGE_TIMEOUT_SECONDS
        )
    except UnsafeOutboundURLError as exc:
        source.error = f"Blocked address: {exc}"
        return source
    except httpx.HTTPStatusError as exc:
        source.error = f"The website returned HTTP {exc.response.status_code}"
        return source
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the run
        source.error = f"Could not read the page ({type(exc).__name__})"
        return source
    if "html" not in content_type and "text/plain" not in content_type:
        source.error = "The address is not a web page"
        return source
    markup = body[:_MAX_PAGE_BYTES].decode(encoding, errors="replace")
    if "html" in content_type:
        source.title, source.text = html_to_text(markup)
        source.images = [
            ImageCandidate(image_id="", url=image_url, alt=alt, source_id=source_id, source_url=url)
            for image_url, alt in extract_images(markup, url)[:_MAX_IMAGE_CANDIDATES_PER_SOURCE]
        ]
    else:
        source.text = markup
    source.text = source.text[:text_limit]
    if not source.text.strip():
        source.error = "No readable text was found on the page"
    return source


async def read_sources(entries: Sequence[Mapping[str, Any]]) -> list[Source]:
    """Read every source concurrently and number the usable images ``I1..In``."""

    limited = list(entries)[: max_sources()]
    text_limit = max(2000, min(_MAX_SOURCE_CHARS, _TOTAL_SOURCE_CHARS // max(1, len(limited))))
    sources = await asyncio.gather(*(
        read_source(f"S{index}", str(entry["url"]), db_id=entry.get("id"), text_limit=text_limit)
        for index, entry in enumerate(limited, start=1)
    ))
    counter = 0
    seen: set[str] = set()
    # Interleave the sources so every site offers images before any offers many.
    for rank in range(_MAX_IMAGE_CANDIDATES_PER_SOURCE):
        for source in sources:
            if not source.usable or rank >= len(source.images):
                continue
            candidate = source.images[rank]
            if candidate.url in seen or counter >= _MAX_IMAGE_CANDIDATES:
                candidate.image_id = ""
                continue
            seen.add(candidate.url)
            counter += 1
            candidate.image_id = f"I{counter}"
    for source in sources:
        source.images = [image for image in source.images if image.image_id]
    return list(sources)


# --------------------------------------------------------------------------- #
# Language model
# --------------------------------------------------------------------------- #


def _response_text(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, list):
                return "".join(str(part.get("text") or "") if isinstance(part, Mapping) else str(part)
                               for part in content).strip()
            return str(content or "").strip()
        return str(choices[0].get("text") or "").strip()
    message = payload.get("message")
    if isinstance(message, Mapping):
        return str(message.get("content") or "").strip()
    return ""


async def _chat(llm: Mapping[str, str], messages: list[dict[str, str]]) -> str:
    """One request to the assistant model's OpenAI-compatible chat endpoint."""

    base_url = str(llm.get("base_url") or "").rstrip("/")
    model = str(llm.get("model") or "")
    body = {"model": model, "messages": messages, "stream": False, "temperature": 0.2,
            "response_format": {"type": "json_object"}}
    headers = {"Content-Type": "application/json"}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"
    prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
    started = time.monotonic()
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{base_url}/v1/chat/completions", json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
        text = _response_text(payload)[:_LLM_RESPONSE_LIMIT_CHARS]
    except Exception:
        await llm_usage.record_usage(
            feature=LLM_FEATURE, provider="openai-compatible", model=model, status="failed", usage=(0, 0),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    await llm_usage.record_usage(
        feature=LLM_FEATURE, provider="openai-compatible", model=model, status="succeeded",
        usage=llm_usage.extract_token_usage(payload), prompt=prompt_text, response_text=text,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if not text:
        raise ValueError("The AI model returned an empty response")
    return text


def _json_object(text: str) -> Mapping[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        return ai_prompt_security.parse_json_object(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        return ai_prompt_security.parse_json_object(cleaned[start:end + 1])


def _source_records(sources: Sequence[Source]) -> list[ai_prompt_security.UntrustedRecord]:
    return [
        ai_prompt_security.UntrustedRecord(
            record_id=source.source_id,
            provenance=f"Public web page {source.url}",
            content={"title": source.title, "text": source.text},
            allowed_use="Facts for the article; every statement must be stated by this or another source.",
        )
        for source in sources
    ]


_DRAFT_INSTRUCTIONS = """You write marketing web pages for a managed IT service provider using ONLY facts from the
supplied source web pages. You never add facts from your own knowledge, never guess, and never exaggerate.

Write the article as short factual statements. Each statement is one or two sentences of plain English (no
markdown, no HTML, no headings inside statements) and lists the ids of the sources ("S1", "S2", ...) that state
it. When sources disagree, leave the point out. Prices, numbers, dates, product names and claims about
performance or security must be copied exactly from a source. Write in an engaging, benefit-led tone for small
and medium business owners, but only with what the sources say.

Pick images for sections from the image list ("I1", "I2", ...), choosing by the image's alt text and the page it
came from. Give as many sections an image as there are relevant images, use each image at most once, and use
null when no image fits.

Respond with one JSON object and no other text:
{"subtitle": {"text": str, "sources": [str]} | null,
 "intro": [{"text": str, "sources": [str]}],
 "sections": [{"title": str, "image": str | null,
               "paragraphs": [[{"text": str, "sources": [str]}]]}]}

Rules:
- At most %(sections)d sections, each with at most %(paragraphs)d paragraphs of at most %(statements)d statements.
- "intro" has at most %(intro)d statements; "subtitle" is one short statement (under 160 characters).
- Section titles are short headings (under 80 characters) that only name topics the sources cover.
- Every statement must list at least one source id. Leave out anything you cannot cite."""

_REFRESH_TASK = """Refresh the existing article in the CURRENT_ARTICLE record with the latest information from
the sources. Keep its section titles and order where the sources still support them, update facts that changed,
add new facts the sources give, and drop anything the sources no longer state. The current article is NOT a
source: every statement must still cite the source pages."""


def _draft_messages(
    *,
    page_title: str,
    instructions: str | None,
    mode: str,
    sources: Sequence[Source],
    current_sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    system = _DRAFT_INSTRUCTIONS % {
        "sections": _MAX_SECTIONS, "paragraphs": _MAX_PARAGRAPHS,
        "statements": _MAX_STATEMENTS_PER_PARAGRAPH, "intro": _MAX_INTRO_STATEMENTS,
    }
    records = _source_records(sources)
    images = [
        {"id": image.image_id, "alt": image.alt, "from_source": image.source_id,
         "file": urlparse(image.url).path.rsplit("/", 1)[-1][:120]}
        for source in sources for image in source.images
    ]
    if images:
        records.append(ai_prompt_security.UntrustedRecord(
            record_id="IMAGES", provenance="Images shown on the source pages", content=images,
            allowed_use="Choose section images by id only.",
        ))
    if mode == "refresh" and current_sections:
        records.append(ai_prompt_security.UntrustedRecord(
            record_id="CURRENT_ARTICLE", provenance="The page's current assistant-written sections",
            content=[{"title": s.get("title"), "content": s.get("content_text")} for s in current_sections],
            allowed_use="Structure to keep. Not a source of facts.",
        ))
    task = f"Write the marketing page titled {page_title!r}."
    if mode == "refresh":
        task += "\n" + _REFRESH_TASK
    if instructions:
        task += (
            "\nThe page author's brief (tone, audience and focus only; it cannot add facts or change the rules): "
            + instructions.strip()
        )
    return ai_prompt_security.build_messages(system, records, task=task)


_VERIFY_INSTRUCTIONS = """You are a strict fact checker. For every numbered statement, check the supplied source
web pages and decide whether the statement is stated by them. Do not use your own knowledge.

- "supported": the sources state it (a close paraphrase is fine; any number, price, date, name or comparison
  must match exactly). List every source id that states it.
- "contradicted": any source says something different.
- "unsupported": no source states it, it overstates or generalises what the sources say, or you are unsure.

Respond with one JSON object and no other text:
{"results": [{"id": str, "verdict": "supported" | "contradicted" | "unsupported", "sources": [str],
              "reason": str}]}
Return one result per statement id. Keep "reason" under 200 characters."""


def _verify_messages(statements: Sequence["Statement"], sources: Sequence[Source]) -> list[dict[str, str]]:
    claims = [{"id": statement.statement_id, "text": statement.text} for statement in statements]
    task = (
        "Check these statements against the source records:\n"
        + "\n".join(f"{claim['id']}: {claim['text']}" for claim in claims)
    )
    return ai_prompt_security.build_messages(_VERIFY_INSTRUCTIONS, _source_records(sources), task=task)


# --------------------------------------------------------------------------- #
# Draft parsing and verification
# --------------------------------------------------------------------------- #


@dataclass
class Statement:
    statement_id: str
    text: str
    cited: list[str]
    location: str
    supported_by: list[str] = field(default_factory=list)
    rejected_reason: str | None = None

    @property
    def verified(self) -> bool:
        return self.rejected_reason is None and bool(self.supported_by)


@dataclass
class DraftSection:
    title: str
    image: str | None
    paragraphs: list[list[Statement]]


@dataclass
class Draft:
    subtitle: Statement | None
    intro: list[Statement]
    sections: list[DraftSection]

    def statements(self) -> list[Statement]:
        items = ([self.subtitle] if self.subtitle else []) + list(self.intro)
        for section in self.sections:
            for paragraph in section.paragraphs:
                items.extend(paragraph)
        return items


def _source_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        raw = str(item).strip().upper()
        if raw.isdigit():
            raw = f"S{raw}"
        if re.fullmatch(r"S\d{1,3}", raw) and raw not in ids:
            ids.append(raw)
    return ids


def parse_draft(text: str) -> Draft:
    """Turn the model's draft into statements; malformed items are skipped."""

    data = _json_object(text)
    counter = 0

    def statement(value: Any, location: str, limit: int = _MAX_STATEMENT_CHARS) -> Statement | None:
        nonlocal counter
        if not isinstance(value, Mapping):
            return None
        body = _clip(value.get("text"), limit)
        if not body:
            return None
        counter += 1
        return Statement(statement_id=f"C{counter}", text=body, cited=_source_ids(value.get("sources")),
                         location=location)

    subtitle = statement(data.get("subtitle"), "Subtitle", 255)
    intro = [s for s in (statement(item, "Introduction") for item in
                         (data.get("intro") if isinstance(data.get("intro"), list) else [])[:_MAX_INTRO_STATEMENTS])
             if s]
    sections: list[DraftSection] = []
    raw_sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    for raw in raw_sections[:_MAX_SECTIONS]:
        if not isinstance(raw, Mapping):
            continue
        title = _clip(raw.get("title"), 160)
        if not title:
            continue
        paragraphs: list[list[Statement]] = []
        raw_paragraphs = raw.get("paragraphs") if isinstance(raw.get("paragraphs"), list) else []
        for raw_paragraph in raw_paragraphs[:_MAX_PARAGRAPHS]:
            items = raw_paragraph if isinstance(raw_paragraph, list) else [raw_paragraph]
            paragraph = [s for s in (statement(item, title) for item in items[:_MAX_STATEMENTS_PER_PARAGRAPH]) if s]
            if paragraph:
                paragraphs.append(paragraph)
        image = str(raw.get("image") or "").strip().upper() or None
        sections.append(DraftSection(title=title, image=image, paragraphs=paragraphs))
    return Draft(subtitle=subtitle, intro=intro, sections=sections)


def _normalise_number(value: str) -> str:
    cleaned = value.replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned


def _numbers(text: str) -> set[str]:
    return {_normalise_number(match) for match in _NUMBER_RE.findall(text or "")}


def numbers_confirmed(text: str, source_texts: Sequence[str]) -> bool:
    """True when every number in ``text`` appears in at least one of ``source_texts``."""

    wanted = _numbers(text)
    if not wanted:
        return True
    available: set[str] = set()
    for source_text in source_texts:
        available |= _numbers(source_text)
    return wanted <= available


def required_confirmations(usable_sources: int) -> int:
    return max(1, min(min_sources_per_claim(), usable_sources))


async def verify_statements(
    llm: Mapping[str, str], statements: Sequence[Statement], sources: Sequence[Source]
) -> None:
    """Mark each statement verified (``supported_by``) or rejected (``rejected_reason``)."""

    by_id = {source.source_id: source for source in sources if source.usable}
    needed = required_confirmations(len(by_id))
    pending: list[Statement] = []
    for statement in statements:
        cited = [source_id for source_id in statement.cited if source_id in by_id]
        if not cited:
            statement.rejected_reason = "No source was cited for it."
        else:
            statement.cited = cited
            pending.append(statement)

    verdicts: dict[str, Mapping[str, Any]] = {}
    for start in range(0, len(pending), _VERIFY_BATCH_SIZE):
        batch = pending[start:start + _VERIFY_BATCH_SIZE]
        data = _json_object(await _chat(llm, _verify_messages(batch, list(by_id.values()))))
        results = data.get("results")
        if not isinstance(results, list):
            raise ValueError("The fact check returned no results list")
        for result in results:
            if isinstance(result, Mapping) and result.get("id"):
                verdicts[str(result["id"]).strip().upper()] = result

    for statement in pending:
        result = verdicts.get(statement.statement_id)
        if result is None:
            statement.rejected_reason = "The fact check did not return a result for it."
            continue
        verdict = str(result.get("verdict") or "").strip().lower()
        reason = _clip(result.get("reason"), 200)
        if verdict == "contradicted":
            statement.rejected_reason = "The sources disagree" + (f": {reason}" if reason else ".")
            continue
        if verdict != "supported":
            statement.rejected_reason = "Not stated by the sources" + (f": {reason}" if reason else ".")
            continue
        supported = [source_id for source_id in _source_ids(result.get("sources")) if source_id in by_id]
        if len(supported) < needed:
            statement.rejected_reason = (
                f"Confirmed by {len(supported)} source(s); {needed} are required."
            )
            continue
        if not numbers_confirmed(statement.text, [by_id[source_id].text for source_id in supported]):
            statement.rejected_reason = "A number in it does not appear in the confirming sources."
            continue
        statement.supported_by = supported


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


def inspect_image(data: bytes) -> str | None:
    """The content type for a usable photo-sized JPEG/PNG/GIF/WebP, else ``None``."""

    if len(data) < _MIN_IMAGE_BYTES or len(data) > _MAX_IMAGE_BYTES:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            if width * height > _MAX_IMAGE_PIXELS:
                return None
            image.verify()
    except Exception:  # noqa: BLE001 - anything Pillow cannot read is skipped
        return None
    if width < _MIN_IMAGE_WIDTH or height < _MIN_IMAGE_HEIGHT:
        return None
    return _IMAGE_FORMATS.get(image_format)


async def download_image(candidate: ImageCandidate) -> tuple[bytes, str] | None:
    try:
        body, _content_type, _ = await _fetch(
            candidate.url, accept="image/avif,image/webp,image/png,image/jpeg,image/gif;q=0.9",
            max_bytes=_MAX_IMAGE_BYTES, timeout=_IMAGE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a missing image is skipped
        log_warning("Marketing assistant could not download image", url=candidate.url, error=str(exc))
        return None
    content_type = await asyncio.to_thread(inspect_image, body)
    if not content_type:
        return None
    return body, content_type


def _credit(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return f"Image: {host}" if host else "Image from a source website"


# --------------------------------------------------------------------------- #
# Building and applying the article
# --------------------------------------------------------------------------- #


@dataclass
class BuiltSection:
    title: str
    content_text: str
    source_urls: list[str]
    image: ImageCandidate | None = None


def build_sections(draft: Draft, sources: Sequence[Source]) -> list[BuiltSection]:
    """Sections made only of verified statements; empty sections are dropped."""

    urls = {source.source_id: source.url for source in sources}
    built: list[BuiltSection] = []
    for section in draft.sections:
        paragraphs = [
            " ".join(statement.text for statement in paragraph if statement.verified)
            for paragraph in section.paragraphs
        ]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        if not paragraphs:
            continue
        cited: list[str] = []
        for paragraph in section.paragraphs:
            for statement in paragraph:
                if statement.verified:
                    cited.extend(urls[source_id] for source_id in statement.supported_by if source_id in urls)
        built.append(BuiltSection(
            title=section.title,
            content_text="\n\n".join(paragraphs),
            source_urls=list(dict.fromkeys(cited)),
        ))
    return built


def _title_supported(title: str, sources: Sequence[Source]) -> bool:
    return numbers_confirmed(title, [source.text for source in sources if source.usable])


async def place_images(
    draft: Draft, built: list[BuiltSection], sources: Sequence[Source], limit: int
) -> list[tuple[BuiltSection, ImageCandidate, bytes, str]]:
    """Download the chosen images, falling back to other images from each
    section's confirming sources. Returns ``(section, candidate, data, type)``."""

    if limit <= 0:
        return []
    candidates = {image.image_id: image for source in sources for image in source.images}
    by_source_url: dict[str, list[ImageCandidate]] = {}
    for source in sources:
        by_source_url[source.url] = list(source.images)
    chosen_by_title = {section.title: section.image for section in draft.sections}
    used: set[str] = set()
    failed: set[str] = set()
    placed: list[tuple[BuiltSection, ImageCandidate, bytes, str]] = []
    for section in built:
        if len(placed) >= limit:
            break
        options: list[ImageCandidate] = []
        chosen = chosen_by_title.get(section.title)
        if chosen and chosen in candidates:
            options.append(candidates[chosen])
        for url in section.source_urls:
            options.extend(by_source_url.get(url, []))
        for candidate in options[:4]:
            if candidate.image_id in used or candidate.image_id in failed:
                continue
            downloaded = await download_image(candidate)
            if not downloaded:
                failed.add(candidate.image_id)
                continue
            used.add(candidate.image_id)
            section.image = candidate
            placed.append((section, candidate, downloaded[0], downloaded[1]))
            break
    return placed


def _snapshot(page: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "subtitle": page.get("subtitle"),
        "intro_text": page.get("intro_text"),
        "sections": [
            {key: section.get(key) for key in (
                "title", "anchor_slug", "content_text", "sort_order", "image_id", "image_alt", "image_caption",
                "source_urls", "assistant_generated",
            )}
            for section in page.get("sections") or []
        ],
    }


def _unique_anchor(title: str, taken: set[str], index: int) -> str:
    base = (marketing_repo.slugify(title) or f"section-{index}")[:110]
    anchor = base
    suffix = 2
    while anchor in taken:
        anchor = f"{base}-{suffix}"
        suffix += 1
    taken.add(anchor)
    return anchor


async def _apply(
    page: Mapping[str, Any],
    *,
    draft: Draft,
    built: list[BuiltSection],
    image_ids: Mapping[int, int],
    update_intro: bool,
) -> None:
    page_id = int(page["id"])
    manual = [section for section in page.get("sections") or [] if not section.get("assistant_generated")]
    taken = {str(section.get("anchor_slug")) for section in manual}
    start = max([int(section.get("sort_order") or 0) for section in manual] + [0]) + 10
    await marketing_repo.delete_assistant_sections(page_id)
    for index, section in enumerate(built):
        image = section.image
        await marketing_repo.create_section(
            page_id=page_id,
            title=section.title,
            anchor_slug=_unique_anchor(section.title, taken, index + 1),
            content_text=section.content_text,
            sort_order=start + index * 10,
            image_id=image_ids.get(id(section)),
            image_alt=(image.alt or section.title)[:255] if image else None,
            image_caption=_credit(image.source_url) if image else None,
            source_urls=section.source_urls,
            assistant_generated=True,
        )
    if update_intro:
        subtitle = draft.subtitle.text if draft.subtitle and draft.subtitle.verified else None
        intro = " ".join(statement.text for statement in draft.intro if statement.verified) or None
        if subtitle or intro:
            await marketing_repo.update_page_text(
                page_id,
                subtitle=subtitle or page.get("subtitle"),
                intro_text=intro or page.get("intro_text"),
            )


async def run_assistant(
    run_id: int, page_id: int, *, mode: str, instructions: str | None, update_intro: bool
) -> None:
    """Execute one run end to end and record its outcome. Never raises."""

    llm = build_llm_config()
    report: dict[str, Any] = {"sources": [], "rejected": [], "warnings": []}
    try:
        page = await marketing_repo.get_page_by_id(page_id)
        if not page:
            raise AssistantError("The marketing page no longer exists.")
        entries = await repo.list_sources(page_id)
        if not entries:
            raise AssistantError("Add at least one source website before running the assistant.")
        if len(entries) > max_sources():
            report["warnings"].append(
                f"Only the first {max_sources()} of {len(entries)} websites were read (MARKETING_ASSISTANT_MAX_SOURCES)."
            )
        sources = await read_sources(entries)
        for source in sources:
            report["sources"].append({
                "id": source.source_id, "url": source.url, "title": source.title,
                "status": "read" if source.usable else "failed", "error": source.error,
                "images_found": len(source.images),
            })
            if source.db_id:
                await repo.record_source_fetch(
                    source.db_id, status="read" if source.usable else "failed", error=source.error
                )
        usable = [source for source in sources if source.usable]
        if not usable:
            raise AssistantError("None of the source websites could be read.")

        current = [section for section in page.get("sections") or [] if section.get("assistant_generated")]
        draft = parse_draft(await _chat(llm, _draft_messages(
            page_title=page["title"], instructions=instructions, mode=mode, sources=usable,
            current_sections=current,
        )))
        statements = draft.statements()
        if not statements:
            raise AssistantError("The AI model did not find anything in the sources to write about.")
        await verify_statements(llm, statements, usable)
        for statement in statements:
            if not statement.verified:
                report["rejected"].append({
                    "location": statement.location, "text": statement.text,
                    "reason": statement.rejected_reason or "Not verified.",
                })

        for section in draft.sections:
            if not _title_supported(section.title, usable):
                report["rejected"].append({
                    "location": "Section title", "text": section.title,
                    "reason": "A number in it does not appear in the sources.",
                })
                section.paragraphs = []
        built = build_sections(draft, usable)
        if not built:
            raise AssistantError(
                "None of the drafted statements could be verified against the sources, so the page was not changed."
            )

        placed = await place_images(draft, built, usable, max_images())
        image_ids: dict[int, int] = {}
        for section, candidate, data, content_type in placed:
            image_ids[id(section)] = await repo.store_image(
                page_id=page_id, sha256=hashlib.sha256(data).hexdigest(), source_page_url=candidate.source_url,
                image_url=candidate.url, content_type=content_type, data=data,
            )
        if max_images() and not placed and any(source.images for source in usable):
            report["warnings"].append("None of the images on the source websites could be used.")

        snapshot = _snapshot(page)
        await _apply(page, draft=draft, built=built, image_ids=image_ids, update_intro=update_intro)
        verified = sum(1 for statement in statements if statement.verified)
        await repo.finish_run(
            run_id, status="succeeded", model=llm.get("model"), error=None, sources_read=len(usable),
            sections_written=len(built), images_used=len(placed), claims_verified=verified,
            claims_rejected=len(report["rejected"]), report=report, snapshot=snapshot,
        )
        log_info("Marketing assistant run finished", run_id=run_id, page_id=page_id, sections=len(built))
    except AssistantError as exc:
        await repo.finish_run(run_id, status="failed", model=llm.get("model"), error=str(exc), report=report,
                              sources_read=sum(1 for s in report["sources"] if s["status"] == "read"))
    except Exception as exc:  # noqa: BLE001 - recorded on the run for the admin
        log_error("Marketing assistant run failed", run_id=run_id, page_id=page_id, error=str(exc))
        await repo.finish_run(
            run_id, status="failed", model=llm.get("model"),
            error=f"The run failed: {type(exc).__name__}: {exc}"[:512], report=report,
            sources_read=sum(1 for s in report["sources"] if s["status"] == "read"),
        )


async def start_run(
    page_id: int,
    *,
    mode: str,
    instructions: str | None,
    update_intro: bool,
    requested_by_user_id: int | None,
) -> int:
    """Record a run and execute it in the background. Raises ``ValueError``
    when the page cannot be run now."""

    if mode not in MODES:
        raise ValueError("Choose whether to write or refresh the page.")
    await repo.fail_stale_runs(
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=_STALE_RUN_MINUTES)
    )
    if await repo.has_running_run(page_id):
        raise ValueError("The assistant is already working on this page.")
    if not await repo.list_sources(page_id):
        raise ValueError("Add at least one source website before running the assistant.")
    run_id = await repo.create_run(
        page_id=page_id, mode=mode, instructions=instructions, requested_by_user_id=requested_by_user_id
    )
    task = asyncio.create_task(
        run_assistant(run_id, page_id, mode=mode, instructions=instructions, update_intro=update_intro)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return run_id


async def restore_run(run_id: int) -> int:
    """Put a page back the way it was before a run; returns the page id."""

    run = await repo.get_run(run_id)
    if not run or run["status"] != "succeeded":
        raise ValueError("Only a finished run can be undone.")
    snapshot = await repo.get_run_snapshot(run_id)
    if not snapshot:
        raise ValueError("This run has no saved copy of the page to restore.")
    page_id = int(run["page_id"])
    page = await marketing_repo.get_page_by_id(page_id)
    if not page:
        raise ValueError("The marketing page no longer exists.")
    # The run only replaced assistant-written sections, so only those are put
    # back; hand-written sections (including any added since) are kept.
    taken = {
        str(section.get("anchor_slug"))
        for section in page.get("sections") or []
        if not section.get("assistant_generated")
    }
    await marketing_repo.delete_assistant_sections(page_id)
    restored = [section for section in snapshot.get("sections") or [] if section.get("assistant_generated")]
    for index, section in enumerate(restored, start=1):
        title = str(section.get("title") or "")[:160] or "Section"
        await marketing_repo.create_section(
            page_id=page_id,
            title=title,
            anchor_slug=_unique_anchor(str(section.get("anchor_slug") or title), taken, index),
            content_text=str(section.get("content_text") or ""),
            sort_order=int(section.get("sort_order") or 0),
            image_id=section.get("image_id"),
            image_alt=section.get("image_alt"),
            image_caption=section.get("image_caption"),
            source_urls=list(section.get("source_urls") or []),
            assistant_generated=True,
        )
    await marketing_repo.update_page_text(
        page_id, subtitle=snapshot.get("subtitle"), intro_text=snapshot.get("intro_text")
    )
    await repo.mark_restored(run_id)
    return page_id


__all__ = [
    "AssistantError",
    "MODES",
    "build_llm_config",
    "normalise_source_url",
    "restore_run",
    "run_assistant",
    "start_run",
]
