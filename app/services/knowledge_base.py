from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

import bleach

from app.core.logging import log_error
from app.repositories import knowledge_base as kb_repo
from app.services import company_access
from app.services import modules as modules_service
from app.services.tagging import filter_helpful_texts
from app.services.realtime import RefreshNotifier, refresh_notifier

PermissionScope = str

_ALLOWED_TAGS: Sequence[str] = (
    "a",
    "abbr",
    "blockquote",
    "code",
    "em",
    "strong",
    "ul",
    "ol",
    "li",
    "p",
    "pre",
    "br",
    "h2",
    "h3",
    "h4",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "img",
)

_ALLOWED_ATTRIBUTES: Mapping[str, Sequence[str]] = {
    "a": ("href", "title", "target", "rel"),
    "abbr": ("title",),
    "img": ("src", "alt", "title"),
    "th": ("colspan", "rowspan", "scope"),
    "td": ("colspan", "rowspan", "headers"),
}

_ALLOWED_PROTOCOLS: Sequence[str] = ("http", "https", "mailto")

_TAG_JSON_PATTERN = re.compile(r"\[[^\]]*\]")
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _sanitise_html(value: str) -> str:
    return bleach.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


def _combine_sections_html(sections: Sequence[Mapping[str, Any]]) -> str:
    rendered: list[str] = []
    for index, section in enumerate(sections, start=1):
        heading = section.get("heading")
        content = section.get("content") or ""
        heading_html = ""
        if heading:
            heading_html = f"<h2>{html.escape(str(heading))}</h2>"
        rendered.append(
            f'<section class="kb-article__section" data-section-index="{index}">{heading_html}{content}</section>'
        )
    return "".join(rendered)


def _prepare_sections(
    sections: Sequence[Mapping[str, Any]] | None,
    *,
    fallback_content: str | None,
    fallback_title: str | None,
) -> tuple[list[dict[str, Any]], str]:
    prepared: list[dict[str, Any]] = []
    if sections:
        for index, section in enumerate(sections, start=1):
            content = section.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            content = _sanitise_html(content)
            heading = section.get("heading")
            heading_text = str(heading).strip() if isinstance(heading, str) else ""
            if heading_text:
                heading_text = heading_text[:255]
            else:
                heading_text = ""
            prepared.append(
                {
                    "heading": heading_text or None,
                    "content": content,
                    "position": index,
                }
            )
    if not prepared and fallback_content:
        content = _sanitise_html(str(fallback_content))
        heading_text = (fallback_title or "").strip()
        prepared.append(
            {
                "heading": heading_text[:255] or None,
                "content": content,
                "position": 1,
            }
        )
    combined = _combine_sections_html(prepared)
    return prepared, combined


