"""Multi-stage AI troubleshooter for ticket assets.

A technician starts the troubleshooter from an asset on a ticket. The server
then works through the problem in stages, posting each result to the ticket as
an internal (staff-only) note:

1. **Research** - ask the LLM which troubleshooting articles apply to the
   ticket and pull matching articles from the internal knowledge base.
2. **Plan** - process those articles into recommended troubleshooting steps,
   and decide which endpoint logs (if any) the steps call for and why.
3. **Collect** - when logs are needed, send a ``collect_logs`` troubleshoot
   command to the asset's tray device naming each allowlisted log source and
   the reason for collecting it. The device uploads the redacted logs to
   ``/api/tickets/{id}/troubleshoot-complete``.
4. **Analyse** - send the logs to the LLM with the reason each was collected
   and the recommended steps, and post its analysis of likely causes and
   solutions.

The LLM never chooses what runs on the endpoint: log sources it suggests are
validated against a fixed allowlist here and again on the device.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
import zlib
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.logging import log_error, log_info, log_warning
from app.services import ai_consent
from app.services import ai_prompt_security
from app.services import knowledge_base as knowledge_base_service
from app.services import llm_usage
from app.services import tray as tray_service
from app.services import troubleshoot_web_search
from app.services.monitored_http import monitored_client

MODE_COLLECT_LOGS = "collect_logs"
LLM_FEATURE = "ai_troubleshooter"

# Windows Event Log channels and macOS unified-log sources the tray agent may
# collect. Keep in sync with tray/internal/agent/logsources.go.
WINDOWS_LOG_SOURCES: dict[str, str] = {
    "System": "Core OS events: services, drivers, disks, unexpected shutdowns",
    "Application": "Application crashes, hangs and errors",
    "Security": "Logons, lockouts, privilege use and audit events",
    "Setup": "Windows feature and update installation",
    "Microsoft-Windows-WLAN-AutoConfig/Operational": "Wi-Fi connects, disconnects and authentication",
    "Microsoft-Windows-NetworkProfile/Operational": "Network connectivity and profile changes",
    "Microsoft-Windows-DNS-Client/Operational": "DNS resolution (often disabled by default)",
    "Microsoft-Windows-SMBClient/Connectivity": "File share / mapped drive connectivity",
    "Microsoft-Windows-PrintService/Admin": "Printer and spooler errors",
    "Microsoft-Windows-PrintService/Operational": "Print job activity (often disabled by default)",
    "Microsoft-Windows-WindowsUpdateClient/Operational": "Windows Update scans, downloads and failures",
    "Microsoft-Windows-Windows Defender/Operational": "Defender detections, scans and protection changes",
    "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational": "RDP / local session logon, logoff and reconnect",
    "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational": "Inbound RDP connection attempts",
    "Microsoft-Windows-GroupPolicy/Operational": "Group Policy processing",
    "Microsoft-Windows-Bits-Client/Operational": "Background transfers (updates, Intune, OneDrive)",
    "Microsoft-Windows-Diagnostics-Performance/Operational": "Slow boot, shutdown and standby",
    "Microsoft-Windows-AAD/Operational": "Entra ID (Azure AD) sign-in and token errors",
    "Microsoft-Windows-User Device Registration/Admin": "Entra ID device join / registration",
    "Microsoft-Windows-Kernel-PnP/Configuration": "Device and driver installation",
    "Microsoft-Windows-Time-Service/Operational": "Time synchronisation",
}
MACOS_LOG_SOURCES: dict[str, str] = {
    "macos:all": "The whole unified log (large; prefer a narrower source)",
    "macos:wifi": "Wi-Fi subsystem",
    "macos:network": "Networking subsystem",
    "macos:bluetooth": "Bluetooth subsystem",
    "macos:printing": "CUPS printing",
    "macos:power": "Power management, sleep and wake",
    "macos:kernel": "Kernel messages (panics, drivers)",
    "macos:install": "Software installs and updates",
    "macos:loginwindow": "Login window and authorisation",
}

DEFAULT_LOG_HOURS = 24
MAX_LOG_HOURS = 72
MAX_LOG_REQUESTS = 5
MAX_ARTICLES = 5
MAX_KB_ARTICLES = 3
MAX_STEPS = 12

# Prompt budgets.
_KB_CONTENT_CHARS = 3000
_LOG_PROMPT_CHARS = 48 * 1024
# Decompressed log bundle cap; bounds memory against a hostile bundle.
_MAX_DECOMPRESSED_LOG_BYTES = 2 * 1024 * 1024
_LLM_TIMEOUT_SECONDS = 300
_LLM_RESPONSE_LIMIT_CHARS = 64 * 1024

_TAG_RE = re.compile(r"<[^<>]*>")
_SPACE_RE = re.compile(r"\s+")
_IMPORTANT_LOG_LINE_RE = re.compile(
    r"error|warn|fail|critical|fatal|denied|timeout|timed out|unable|crash|panic",
    re.IGNORECASE,
)
_LOG_SECTION_RE = re.compile(r"^### (.+?) Log\s*$", re.MULTILINE)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

NOTE_HEADER = "<p><strong>AI Troubleshooting Agent</strong> - "


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plain_text(value: Any, limit: int | None = None) -> str:
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", str(value or "")))).strip()
    if limit is not None and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _clean(value: Any, limit: int) -> str:
    return _plain_text(value, limit)


def endpoint_platform(device: Mapping[str, Any] | None, asset: Mapping[str, Any] | None) -> str:
    """Return ``windows``, ``macos`` or ``unknown`` for the endpoint."""

    hints = " ".join(
        str(value or "")
        for value in (
            (device or {}).get("os"),
            (device or {}).get("os_version"),
            (asset or {}).get("os_name"),
        )
    ).lower()
    # "darwin" contains "win", so check macOS first.
    if any(token in hints for token in ("mac", "darwin", "os x")):
        return "macos"
    if "win" in hints:
        return "windows"
    return "unknown"


def allowed_log_sources(platform: str) -> dict[str, str]:
    if platform == "windows":
        return dict(WINDOWS_LOG_SOURCES)
    if platform == "macos":
        return dict(MACOS_LOG_SOURCES)
    return {**WINDOWS_LOG_SOURCES, **MACOS_LOG_SOURCES}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse the first JSON object in ``text`` (tolerates code fences)."""

    candidate = str(text or "").strip()
    if not candidate:
        return None
    try:
        return dict(ai_prompt_security.parse_json_object(candidate))
    except ValueError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return dict(ai_prompt_security.parse_json_object(candidate[start : end + 1]))
    except ValueError:
        return None


