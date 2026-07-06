"""Help documentation service.

Scans the ``docs/wiki`` directory for Markdown files, groups them by
subfolder (section), and renders individual articles to sanitised HTML.
"""

from __future__ import annotations

import pathlib
from typing import TypedDict

import nh3
from markdown_it import MarkdownIt

# Allowed HTML tags and attributes after markdown rendering
_ALLOWED_TAGS: frozenset[str] = frozenset((
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "a", "img",
    "details", "summary",
    "div", "span",
))

_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align", "colspan", "rowspan"},
    "td": {"align", "colspan", "rowspan"},
    "code": {"class"},
    "pre": {"class"},
    "div": {"class"},
    "span": {"class"},
}

_md = MarkdownIt("commonmark", {"breaks": False, "html": False}).enable("table")

# Path to the wiki directory relative to repo root
_WIKI_DIR = pathlib.Path(__file__).resolve().parents[3] / "docs" / "wiki"


class HelpArticle(TypedDict):
    name: str
    slug: str
    section: str
    section_slug: str
    path: pathlib.Path


class HelpSection(TypedDict):
    name: str
    slug: str
    articles: list[HelpArticle]


def _section_display_name(folder_name: str) -> str:
    """Convert a folder slug to a human-readable section name."""
    replacements = {
        "getting-started": "Getting Started",
        "api-reference": "API Reference",
        "business-continuity": "Business Continuity",
        "m365": "Microsoft 365",
    }
    if folder_name in replacements:
        return replacements[folder_name]
    return folder_name.replace("-", " ").title()


def list_sections(wiki_dir: pathlib.Path | None = None) -> list[HelpSection]:
    """Return all sections (subfolders) with their articles, sorted alphabetically.

    Sections are ordered: "Getting Started" first, then the rest alphabetically.
    Files within each section are sorted alphabetically by name.
    """
    base = wiki_dir or _WIKI_DIR
    if not base.exists():
        return []

    sections: list[HelpSection] = []

    for subdir in sorted(base.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith("."):
            continue

        articles: list[HelpArticle] = []
        for md_file in sorted(subdir.iterdir()):
            if md_file.suffix.lower() not in (".md", ".markdown") or not md_file.is_file():
                continue
            article_name = md_file.stem  # filename without extension
            articles.append(
                HelpArticle(
                    name=article_name,
                    slug=_to_url_slug(md_file.stem),
                    section=_section_display_name(subdir.name),
                    section_slug=subdir.name,
                    path=md_file,
                )
            )

        if articles:
            sections.append(
                HelpSection(
                    name=_section_display_name(subdir.name),
                    slug=subdir.name,
                    articles=articles,
                )
            )

    # Put "Getting Started" first
    sections.sort(key=lambda s: (0 if s["slug"] == "getting-started" else 1, s["name"].lower()))
    return sections


def find_article(section_slug: str, article_slug: str, wiki_dir: pathlib.Path | None = None) -> HelpArticle | None:
    """Locate a specific article by section and article slug."""
    base = wiki_dir or _WIKI_DIR
    section_dir = base / section_slug
    if not section_dir.is_dir():
        return None

    for md_file in section_dir.iterdir():
        if md_file.suffix.lower() not in (".md", ".markdown") or not md_file.is_file():
            continue
        if _to_url_slug(md_file.stem) == article_slug:
            return HelpArticle(
                name=md_file.stem,
                slug=article_slug,
                section=_section_display_name(section_slug),
                section_slug=section_slug,
                path=md_file,
            )
    return None


def render_article(article: HelpArticle) -> str:
    """Read and render the article's Markdown to sanitised HTML."""
    raw = article["path"].read_text(encoding="utf-8")
    html = _md.render(raw)
    return nh3.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS)


def _to_url_slug(name: str) -> str:
    """Convert a filename stem to a URL-safe slug."""
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