def _extract_sections_sequence(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        collected: list[Mapping[str, Any]] = []
        for item in value:
            if isinstance(item, Mapping):
                collected.append(item)
        return collected
    return []


def _render_ai_tag_prompt(
    title: str,
    summary: str | None,
    sections: Sequence[Mapping[str, Any]],
    fallback_content: str,
) -> str:
    clean_title = title.strip() or "Untitled article"
    clean_summary = (summary or "").strip()
    lines = [
        "You classify knowledge base articles by topic.",
        "Generate between 5 and 10 concise tags (1-3 words) describing the main subjects.",
        "Return only a JSON array of lowercase strings.",
        "",
        f"Title: {clean_title}",
        f"Summary: {clean_summary or '(none provided)'}",
        "",
        "Sections:",
    ]
    included = 0
    for section in sections:
        if included >= 6:
            break
        content = section.get("content") or ""
        text_content = bleach.clean(str(content), tags=[], strip=True)
        text_content = " ".join(text_content.split())
        if not text_content:
            continue
        heading = section.get("heading") or f"Section {included + 1}"
        snippet = text_content[:400]
        lines.append(f"{included + 1}. {heading}: {snippet}")
        included += 1
    if included == 0:
        fallback_text = bleach.clean(str(fallback_content), tags=[], strip=True)
        fallback_text = " ".join(fallback_text.split())
        if fallback_text:
            lines.append(fallback_text[:600])
    lines.extend([
        "",
        'Example output: ["networking", "setup", "security"]',
    ])
    return "\n".join(lines)


def _parse_ai_tag_text(raw: str) -> list[str]:
    if not raw:
        return []
    text = raw.strip()
    candidates: list[Any] = []

    def _decode(value: str) -> list[Any] | None:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, list):
            return parsed
        return None

    decoded = _decode(text)
    if decoded is None:
        match = _TAG_JSON_PATTERN.search(text)
        if match:
            decoded = _decode(match.group(0))
    if decoded is None:
        stripped_lines = [segment.strip(" \t-*#•\u2022") for segment in re.split(r"[,\n;]+", text)]
        decoded = [segment for segment in stripped_lines if segment]
    candidates = decoded if decoded is not None else []

    tags: list[str] = []
    for item in candidates:
        if item is None:
            continue
        tag = str(item).strip().lower()
        if not tag:
            continue
        tag = re.sub(r"\s+", " ", tag)
        tags.append(tag)
    helpful = filter_helpful_texts(tags)
    return helpful[:10]


async def _schedule_article_ai_tags(
    article_id: int,
    title: str,
    summary: str | None,
    sections: Sequence[Mapping[str, Any]],
    combined_content: str,
    *,
    notifier: RefreshNotifier | None = None,
) -> None:
    prompt = _render_ai_tag_prompt(title, summary, sections, combined_content)

    async def _apply_result(result: Mapping[str, Any]) -> None:
        status = str(result.get("status") or result.get("event_status") or "").lower()
        if status == "queued":
            return
        if status == "skipped":
            return
        payload = result.get("response")
        text: str | None = None
        if isinstance(payload, Mapping):
            text = payload.get("response") or payload.get("message") or payload.get("text")
        elif isinstance(payload, str):
            text = payload
        if not text:
            message = result.get("message")
            if isinstance(message, str):
                text = message
        if not text:
            log_error("Knowledge base AI tag generation returned empty response")
            return
        tags = _parse_ai_tag_text(text)
        if not tags:
            log_error("Knowledge base AI tag parsing yielded no tags")
            return
        
        # Fetch the current article to get excluded tags
        article = await kb_repo.get_article_by_id(article_id)
        if article:
            excluded_tags = article.get("excluded_ai_tags", [])
            # Filter out any tags that have been manually excluded
            tags = [tag for tag in tags if tag not in excluded_tags]
        
        await kb_repo.update_article(article_id, ai_tags=tags)
        resolved_notifier = notifier or refresh_notifier
        await resolved_notifier.broadcast_refresh(
            reason="knowledge_base:article_tags_refreshed"
        )

    try:
        response = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt},
            on_complete=_apply_result,
        )
    except ValueError:
        return
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("Knowledge base AI tag generation failed", error=str(exc))
        return

    if str(response.get("status") or "").lower() not in {"queued", "skipped"}:
        # For immediate synchronous completions we still invoke the callback
        await _apply_result(response)


@dataclass(slots=True)
class ArticleAccessContext:
    user: Mapping[str, Any] | None
    user_id: int | None
    is_super_admin: bool
    memberships: dict[int, Mapping[str, Any]]


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _normalise_ids(values: Iterable[int]) -> list[int]:
    normalised: list[int] = []
    for value in values:
        try:
            normalised.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(normalised))