def _string_list(value: Any, *, limit: int, item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = _clean(entry, item_chars)
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _safe_url(value: Any) -> str:
    """Return ``value`` only when it is a plain http(s) URL."""

    url = str(value or "").strip()
    if not url or len(url) > 500 or any(ch.isspace() for ch in url):
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def _html_list(items: Sequence[str], *, ordered: bool = False) -> str:
    if not items:
        return ""
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{tag}>"


def _schedule(coro: Awaitable[Any], *, description: str, ticket_id: int) -> asyncio.Task[Any]:
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)

    def _done(completed: asyncio.Task[Any]) -> None:
        _BACKGROUND_TASKS.discard(completed)
        if completed.cancelled():
            return
        exc = completed.exception()
        if exc is not None:
            log_error(
                f"AI troubleshooter {description} failed",
                ticket_id=ticket_id,
                error=str(exc),
            )

    task.add_done_callback(_done)
    return task


async def _note(ticket_id: int, body_html: str) -> None:
    try:
        await tray_service.add_troubleshoot_note(ticket_id, body_html)
    except Exception as exc:  # noqa: BLE001 - notes are best-effort
        log_error("Failed to add AI troubleshooter note", ticket_id=ticket_id, error=str(exc))


async def _consent_withdrawn(ticket_id: int) -> bool:
    if await ai_consent.is_ai_allowed_for_ticket_id(ticket_id):
        return False
    await _note(
        ticket_id,
        NOTE_HEADER
        + "stopped because the ticket's requester has opted out of AI processing.</p>",
    )
    return True


# ---------------------------------------------------------------------------
# LLM access
# ---------------------------------------------------------------------------


def _response_text(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        message = first.get("message")
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, list):
                return "".join(
                    str(part.get("text") or "") if isinstance(part, Mapping) else str(part)
                    for part in content
                ).strip()
            return str(content or "").strip()
        return str(first.get("text") or "").strip()
    # Ollama native /api/chat shape, in case a proxy returns it.
    message = payload.get("message")
    if isinstance(message, Mapping):
        return str(message.get("content") or "").strip()
    return ""


