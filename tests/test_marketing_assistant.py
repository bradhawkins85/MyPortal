"""Tests for the Marketing Assistant feature pack and service."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.features import init_registry
from app.features.marketing_assistant import PACK
from app.features.marketing_assistant import routes as assistant_routes
from app.services import marketing_assistant as assistant


EXPECTED = {
    ("GET", "/admin/marketing/pages/{page_id}/assistant"),
    ("GET", "/admin/marketing/pages/{page_id}/assistant/status"),
    ("POST", "/admin/marketing/pages/{page_id}/assistant/sources"),
    ("POST", "/admin/marketing/assistant/sources/{source_id}/delete"),
    ("POST", "/admin/marketing/pages/{page_id}/assistant/run"),
    ("POST", "/admin/marketing/assistant/runs/{run_id}/restore"),
    ("GET", "/marketing/assistant-images/{image_id}"),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _source(source_id: str, text: str, url: str | None = None, images: int = 0) -> assistant.Source:
    url = url or f"https://{source_id.lower()}.example.com/page"
    source = assistant.Source(source_id=source_id, db_id=None, url=url, title=f"Page {source_id}", text=text)
    source.images = [
        assistant.ImageCandidate(
            image_id=f"I{source_id[1:]}{index}", url=f"{url}/img{index}.jpg", alt=f"Photo {index}",
            source_id=source_id, source_url=url,
        )
        for index in range(images)
    ]
    return source


# -- Feature pack ---------------------------------------------------------------


def test_pack_manifest_declares_routes():
    declared = {(method, route.path) for router in PACK.routers for route in router.routes
                for method in route.methods or set()}
    assert PACK.slug == "marketing_assistant"
    assert declared == EXPECTED


def test_pack_is_a_default_feature_pack():
    assert "marketing_assistant" in str(Settings.model_fields["feature_packs"].default).split(",")


def test_pack_loads_and_reloads_cleanly():
    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)
        await registry.load("marketing_assistant")
        await registry.reload("marketing_assistant")
        routes = [(method, route.path) for route in test_app.router.routes
                  for method in getattr(route, "methods", None) or set()]
        for key in EXPECTED:
            assert routes.count(key) == 1
        await registry.unload_all()

    asyncio.new_event_loop().run_until_complete(_run())


def test_split_urls_accepts_lines_commas_and_spaces():
    raw = "https://a.example.com\nhttps://b.example.com, https://c.example.com https://a.example.com\n\n"
    assert assistant_routes.split_urls(raw) == [
        "https://a.example.com", "https://b.example.com", "https://c.example.com",
    ]


# -- Settings ---------------------------------------------------------------------


def test_llm_config_falls_back_to_troubleshooter(monkeypatch):
    settings = Settings()
    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    monkeypatch.setattr(assistant.tray_service, "build_troubleshoot_llm_config",
                        lambda: {"base_url": "http://ts", "model": "ts-model", "api_key": ""})
    assert assistant.build_llm_config()["model"] == "ts-model"

    settings.marketing_assistant_llm_model = "marketing-model"
    config = assistant.build_llm_config()
    assert config == {"base_url": "http://127.0.0.1:11434", "model": "marketing-model", "api_key": ""}


@pytest.mark.anyio
async def test_source_urls_must_be_public_http():
    for bad in ("ftp://example.com/file", "https://user:pw@example.com/", "http://127.0.0.1/admin",
                "http://169.254.169.254/latest/meta-data", "not a url"):
        with pytest.raises(ValueError):
            await assistant.normalise_source_url(bad)


# -- Reading sources ----------------------------------------------------------------


def test_extract_images_keeps_content_images_only():
    markup = """
    <html><head><meta property="og:image" content="/hero.jpg"></head><body>
      <header><img src="/logo.png" alt="Logo"></header>
      <img src="product.jpg" alt="The product">
      <img src="/icon.svg" alt="Icon">
      <img src="/tracking-pixel.gif">
      <img src="small.png" width="16" height="16">
      <img data-src="/lazy.webp" alt="Lazy">
      <img src="data:image/png;base64,AAAA" srcset="/hi-res.jpg 2x, /low.jpg 1x">
    </body></html>
    """
    images = assistant.extract_images(markup, "https://vendor.example.com/products/item")
    assert images == [
        ("https://vendor.example.com/hero.jpg", ""),
        ("https://vendor.example.com/products/product.jpg", "The product"),
        ("https://vendor.example.com/lazy.webp", "Lazy"),
        ("https://vendor.example.com/hi-res.jpg", ""),
    ]


def test_inspect_image_accepts_photos_and_rejects_others():
    from PIL import Image

    def encode(size: tuple[int, int], fmt: str) -> bytes:
        buffer = io.BytesIO()
        Image.effect_noise(size, 64).convert("RGB").save(buffer, format=fmt)
        return buffer.getvalue()

    assert assistant.inspect_image(encode((400, 300), "JPEG")) == "image/jpeg"
    assert assistant.inspect_image(encode((400, 300), "PNG")) == "image/png"
    assert assistant.inspect_image(encode((80, 80), "PNG")) is None  # icon sized
    assert assistant.inspect_image(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>" * 100) is None
    assert assistant.inspect_image(b"not an image" * 500) is None


# -- Drafts and verification ----------------------------------------------------------


DRAFT = {
    "subtitle": {"text": "Managed backup for small business", "sources": ["S1"]},
    "intro": [{"text": "Backups run every 15 minutes.", "sources": ["S1"]}],
    "sections": [
        {
            "title": "Fast recovery",
            "image": "I11",
            "paragraphs": [[
                {"text": "Files can be restored in under 5 minutes.", "sources": ["S1", "S2"]},
                {"text": "It is the best product on the market.", "sources": ["S2"]},
            ]],
        },
        {
            "title": "Pricing",
            "image": None,
            "paragraphs": [[{"text": "Plans start at $99 per month.", "sources": ["S9"]}]],
        },
        {"title": "", "paragraphs": []},
        "not a section",
    ],
}


def test_parse_draft_numbers_statements_and_skips_malformed_items():
    draft = assistant.parse_draft("```json\n" + json.dumps(DRAFT) + "\n```")
    assert [section.title for section in draft.sections] == ["Fast recovery", "Pricing"]
    assert draft.sections[0].image == "I11"
    statements = draft.statements()
    assert [s.statement_id for s in statements] == ["C1", "C2", "C3", "C4", "C5"]
    assert statements[2].cited == ["S1", "S2"]


def test_numbers_must_appear_in_sources():
    assert assistant.numbers_confirmed("Restores in 5 minutes", ["Restore in 5 minutes or less"])
    assert assistant.numbers_confirmed("Costs $1,200 a year", ["Only $1200 per year"])
    assert assistant.numbers_confirmed("Uptime of 99.90%", ["99.9% uptime"])
    assert not assistant.numbers_confirmed("Restores in 3 minutes", ["Restore in 5 minutes"])
    assert assistant.numbers_confirmed("No numbers here", [])


def _verdicts(*results: dict[str, Any]):
    async def fake_chat(llm, messages):
        return json.dumps({"results": list(results)})
    return fake_chat


@pytest.mark.anyio
async def test_verify_rejects_unsupported_contradicted_and_uncited(monkeypatch):
    sources = [
        _source("S1", "Backups run every 15 minutes. Files restore in under 5 minutes."),
        _source("S2", "Restores take under 5 minutes. Rated highly by customers."),
    ]
    draft = assistant.parse_draft(json.dumps(DRAFT))
    statements = draft.statements()
    monkeypatch.setattr(assistant, "_chat", _verdicts(
        {"id": "C1", "verdict": "supported", "sources": ["S1"]},
        {"id": "C2", "verdict": "supported", "sources": ["S1"]},
        {"id": "C3", "verdict": "supported", "sources": ["S1", "S2"]},
        {"id": "C4", "verdict": "unsupported", "sources": [], "reason": "Opinion"},
    ))
    await assistant.verify_statements({"model": "m"}, statements, sources)
    by_id = {s.statement_id: s for s in statements}
    assert by_id["C1"].verified and by_id["C2"].verified and by_id["C3"].verified
    assert by_id["C3"].supported_by == ["S1", "S2"]
    assert not by_id["C4"].verified and "Opinion" in by_id["C4"].rejected_reason
    assert by_id["C5"].rejected_reason == "No source was cited for it."  # S9 does not exist


@pytest.mark.anyio
async def test_verify_checks_numbers_contradictions_and_required_sources(monkeypatch):
    sources = [_source("S1", "Plans from $99 per month."), _source("S2", "Plans from $109 per month.")]
    statements = [
        assistant.Statement("C1", "Plans start at $89 per month.", ["S1"], "Pricing"),
        assistant.Statement("C2", "Plans start at $99 per month.", ["S1"], "Pricing"),
        assistant.Statement("C3", "Plans start at $99 per month.", ["S1", "S2"], "Pricing"),
        assistant.Statement("C4", "Support is included.", ["S1"], "Pricing"),
    ]
    monkeypatch.setattr(assistant, "_chat", _verdicts(
        {"id": "C1", "verdict": "supported", "sources": ["S1", "S2"]},
        {"id": "C2", "verdict": "supported", "sources": ["S1"]},
        {"id": "C3", "verdict": "contradicted", "sources": ["S1"], "reason": "S2 says $109"},
    ))
    settings = Settings()
    settings.marketing_assistant_min_sources_per_claim = 2
    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    await assistant.verify_statements({"model": "m"}, statements, sources)
    assert "number" in statements[0].rejected_reason
    assert statements[1].rejected_reason == "Confirmed by 1 source(s); 2 are required."
    assert statements[2].rejected_reason.startswith("The sources disagree")
    assert statements[3].rejected_reason == "The fact check did not return a result for it."
    assert not any(statement.verified for statement in statements)


def test_required_confirmations_never_exceed_usable_sources(monkeypatch):
    settings = Settings()
    settings.marketing_assistant_min_sources_per_claim = 3
    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    assert assistant.required_confirmations(1) == 1
    assert assistant.required_confirmations(5) == 3


def test_sections_contain_only_verified_statements():
    sources = [_source("S1", "x"), _source("S2", "y")]
    draft = assistant.parse_draft(json.dumps(DRAFT))
    statements = {s.statement_id: s for s in draft.statements()}
    statements["C3"].supported_by = ["S1", "S2"]
    statements["C4"].rejected_reason = "Not stated by the sources."
    built = assistant.build_sections(draft, sources)
    assert len(built) == 1  # "Pricing" had nothing verified
    assert built[0].content_text == "Files can be restored in under 5 minutes."
    assert built[0].source_urls == [sources[0].url, sources[1].url]


# -- Full run --------------------------------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.page = {
            "id": 7, "title": "Managed backup", "subtitle": None, "intro_text": "Hand-written intro",
            "sections": [
                {"id": 1, "title": "Contact our team", "anchor_slug": "fast-recovery", "content_text": "Call us.",
                 "sort_order": 5, "assistant_generated": False},
                {"id": 2, "title": "Old", "anchor_slug": "old", "content_text": "Old text", "sort_order": 10,
                 "assistant_generated": True},
            ],
        }
        self.created: list[dict[str, Any]] = []
        self.deleted_assistant = False
        self.page_text: dict[str, Any] | None = None
        self.finished: dict[str, Any] = {}
        self.images: list[dict[str, Any]] = []


@pytest.fixture
def store(monkeypatch) -> _Store:
    data = _Store()

    async def get_page_by_id(page_id):
        return data.page

    async def list_sources(page_id):
        return [{"id": 1, "url": "https://s1.example.com/page"}, {"id": 2, "url": "https://s2.example.com/page"}]

    async def record_source_fetch(source_id, *, status, error):
        return None

    async def delete_assistant_sections(page_id):
        data.deleted_assistant = True

    async def create_section(**kwargs):
        data.created.append(kwargs)
        return kwargs

    async def update_page_text(page_id, *, subtitle, intro_text):
        data.page_text = {"subtitle": subtitle, "intro_text": intro_text}

    async def finish_run(run_id, **kwargs):
        data.finished = kwargs

    async def store_image(**kwargs):
        data.images.append(kwargs)
        return 40 + len(data.images)

    monkeypatch.setattr(assistant.marketing_repo, "get_page_by_id", get_page_by_id)
    monkeypatch.setattr(assistant.marketing_repo, "delete_assistant_sections", delete_assistant_sections)
    monkeypatch.setattr(assistant.marketing_repo, "create_section", create_section)
    monkeypatch.setattr(assistant.marketing_repo, "update_page_text", update_page_text)
    monkeypatch.setattr(assistant.repo, "list_sources", list_sources)
    monkeypatch.setattr(assistant.repo, "record_source_fetch", record_source_fetch)
    monkeypatch.setattr(assistant.repo, "finish_run", finish_run)
    monkeypatch.setattr(assistant.repo, "store_image", store_image)
    monkeypatch.setattr(assistant, "build_llm_config", lambda: {"base_url": "http://llm", "model": "m"})
    return data


@pytest.mark.anyio
async def test_run_writes_verified_sections_with_images(monkeypatch, store):
    async def read_sources(entries):
        return [
            _source("S1", "Backups run every 15 minutes. Files restore in under 5 minutes.",
                    url="https://s1.example.com/page", images=2),
            _source("S2", "Restores take under 5 minutes.", url="https://s2.example.com/page"),
        ]

    calls: list[str] = []

    async def fake_chat(llm, messages):
        if "strict fact checker" in messages[0]["content"]:
            calls.append("verify")
            return json.dumps({"results": [
                {"id": "C1", "verdict": "supported", "sources": ["S1"]},
                {"id": "C2", "verdict": "supported", "sources": ["S1"]},
                {"id": "C3", "verdict": "supported", "sources": ["S1", "S2"]},
                {"id": "C4", "verdict": "unsupported", "sources": []},
            ]})
        calls.append("draft")
        assert "CURRENT_ARTICLE" in messages[1]["content"]  # refresh shows the old sections
        return json.dumps(DRAFT)

    async def download_image(candidate):
        return b"jpeg-bytes-" + candidate.image_id.encode(), "image/jpeg"

    monkeypatch.setattr(assistant, "read_sources", read_sources)
    monkeypatch.setattr(assistant, "_chat", fake_chat)
    monkeypatch.setattr(assistant, "download_image", download_image)

    await assistant.run_assistant(3, 7, mode="refresh", instructions="Focus on SMBs", update_intro=True)

    assert calls == ["draft", "verify"]
    assert store.finished["status"] == "succeeded"
    assert store.finished["sections_written"] == 1
    assert store.finished["claims_verified"] == 3
    assert store.finished["images_used"] == 1
    rejected = store.finished["report"]["rejected"]
    assert {item["text"] for item in rejected} == {
        "It is the best product on the market.", "Plans start at $99 per month.",
    }
    assert store.finished["snapshot"]["intro_text"] == "Hand-written intro"
    assert store.deleted_assistant
    [section] = store.created
    assert section["assistant_generated"] is True
    assert section["anchor_slug"] == "fast-recovery-2"  # a hand-written section owns "fast-recovery"
    assert section["sort_order"] == 15
    assert section["image_id"] == 41
    assert section["image_caption"] == "Image: s1.example.com"
    assert "best product" not in section["content_text"]
    assert store.images[0]["image_url"] == "https://s1.example.com/page/img1.jpg"  # the model's pick, I11
    assert store.page_text == {"subtitle": "Managed backup for small business",
                               "intro_text": "Backups run every 15 minutes."}


@pytest.mark.anyio
async def test_run_leaves_page_alone_when_nothing_verifies(monkeypatch, store):
    async def read_sources(entries):
        return [_source("S1", "Some text", url="https://s1.example.com/page")]

    async def fake_chat(llm, messages):
        if "strict fact checker" in messages[0]["content"]:
            return json.dumps({"results": []})
        return json.dumps(DRAFT)

    monkeypatch.setattr(assistant, "read_sources", read_sources)
    monkeypatch.setattr(assistant, "_chat", fake_chat)

    await assistant.run_assistant(4, 7, mode="generate", instructions=None, update_intro=True)

    assert store.finished["status"] == "failed"
    assert "could be verified" in store.finished["error"]
    assert not store.created and not store.deleted_assistant and store.page_text is None


@pytest.mark.anyio
async def test_run_fails_when_no_source_can_be_read(monkeypatch, store):
    async def read_sources(entries):
        failed = _source("S1", "", url="https://s1.example.com/page")
        failed.error = "The website returned HTTP 404"
        return [failed]

    monkeypatch.setattr(assistant, "read_sources", read_sources)
    await assistant.run_assistant(5, 7, mode="generate", instructions=None, update_intro=False)
    assert store.finished["status"] == "failed"
    assert store.finished["error"] == "None of the source websites could be read."
    assert store.finished["report"]["sources"][0]["error"] == "The website returned HTTP 404"