async def build_access_context(user: Mapping[str, Any] | None) -> ArticleAccessContext:
    if not user:
        return ArticleAccessContext(user=None, user_id=None, is_super_admin=False, memberships={})
    try:
        user_id = int(user.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        user_id = None
    memberships: dict[int, Mapping[str, Any]] = {}
    if user_id is not None:
        try:
            membership_rows = await company_access.list_accessible_companies(user)
        except Exception as exc:  # pragma: no cover - defensive
            log_error("Failed to list company memberships for knowledge base", error=str(exc))
            membership_rows = []
        for membership in membership_rows:
            company_id = membership.get("company_id")
            try:
                company_id_int = int(company_id)
            except (TypeError, ValueError):
                continue
            memberships[company_id_int] = membership
    is_super_admin = bool(user.get("is_super_admin"))
    return ArticleAccessContext(
        user=user,
        user_id=user_id,
        is_super_admin=is_super_admin,
        memberships=memberships,
    )


def _article_visible(article: Mapping[str, Any], context: ArticleAccessContext) -> bool:
    scope = str(article.get("permission_scope") or "anonymous")
    if scope == "anonymous":
        return True
    if context.is_super_admin:
        return True
    if context.user_id is None:
        return False
    if scope == "super_admin":
        return context.is_super_admin
    if scope == "user":
        allowed = _normalise_ids(article.get("allowed_user_ids", []))
        return context.user_id in allowed
    if scope == "company":
        companies = _normalise_ids(article.get("company_ids", []))
        if not companies:
            return bool(context.memberships)
        return any(company_id in context.memberships for company_id in companies)
    if scope == "company_admin":
        admin_companies = _normalise_ids(article.get("company_admin_ids", []))
        if admin_companies:
            return any(
                company_id in context.memberships and bool(context.memberships[company_id].get("is_admin"))
                for company_id in admin_companies
            )
        return any(bool(membership.get("is_admin")) for membership in context.memberships.values())
    return False


def _serialise_article(
    article: Mapping[str, Any],
    *,
    include_content: bool,
    include_permissions: bool,
) -> dict[str, Any]:
    base = {
        "id": int(article.get("id")),
        "slug": str(article.get("slug")),
        "title": str(article.get("title")),
        "summary": article.get("summary"),
        "ai_tags": list(article.get("ai_tags") or []),
        "excluded_ai_tags": list(article.get("excluded_ai_tags") or []),
        "permission_scope": str(article.get("permission_scope")),
        "is_published": bool(article.get("is_published")),
        "updated_at": article.get("updated_at_utc"),
        "updated_at_iso": _isoformat(article.get("updated_at_utc")),
        "published_at": article.get("published_at_utc"),
        "published_at_iso": _isoformat(article.get("published_at_utc")),
        "allowed_user_ids": [],
        "allowed_company_ids": [],
        "company_admin_ids": [],
        "sections": [],
        "created_by": article.get("created_by"),
        "created_at": article.get("created_at_utc"),
        "created_at_iso": _isoformat(article.get("created_at_utc")),
    }
    sections_payload = article.get("sections") or []
    serialised_sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections_payload, start=1):
        content = section.get("content") or ""
        heading = section.get("heading")
        serialised_sections.append(
            {
                "position": section.get("position") or index,
                "heading": heading if isinstance(heading, str) else None,
                "content": content,
            }
        )
    base["sections"] = serialised_sections
    if include_content:
        article_content = article.get("content")
        if not article_content and serialised_sections:
            article_content = _combine_sections_html(serialised_sections)
        base["content"] = article_content or ""
    if include_permissions:
        base.update(
            {
                "allowed_user_ids": _normalise_ids(article.get("allowed_user_ids", [])),
                "allowed_company_ids": _normalise_ids(article.get("company_ids", [])),
                "company_admin_ids": _normalise_ids(article.get("company_admin_ids", [])),
            }
        )
    return base


async def list_articles_for_context(
    context: ArticleAccessContext,
    *,
    include_unpublished: bool = False,
    include_permissions: bool = False,
) -> list[dict[str, Any]]:
    articles = await kb_repo.list_articles(include_unpublished=include_unpublished)
    visible: list[dict[str, Any]] = []
    for article in articles:
        if include_unpublished or article.get("is_published"):
            if _article_visible(article, context) or include_permissions:
                visible.append(
                    _serialise_article(
                        article,
                        include_content=False,
                        include_permissions=include_permissions,
                    )
                )
        elif include_permissions and context.is_super_admin:
            visible.append(
                _serialise_article(
                    article,
                    include_content=False,
                    include_permissions=True,
                )
            )
    return visible