async def _chat(
    llm: Mapping[str, str],
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
) -> str:
    """Send one chat request to the troubleshooter's OpenAI-compatible LLM."""

    base_url = str(llm.get("base_url") or "").rstrip("/")
    model = str(llm.get("model") or "")
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    api_key = str(llm.get("api_key") or "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    prompt_text = "\n".join(str(m.get("content") or "") for m in messages)
    started = time.monotonic()
    try:
        async with monitored_client(httpx.AsyncClient, timeout=_LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions", json=body, headers=headers
            )
        response.raise_for_status()
        payload = response.json()
        text = _response_text(payload)[:_LLM_RESPONSE_LIMIT_CHARS]
    except Exception:
        await llm_usage.record_usage(
            feature=LLM_FEATURE,
            provider="openai-compatible",
            model=model,
            status="failed",
            usage=(0, 0),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    await llm_usage.record_usage(
        feature=LLM_FEATURE,
        provider="openai-compatible",
        model=model,
        status="succeeded",
        usage=llm_usage.extract_token_usage(payload),
        prompt=prompt_text,
        response_text=text,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if not text:
        raise ValueError("The AI model returned an empty response")
    return text


def _problem_record(problem: str) -> ai_prompt_security.UntrustedRecord:
    return ai_prompt_security.UntrustedRecord(
        record_id="ticket",
        provenance="Support ticket and the asset under investigation",
        content=problem,
        allowed_use="Understand the reported problem",
    )


# ---------------------------------------------------------------------------
# Stage 1 - research troubleshooting articles
# ---------------------------------------------------------------------------

_RESEARCH_INSTRUCTIONS = """You are a senior IT support engineer at a managed-service provider.
Identify the published troubleshooting articles that best match the reported problem
(for example Microsoft Learn / Microsoft Support, Apple Support, or the hardware or
software vendor's own knowledge base).

Respond with a single JSON object, no other text:
{"articles": [{"title": str, "publisher": str, "url": str, "summary": str,
  "steps": [str, ...]}], "search_terms": [str, ...]}

Rules:
- At most 5 articles, most relevant first.
- Only cite articles you are confident exist. Leave "url" empty when you are not sure
  of the exact address; never invent one.
- "steps" are the troubleshooting steps that article recommends, in order.
- "search_terms" are 1-4 short, generic phrases for searching a knowledge base or the
  public web (for example "Windows 11 Wi-Fi disconnects intermittently"). Never include
  names, email addresses, company names, hostnames, IP addresses or other identifying
  details from the ticket."""


def _parse_articles(data: Mapping[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    if not data:
        return [], []
    articles: list[dict[str, Any]] = []
    raw = data.get("articles")
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        title = _clean(entry.get("title"), 200)
        if not title:
            continue
        articles.append(
            {
                "title": title,
                "publisher": _clean(entry.get("publisher"), 100),
                "url": _safe_url(entry.get("url")),
                "summary": _clean(entry.get("summary"), 600),
                "steps": _string_list(entry.get("steps"), limit=10, item_chars=300),
            }
        )
        if len(articles) >= MAX_ARTICLES:
            break
    terms = _string_list(data.get("search_terms"), limit=4, item_chars=80)
    return articles, terms


def _kb_access_context(ticket: Mapping[str, Any]) -> knowledge_base_service.ArticleAccessContext:
    """KB visibility for the ticket: public articles plus the ticket's company.

    Notes are staff-only, but the troubleshooter still never pulls another
    company's restricted articles into a ticket.
    """

    memberships: dict[int, Mapping[str, Any]] = {}
    try:
        company_id = int(ticket.get("company_id") or 0)
    except (TypeError, ValueError):
        company_id = 0
    if company_id > 0:
        memberships[company_id] = {"company_id": company_id, "is_admin": False}
    return knowledge_base_service.ArticleAccessContext(
        user=None, user_id=None, is_super_admin=False, memberships=memberships
    )


async def _search_kb(ticket: Mapping[str, Any], terms: Sequence[str]) -> list[dict[str, Any]]:
    context = _kb_access_context(ticket)
    queries = [str(ticket.get("subject") or "").strip(), *terms]
    found: dict[int, dict[str, Any]] = {}
    for query in [q for q in queries if q][:5]:
        try:
            result = await knowledge_base_service.search_articles(
                query, context, limit=MAX_KB_ARTICLES, use_ollama=False
            )
        except Exception as exc:  # noqa: BLE001 - KB search is best-effort
            log_warning("AI troubleshooter KB search failed", error=str(exc))
            continue
        for article in result.get("results") or []:
            article_id = int(article.get("id") or 0)
            if article_id and article_id not in found:
                found[article_id] = {
                    "id": article_id,
                    "slug": str(article.get("slug") or ""),
                    "title": _clean(article.get("title"), 200),
                    "summary": _clean(article.get("summary"), 600),
                    "content": _plain_text(article.get("content"), _KB_CONTENT_CHARS),
                }
        if len(found) >= MAX_KB_ARTICLES:
            break
    return list(found.values())[:MAX_KB_ARTICLES]


def _link(url: str, title: str) -> str:
    return (
        f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" '
        f'target="_blank">{html.escape(title)}</a>'
    )


def _research_note(
    kb_articles: Sequence[Mapping[str, Any]],
    articles: Sequence[Mapping[str, Any]],
    web_pages: Sequence[Mapping[str, Any]] = (),
    web_error: str | None = None,
) -> str:
    parts = [NOTE_HEADER + "Stage 1 of 4: troubleshooting articles found.</p>"]
    if kb_articles:
        items = [
            f'<a href="/knowledge-base/articles/{html.escape(str(a["slug"]))}">'
            f'{html.escape(str(a["title"]))}</a>'
            for a in kb_articles
            if a.get("slug")
        ]
        parts.append("<p><strong>Internal knowledge base</strong></p>" + _html_list(items))
    if articles:
        items = []
        for article in articles:
            title = html.escape(str(article["title"]))
            if article.get("url"):
                title = _link(str(article["url"]), str(article["title"]))
            publisher = (
                f" ({html.escape(str(article['publisher']))})" if article.get("publisher") else ""
            )
            summary = (
                f" - {html.escape(str(article['summary']))}" if article.get("summary") else ""
            )
            items.append(title + publisher + summary)
        parts.append(
            "<p><strong>Published articles suggested by the AI model</strong> "
            "<em>(verify the source before relying on it)</em></p>" + _html_list(items)
        )
    if web_pages:
        items = [
            _link(str(page["url"]), str(page["title"]))
            + (f" - {html.escape(str(page['snippet']))}" if page.get("snippet") else "")
            for page in web_pages
        ]
        parts.append("<p><strong>Public web pages read</strong></p>" + _html_list(items))
    if web_error:
        parts.append(
            "<p><em>Public web search failed: " + html.escape(web_error[:300]) + "</em></p>"
        )
    if not kb_articles and not articles and not web_pages:
        parts.append(
            "<p>No matching articles were found. The recommended steps below are "
            "based on the ticket details alone.</p>"
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Stage 2 - recommended steps and log requests
# ---------------------------------------------------------------------------

_PLAN_INSTRUCTIONS = """You are a senior IT support engineer at a managed-service provider.
Using the reported problem and the troubleshooting articles provided, write the
recommended troubleshooting steps for the technician, and decide whether any logs
should be collected from the endpoint to narrow down the cause.

Respond with a single JSON object, no other text:
{"summary": str,
 "steps": [{"action": str, "detail": str, "article": str}],
 "log_requests": [{"source": str, "reason": str, "hours": int}]}

Rules:
- "summary": one or two sentences on the most likely cause(s).
- "steps": ordered, concrete steps (at most 12). "article" is the title of the article
  the step comes from, or "" if it is your own recommendation.
- "log_requests": only when a step advises reviewing or collecting logs, or the logs
  would clearly confirm or rule out a likely cause. Otherwise return [].
  "source" MUST be one of the ALLOWED_LOG_SOURCES keys exactly. "reason" says what you
  expect the log to show and which step it supports. "hours" is how far back to read
  (1-72). At most 5 requests."""

_PLAN_WEB_INSTRUCTIONS = """
Some records are public web pages (record_id "web-N"). Also return
"web_sources": [{"id": "web-N", "steps": [str, ...]}] listing, for each page that
contains troubleshooting steps for this problem, those steps in order and in your own
words. Leave a page out if it has no relevant steps. These will be used to write
internal knowledge base articles."""


def _parse_plan(data: Mapping[str, Any] | None, raw_text: str, allowed: Mapping[str, str]) -> dict[str, Any]:
    if not data:
        # The model ignored the JSON format; keep its text as a single step so
        # the technician still gets the guidance.
        fallback = _clean(raw_text, 4000)
        return {
            "summary": "",
            "steps": [{"action": fallback, "detail": "", "article": ""}] if fallback else [],
            "log_requests": [],
        }
    steps: list[dict[str, str]] = []
    raw_steps = data.get("steps")
    for entry in raw_steps if isinstance(raw_steps, list) else []:
        if isinstance(entry, Mapping):
            action = _clean(entry.get("action"), 400)
            detail = _clean(entry.get("detail"), 800)
            article = _clean(entry.get("article"), 200)
        else:
            action, detail, article = _clean(entry, 400), "", ""
        if action:
            steps.append({"action": action, "detail": detail, "article": article})
        if len(steps) >= MAX_STEPS:
            break
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_requests = data.get("log_requests")
    for entry in raw_requests if isinstance(raw_requests, list) else []:
        if not isinstance(entry, Mapping):
            continue
        source = str(entry.get("source") or "").strip()
        if source not in allowed or source in seen:
            continue
        try:
            hours = int(entry.get("hours") or DEFAULT_LOG_HOURS)
        except (TypeError, ValueError):
            hours = DEFAULT_LOG_HOURS
        hours = max(1, min(MAX_LOG_HOURS, hours))
        reason = _clean(entry.get("reason"), 400) or "Requested by the troubleshooting plan."
        requests.append({"source": source, "reason": reason, "hours": hours})
        seen.add(source)
        if len(requests) >= MAX_LOG_REQUESTS:
            break
    return {"summary": _clean(data.get("summary"), 1000), "steps": steps, "log_requests": requests}


def _parse_web_sources(
    data: Mapping[str, Any] | None, web_pages: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Attach the steps the model extracted to each web page it read."""

    if not data or not web_pages:
        return []
    by_id = {f"web-{index}": page for index, page in enumerate(web_pages, start=1)}
    sources: list[dict[str, Any]] = []
    raw = data.get("web_sources")
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        page = by_id.pop(str(entry.get("id") or "").strip(), None)
        steps = _string_list(entry.get("steps"), limit=15, item_chars=400)
        if page is None or not steps:
            continue
        sources.append({"title": page["title"], "url": page["url"], "steps": steps})
    return sources


def _web_steps_note(sources: Sequence[Mapping[str, Any]]) -> str:
    """A note gathering each public page's steps with its URL, ready for a KB article."""

    parts = [
        NOTE_HEADER
        + "troubleshooting steps found on the public web. Useful as a starting "
        "point for an internal knowledge base article; check each source first.</p>"
    ]
    for source in sources:
        parts.append(
            "<p><strong>"
            + _link(str(source["url"]), str(source["title"]))
            + "</strong><br><code>"
            + html.escape(str(source["url"]))
            + "</code></p>"
            + _html_list([html.escape(step) for step in source["steps"]], ordered=True)
        )
    return "".join(parts)


def _plan_note(plan: Mapping[str, Any], *, can_collect: bool) -> str:
    parts = [NOTE_HEADER + "Stage 2 of 4: recommended troubleshooting steps.</p>"]
    if plan.get("summary"):
        parts.append(f"<p><strong>Likely cause:</strong> {html.escape(plan['summary'])}</p>")
    items = []
    for step in plan.get("steps") or []:
        item = f"<strong>{html.escape(step['action'])}</strong>"
        if step.get("detail"):
            item += f"<br>{html.escape(step['detail'])}"
        if step.get("article"):
            item += f"<br><em>Source: {html.escape(step['article'])}</em>"
        items.append(item)
    parts.append(_html_list(items, ordered=True) or "<p>No steps were produced.</p>")
    requests = plan.get("log_requests") or []
    if requests:
        log_items = [
            f"<strong>{html.escape(r['source'])}</strong> (last {int(r['hours'])} h): "
            f"{html.escape(r['reason'])}"
            for r in requests
        ]
        heading = (
            "Logs to collect from the endpoint"
            if can_collect
            else "Logs that would help (the endpoint has no active tray agent to collect them)"
        )
        parts.append(f"<p><strong>{heading}</strong></p>" + _html_list(log_items))
    else:
        parts.append("<p>The steps do not call for endpoint logs, so the troubleshooter is finished.</p>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Stage orchestration
# ---------------------------------------------------------------------------


async def start_asset_troubleshoot(
    *,
    ticket: Mapping[str, Any],
    asset: Mapping[str, Any],
    device: Mapping[str, Any],
    problem: str,
    llm: Mapping[str, str],
    requested_by: Mapping[str, Any] | None,
) -> None:
    """Record the request on the ticket and run the stages in the background."""

    ticket_id = int(ticket["id"])
    target = str(asset.get("name") or f"asset #{asset.get('asset_id')}")
    device_label = str(device.get("hostname") or device.get("device_uid") or "device")
    await _note(
        ticket_id,
        NOTE_HEADER
        + "troubleshooter requested by "
        + html.escape(tray_service.troubleshoot_requester_label(dict(requested_by or {})))
        + f" for {html.escape(target)} ({html.escape(device_label)}), model "
        + html.escape(str(llm.get("model") or "default"))
        + ".</p><p>It will research troubleshooting articles, post recommended steps, "
        "and collect and analyse endpoint logs if the steps call for them.</p>",
    )
    _schedule(
        run_research_and_plan(
            ticket=dict(ticket),
            asset=dict(asset),
            device=dict(device),
            problem=problem,
            llm=dict(llm),
            initiated_by_user_id=(requested_by or {}).get("id"),
        ),
        description="research",
        ticket_id=ticket_id,
    )


async def run_research_and_plan(
    *,
    ticket: dict[str, Any],
    asset: dict[str, Any],
    device: dict[str, Any],
    problem: str,
    llm: dict[str, str],
    initiated_by_user_id: Any = None,
) -> dict[str, Any] | None:
    """Stages 1-3. Returns the plan, or ``None`` when the run stopped early."""

    ticket_id = int(ticket["id"])
    problem_record = _problem_record(problem)

    # --- Stage 1: research --------------------------------------------------
    try:
        research_text = await _chat(
            llm,
            ai_prompt_security.build_messages(
                _RESEARCH_INSTRUCTIONS,
                [problem_record],
                task="Find troubleshooting articles for this problem.",
            ),
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - reported on the ticket
        await _note(
            ticket_id,
            NOTE_HEADER
            + "Stage 1 of 4 failed: the AI model could not be reached ("
            + html.escape(str(exc)[:300])
            + ").</p>",
        )
        return None
    articles, terms = _parse_articles(_extract_json_object(research_text))
    kb_articles = await _search_kb(ticket, terms)
    web_pages: list[dict[str, str]] = []
    web_error: str | None = None
    if troubleshoot_web_search.is_enabled() and terms:
        try:
            web_pages = await troubleshoot_web_search.research(terms)
        except Exception as exc:  # noqa: BLE001 - reported in the note
            web_error = str(exc) or exc.__class__.__name__
    await _note(ticket_id, _research_note(kb_articles, articles, web_pages, web_error))

    if await _consent_withdrawn(ticket_id):
        return None

    # --- Stage 2: plan ------------------------------------------------------
    platform = endpoint_platform(device, asset)
    allowed = allowed_log_sources(platform)
    article_records = [
        ai_prompt_security.UntrustedRecord(
            record_id=f"kb-{article['id']}",
            provenance="Internal knowledge base article",
            content={"title": article["title"], "summary": article["summary"], "content": article["content"]},
            allowed_use="Troubleshooting reference",
        )
        for article in kb_articles
    ] + [
        ai_prompt_security.UntrustedRecord(
            record_id=f"article-{index}",
            provenance="Published article recalled by the AI model (unverified)",
            content=article,
            allowed_use="Troubleshooting reference",
        )
        for index, article in enumerate(articles, start=1)
    ] + [
        ai_prompt_security.UntrustedRecord(
            record_id=f"web-{index}",
            provenance=f"Public web page: {page['url']}",
            content={"title": page["title"], "url": page["url"], "text": page["content"]},
            allowed_use="Troubleshooting reference",
        )
        for index, page in enumerate(web_pages, start=1)
    ]
    plan_instructions = (
        _PLAN_INSTRUCTIONS
        + (_PLAN_WEB_INSTRUCTIONS if web_pages else "")
        + f"\n\nENDPOINT_PLATFORM: {platform}\nALLOWED_LOG_SOURCES:\n"
        + "\n".join(f"- {key}: {description}" for key, description in allowed.items())
    )
    try:
        plan_text = await _chat(
            llm,
            ai_prompt_security.build_messages(
                plan_instructions,
                [problem_record, *article_records],
                task="Write the recommended troubleshooting steps and any log requests.",
            ),
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - reported on the ticket
        await _note(
            ticket_id,
            NOTE_HEADER
            + "Stage 2 of 4 failed: the AI model could not be reached ("
            + html.escape(str(exc)[:300])
            + ").</p>",
        )
        return None
    plan_data = _extract_json_object(plan_text)
    plan = _parse_plan(plan_data, plan_text, allowed)
    web_sources = _parse_web_sources(plan_data, web_pages)
    device_uid = str(device.get("device_uid") or "").strip()
    can_collect = bool(device_uid) and device.get("status") == "active"
    await _note(ticket_id, _plan_note(plan, can_collect=can_collect))
    if web_sources:
        await _note(ticket_id, _web_steps_note(web_sources))
    plan["web_sources"] = web_sources

    if not plan["log_requests"] or not can_collect:
        return plan
    if await _consent_withdrawn(ticket_id):
        return None

    # --- Stage 3: collect logs ---------------------------------------------
    payload = {
        "ticket_id": ticket_id,
        "mode": MODE_COLLECT_LOGS,
        "log_requests": plan["log_requests"],
        tray_service.SERVER_CONTEXT_KEY: {
            "problem": problem,
            "model": str(llm.get("model") or ""),
            "summary": plan["summary"],
            "steps": [step["action"] for step in plan["steps"]],
        },
    }
    try:
        user_id = int(initiated_by_user_id) if initiated_by_user_id is not None else None
    except (TypeError, ValueError):
        user_id = None
    command_id, delivered = await tray_service.dispatch_troubleshoot_command(
        device_id=int(device["id"]),
        device_uid=device_uid,
        payload=payload,
        initiated_by_user_id=user_id,
    )
    await _note(
        ticket_id,
        NOTE_HEADER
        + f"Stage 3 of 4: requested {len(plan['log_requests'])} log source(s) from "
        + html.escape(str(device.get("hostname") or device_uid))
        + f" (command #{int(command_id)}).</p>"
        + tray_service.troubleshoot_delivery_html(delivered=delivered, device=device),
    )
    log_info(
        "AI troubleshooter requested endpoint logs",
        ticket_id=ticket_id,
        command_id=command_id,
        sources=[r["source"] for r in plan["log_requests"]],
        delivered=delivered,
    )
    return plan


# ---------------------------------------------------------------------------
# Stage 4 - analyse the collected logs
# ---------------------------------------------------------------------------


def command_payload(command: Mapping[str, Any]) -> dict[str, Any]:
    raw = command.get("payload_json") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def is_collect_logs_command(command: Mapping[str, Any]) -> bool:
    return command_payload(command).get("mode") == MODE_COLLECT_LOGS


def decompress_log_bundle(data: bytes) -> str:
    """Inflate a gzip log bundle, bounded and tolerant of truncation.

    The device caps the bundle by cutting the compressed bytes, so the stream
    may end early; whatever inflated cleanly is kept.
    """

    if not data:
        return ""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(data, _MAX_DECOMPRESSED_LOG_BYTES)
    except zlib.error:
        return ""
    return out.decode("utf-8", errors="replace")


def split_log_sections(text: str) -> dict[str, str]:
    """Split a bundle into ``{source: text}`` using the agent's headers."""

    sections: dict[str, str] = {}
    matches = list(_LOG_SECTION_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return {"logs": stripped} if stripped else {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        sections[match.group(1).strip()] = body
    return sections


def _sample_log(text: str, budget: int) -> tuple[str, bool]:
    """Fit a log section into ``budget`` chars, keeping error/warning lines first."""

    if len(text) <= budget:
        return text, False
    lines = text.splitlines()
    chosen: set[int] = set()
    used = 0
    for important in (True, False):
        for index, line in enumerate(lines):
            if index in chosen:
                continue
            if bool(_IMPORTANT_LOG_LINE_RE.search(line)) != important:
                continue
            if used + len(line) + 1 > budget:
                continue
            chosen.add(index)
            used += len(line) + 1
    return "\n".join(lines[i] for i in sorted(chosen)), True


_ANALYSIS_INSTRUCTIONS = """You are a senior IT support engineer at a managed-service provider.
Logs were collected from the endpoint because the troubleshooting plan asked for them.
Each log record states the reason it was collected. Analyse the logs against those
reasons and the reported problem to help determine the possible solutions.

Respond with a single JSON object, no other text:
{"summary": str,
 "findings": [{"finding": str, "evidence": str, "source": str}],
 "likely_causes": [str],
 "solutions": [str],
 "next_steps": [str]}

Rules:
- Quote the exact log lines (with timestamps) that support each finding in "evidence".
- Never invent log entries. If the logs do not show anything relevant, say so in
  "summary" and suggest what else to check.
- Treat [REDACTED...] placeholders as absent.
- "solutions" are the fixes most likely to resolve the issue, most likely first."""


def _analysis_note(data: Mapping[str, Any] | None, raw_text: str, endpoint_label: str) -> str:
    parts = [
        NOTE_HEADER
        + "Stage 4 of 4: log analysis for "
        + html.escape(endpoint_label)
        + ".</p>"
    ]
    if not data:
        text = _clean(raw_text, 8000)
        parts.append(f"<p>{html.escape(text)}</p>" if text else "<p>No analysis was produced.</p>")
        return "".join(parts)
    summary = _clean(data.get("summary"), 1500)
    if summary:
        parts.append(f"<p>{html.escape(summary)}</p>")
    findings = []
    raw_findings = data.get("findings")
    for entry in (raw_findings if isinstance(raw_findings, list) else [])[:10]:
        if not isinstance(entry, Mapping):
            continue
        finding = _clean(entry.get("finding"), 500)
        if not finding:
            continue
        item = html.escape(finding)
        source = _clean(entry.get("source"), 120)
        if source:
            item += f" <em>({html.escape(source)})</em>"
        evidence = str(entry.get("evidence") or "").strip()[:1200]
        if evidence:
            item += f"<pre>{html.escape(evidence)}</pre>"
        findings.append(item)
    if findings:
        parts.append("<p><strong>Findings</strong></p>" + _html_list(findings))
    for key, heading, ordered in (
        ("likely_causes", "Likely causes", False),
        ("solutions", "Possible solutions", True),
        ("next_steps", "Next steps", True),
    ):
        items = [html.escape(item) for item in _string_list(data.get(key), limit=10, item_chars=600)]
        if items:
            parts.append(f"<p><strong>{heading}</strong></p>" + _html_list(items, ordered=ordered))
    return "".join(parts)


async def analyse_collected_logs(
    *,
    ticket_id: int,
    command: Mapping[str, Any],
    log_text: str,
    endpoint_label: str,
    llm: Mapping[str, str],
) -> None:
    """Stage 4: send the collected logs to the LLM with the reason for each."""

    if await _consent_withdrawn(ticket_id):
        return
    payload = command_payload(command)
    context = payload.get(tray_service.SERVER_CONTEXT_KEY)
    context = context if isinstance(context, Mapping) else {}
    requests = [r for r in payload.get("log_requests") or [] if isinstance(r, Mapping)]
    sections = split_log_sections(log_text)

    ordered: list[tuple[str, str, str]] = []
    for request in requests:
        source = str(request.get("source") or "")
        if source in sections:
            ordered.append((source, str(request.get("reason") or ""), sections.pop(source)))
    # Sources the device read without being asked (older agents read defaults).
    for source, body in sections.items():
        ordered.append((source, "Collected by the endpoint agent's default log set.", body))

    if not ordered:
        await _note(
            ticket_id,
            NOTE_HEADER + "Stage 4 of 4 skipped: the endpoint returned no log entries.</p>",
        )
        return

    budget = max(2048, _LOG_PROMPT_CHARS // len(ordered))
    log_records = []
    truncated_any = False
    for source, reason, body in ordered:
        sample, truncated = _sample_log(body, budget)
        truncated_any = truncated_any or truncated
        log_records.append(
            ai_prompt_security.UntrustedRecord(
                record_id=f"log-{source}",
                provenance=f"Redacted endpoint log: {source}",
                content={
                    "source": source,
                    "reason_collected": reason,
                    "truncated": truncated,
                    "entries": sample or "[no entries in this window]",
                },
                allowed_use="Evidence for diagnosing the reported problem",
            )
        )
    plan_record = ai_prompt_security.UntrustedRecord(
        record_id="plan",
        provenance="Troubleshooting plan produced in an earlier stage",
        content={
            "likely_cause": str(context.get("summary") or ""),
            "recommended_steps": list(context.get("steps") or []),
        },
        allowed_use="Context for why the logs were collected",
    )
    try:
        analysis_text = await _chat(
            llm,
            ai_prompt_security.build_messages(
                _ANALYSIS_INSTRUCTIONS,
                [_problem_record(str(context.get("problem") or "")), plan_record, *log_records],
                task="Analyse these logs to help determine the possible solutions.",
            ),
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - reported on the ticket
        await _note(
            ticket_id,
            NOTE_HEADER
            + "Stage 4 of 4 failed: the AI model could not analyse the logs ("
            + html.escape(str(exc)[:300])
            + "). The log bundle is attached to the ticket.</p>",
        )
        return
    body = _analysis_note(_extract_json_object(analysis_text), analysis_text, endpoint_label)
    if truncated_any:
        body += (
            "<p><em>Some logs were too long to send in full; error and warning "
            "lines were sent first. The full bundle is attached.</em></p>"
        )
    await _note(ticket_id, body)


def schedule_log_analysis(
    *,
    ticket_id: int,
    command: Mapping[str, Any],
    log_bytes: bytes,
    endpoint_label: str,
) -> bool:
    """Queue stage 4 for an uploaded bundle. Returns False when there is nothing to analyse."""

    log_text = decompress_log_bundle(log_bytes)
    if not log_text.strip():
        return False
    context = command_payload(command).get(tray_service.SERVER_CONTEXT_KEY)
    model = str(context.get("model") or "") if isinstance(context, Mapping) else ""
    llm = tray_service.build_troubleshoot_llm_config(model or None)
    _schedule(
        analyse_collected_logs(
            ticket_id=ticket_id,
            command=dict(command),
            log_text=log_text,
            endpoint_label=endpoint_label,
            llm=llm,
        ),
        description="log analysis",
        ticket_id=ticket_id,
    )
    return True


__all__ = [
    "MODE_COLLECT_LOGS",
    "WINDOWS_LOG_SOURCES",
    "MACOS_LOG_SOURCES",
    "allowed_log_sources",
    "analyse_collected_logs",
    "command_payload",
    "decompress_log_bundle",
    "endpoint_platform",
    "is_collect_logs_command",
    "run_research_and_plan",
    "schedule_log_analysis",
    "split_log_sections",
    "start_asset_troubleshoot",
]