async def get_article_by_slug_for_context(
    slug: str,
    context: ArticleAccessContext,
    *,
    include_unpublished: bool = False,
    include_permissions: bool = False,
) -> dict[str, Any] | None:
    article = await kb_repo.get_article_by_slug(slug)
    if not article:
        return None
    if not include_unpublished and not article.get("is_published") and not context.is_super_admin:
        return None
    if not _article_visible(article, context) and not (include_permissions and context.is_super_admin):
        return None
    return _serialise_article(
        article,
        include_content=True,
        include_permissions=include_permissions,
    )


async def create_article(
    payload: Mapping[str, Any],
    *,
    author_id: int | None,
    notifier: RefreshNotifier | None = None,
) -> dict[str, Any]:
    permission_scope = str(payload.get("permission_scope") or "anonymous")
    is_published = bool(payload.get("is_published", False))
    now = datetime.now(timezone.utc)
    published_at = now if is_published else None
    sections_input = _extract_sections_sequence(payload.get("sections"))
    prepared_sections, combined_content = _prepare_sections(
        sections_input,
        fallback_content=payload.get("content"),
        fallback_title=payload.get("title"),
    )
    if not combined_content:
        raise ValueError("At least one section with content is required")
    title_value = str(payload.get("title"))
    summary_value = payload.get("summary")
    created = await kb_repo.create_article(
        slug=str(payload.get("slug")),
        title=title_value,
        summary=summary_value,
        content=combined_content,
        permission_scope=permission_scope,
        is_published=is_published,
        published_at=published_at,
        created_by=author_id,
        ai_tags=None,
    )
    await _sync_relations(created["id"], permission_scope, payload)
    await kb_repo.replace_article_sections(created["id"], prepared_sections)
    refreshed = await kb_repo.get_article_by_id(created["id"])
    if not refreshed:
        raise RuntimeError("Failed to load knowledge base article after creation")
    resolved_notifier = notifier or refresh_notifier
    await resolved_notifier.broadcast_refresh(reason="knowledge_base:article_created")
    await _schedule_article_ai_tags(
        created["id"],
        title_value,
        summary_value,
        prepared_sections,
        combined_content,
        notifier=notifier,
    )
    return refreshed


async def update_article(
    article_id: int,
    payload: Mapping[str, Any],
    *,
    notifier: RefreshNotifier | None = None,
) -> dict[str, Any]:
    current = await kb_repo.get_article_by_id(article_id)
    if not current:
        raise ValueError("Article not found")
    updates: dict[str, Any] = {}
    if "slug" in payload:
        updates["slug"] = payload.get("slug")
    if "title" in payload:
        updates["title"] = payload.get("title")
    if "summary" in payload:
        updates["summary"] = payload.get("summary")
    sections_update_required = False
    prepared_sections: list[dict[str, Any]] = _extract_sections_sequence(current.get("sections"))
    if "sections" in payload or "content" in payload or "title" in payload:
        sections_payload = payload.get("sections") if "sections" in payload else current.get("sections")
        prepared_sections, combined_content = _prepare_sections(
            _extract_sections_sequence(sections_payload),
            fallback_content=payload.get("content") if "content" in payload else current.get("content"),
            fallback_title=payload.get("title") if "title" in payload else current.get("title"),
        )
        if not combined_content:
            raise ValueError("At least one section with content is required")
        updates["content"] = combined_content
        sections_update_required = True
    if "permission_scope" in payload:
        updates["permission_scope"] = payload.get("permission_scope")
    published_flag = payload.get("is_published")
    if published_flag is not None:
        updates["is_published"] = bool(published_flag)
        updates["published_at"] = datetime.now(timezone.utc) if updates["is_published"] else None
    title_for_ai_source = updates.get("title", current.get("title"))
    title_for_ai = str(title_for_ai_source) if title_for_ai_source is not None else ""
    summary_for_ai = updates.get("summary") if "summary" in updates else current.get("summary")
    content_for_ai = updates.get("content", current.get("content") or "")
    if updates:
        current = await kb_repo.update_article(article_id, **updates)
    permission_scope = str(current.get("permission_scope"))
    await _sync_relations(article_id, permission_scope, payload)
    if sections_update_required:
        await kb_repo.replace_article_sections(article_id, prepared_sections)
    refreshed = await kb_repo.get_article_by_id(article_id)
    if not refreshed:
        raise RuntimeError("Failed to refresh article after update")
    await _schedule_article_ai_tags(
        article_id,
        title_for_ai,
        summary_for_ai,
        prepared_sections,
        str(content_for_ai),
        notifier=notifier,
    )
    resolved_notifier = notifier or refresh_notifier
    await resolved_notifier.broadcast_refresh(reason="knowledge_base:article_updated")
    return refreshed


async def delete_article(article_id: int, *, notifier: RefreshNotifier | None = None) -> None:
    await kb_repo.delete_article(article_id)
    resolved_notifier = notifier or refresh_notifier
    await resolved_notifier.broadcast_refresh(reason="knowledge_base:article_deleted")


async def refresh_article_ai_tags(article_id: int, *, notifier: RefreshNotifier | None = None) -> None:
    """Refresh the AI-generated tags for a knowledge base article."""
    article = await kb_repo.get_article_by_id(article_id)
    if not article:
        return
    
    title = str(article.get("title", ""))
    summary = article.get("summary")
    sections = article.get("sections", [])
    content = article.get("content", "")
    
    await _schedule_article_ai_tags(
        article_id,
        title,
        summary,
        sections,
        content,
        notifier=notifier,
    )



async def _sync_relations(article_id: int, permission_scope: str, payload: Mapping[str, Any]) -> None:
    allowed_users = payload.get("allowed_user_ids") or []
    allowed_companies = payload.get("allowed_company_ids") or []
    await kb_repo.replace_article_users(article_id, allowed_users if permission_scope == "user" else [])
    if permission_scope == "company":
        await kb_repo.replace_article_companies(article_id, allowed_companies, require_admin=False)
        await kb_repo.replace_article_companies(article_id, [], require_admin=True)
    elif permission_scope == "company_admin":
        await kb_repo.replace_article_companies(article_id, allowed_companies, require_admin=True)
        await kb_repo.replace_article_companies(article_id, [], require_admin=False)
    else:
        await kb_repo.replace_article_companies(article_id, [], require_admin=False)
        await kb_repo.replace_article_companies(article_id, [], require_admin=True)


def _build_excerpt(content: str, query: str, summary: str | None) -> str | None:
    lowered = content.lower()
    query_lower = query.lower()
    index = lowered.find(query_lower)
    if index == -1:
        source = summary or content
        if not source:
            return None
        return source[:240].strip()
    start = max(0, index - 160)
    end = min(len(content), index + 160)
    excerpt = content[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(content):
        excerpt = excerpt + "…"
    return excerpt


def _render_prompt(query: str, articles: list[Mapping[str, Any]]) -> str:
    lines = [
        "You are an assistant helping users navigate a knowledge base.",
        "Summarise the relevant articles for the query below.",
        "Always cite article slugs in your response.",
        "",
        f"Query: {query}",
        "",
        "Articles:",
    ]
    for article in articles:
        content = str(article.get("content") or "")
        snippet = content[:1000]
        lines.extend(
            [
                f"- Title: {article.get('title')}",
                f"  Slug: {article.get('slug')}",
                f"  Summary: {article.get('summary') or 'N/A'}",
                "  Content snippet:",
                f"  {snippet}",
                "",
            ]
        )
    lines.append("Provide a concise answer with bullet points when appropriate.")
    return "\n".join(lines)


def _tokenise(text: str) -> list[str]:
    if not text:
        return []
    return _WORD_PATTERN.findall(text.lower())


def _score_article(article: Mapping[str, Any], *, query: str, tokens: Sequence[str]) -> int:
    if not tokens:
        return 0

    haystack_parts: list[str] = []
    title = article.get("title") or ""
    summary = article.get("summary") or ""
    content = article.get("content") or ""
    slug = article.get("slug") or ""
    ai_tags = article.get("ai_tags") or []
    sections = article.get("sections") or []

    for value in (title, summary, content, slug):
        if isinstance(value, str):
            haystack_parts.append(value.lower())

    if isinstance(ai_tags, Sequence) and not isinstance(ai_tags, (str, bytes)):
        for tag in ai_tags:
            if isinstance(tag, str):
                haystack_parts.append(tag.lower())

    if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)):
        for section in sections:
            if isinstance(section, Mapping):
                heading = section.get("heading")
                if isinstance(heading, str):
                    haystack_parts.append(heading.lower())

    haystack_text = " ".join(haystack_parts)
    if not haystack_text:
        return 0

    score = 0
    query_lower = query.lower()
    if query_lower and query_lower in haystack_text:
        score += max(len(query_lower), 4)

    haystack_tokens = Counter(_tokenise(haystack_text))
    for token in tokens:
        occurrences = haystack_tokens.get(token, 0)
        if occurrences:
            score += min(occurrences, 3)

    return score


async def search_articles(
    query: str,
    context: ArticleAccessContext,
    *,
    limit: int = 8,
    use_ollama: bool = True,
) -> dict[str, Any]:
    candidates = await kb_repo.list_articles(include_unpublished=context.is_super_admin)
    visible: list[dict[str, Any]] = []
    tokens = _tokenise(query)
    scored: list[tuple[int, float, Mapping[str, Any]]] = []
    for article in candidates:
        if not article.get("is_published") and not context.is_super_admin:
            continue
        if not _article_visible(article, context):
            continue
        score = _score_article(article, query=query, tokens=tokens)
        if score <= 0:
            continue
        updated = article.get("updated_at_utc") or article.get("updated_at")
        updated_ts = 0.0
        if isinstance(updated, datetime):
            updated_ts = updated.timestamp()
        scored.append((score, updated_ts, article))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    visible = [dict(item[2]) for item in scored[:limit]]
    results: list[dict[str, Any]] = []
    for article in visible:
        content = str(article.get("content") or "")
        summary = article.get("summary")
        results.append(
            {
                "id": int(article.get("id")),
                "slug": str(article.get("slug")),
                "title": str(article.get("title")),
                "summary": summary,
                "excerpt": _build_excerpt(content, query, summary),
                "updated_at_iso": _isoformat(article.get("updated_at_utc")),
            }
        )
    ollama_status = "skipped"
    ollama_model: str | None = None
    ollama_summary: str | None = None
    if results and use_ollama:
        prompt_articles = visible[: min(3, len(visible))]
        prompt = _render_prompt(query, prompt_articles)
        try:
            response = await modules_service.trigger_module(
                "ollama", {"prompt": prompt}, background=False
            )
        except Exception as exc:  # pragma: no cover - network interaction
            log_error("Knowledge base Ollama search failed", error=str(exc))
            ollama_status = "error"
            ollama_summary = None
        else:
            ollama_status = str(response.get("status") or "unknown")
            ollama_model = response.get("model")
            payload = response.get("response")
            if isinstance(payload, Mapping):
                ollama_summary = payload.get("response") or payload.get("message")
                if not ollama_model:
                    model_candidate = payload.get("model")
                    if isinstance(model_candidate, str):
                        ollama_model = model_candidate
            elif isinstance(payload, str):
                ollama_summary = payload
    return {
        "results": results,
        "ollama_status": ollama_status,
        "ollama_model": ollama_model,
        "ollama_summary": ollama_summary,
    }

