"""Audit MyPortal templates against the gold standards in ``docs/design.md``.

Usage::

    python3 docs/design_audit_scan.py > docs/design_audit.md

The script walks every page-level Jinja template under ``app/templates/``
and emits a Markdown report covering three standards:

1. Header **Actions menu** (`page_header_actions` macro / `header-title-menu`)
2. **Page statistics strip** (`counter_strip` / `.stat-strip`)
3. **Popup modals** (the ``<div class="modal" hidden>`` pattern)

The classifier is intentionally conservative — ``PARTIAL`` and ``FAIL?``
verdicts should be hand-reviewed before remediation.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"

SKIP_PATHS: set[str] = {
    "base.html",
    "errors/error.html",
    "auth/login.html",
    "auth/register.html",
    "bcp/layout.html",
    "bcp/stub.html",
    "bcp/heatmap_partial.html",
    "bcp/export/bcp_pdf.html",
}
SKIP_PREFIXES: tuple[str, ...] = ("macros/", "partials/", "chat/_message")


def classify(rel: str, text: str) -> dict:
    findings = {
        "rel": rel,
        "actions": "n/a",
        "stats": "n/a",
        "modals": "n/a",
        "notes": [],
    }

    # ---- Actions menu ---------------------------------------------------
    has_header_actions_block = "{% block header_actions" in text
    uses_macro = "page_header_actions" in text
    uses_legacy = "header-title-menu" in text
    if uses_macro or uses_legacy:
        findings["actions"] = "PASS"
    elif has_header_actions_block:
        findings["actions"] = "PARTIAL"
        findings["notes"].append(
            "header_actions block present but doesn't use page_header_actions/header-title-menu"
        )
    else:
        if "button--primary" in text:
            findings["actions"] = "FAIL"
            findings["notes"].append(
                "primary button rendered in body, not in header_actions"
            )
        else:
            findings["actions"] = "OK"

    # ---- Stat strip -----------------------------------------------------
    uses_strip = "stat-strip" in text or "counter_strip" in text
    has_kpi_like = bool(
        re.search(
            r"(open_count|pending_count|active_count|total_count|count\s*:|stat__|kpi)",
            text,
            re.I,
        )
    )
    if uses_strip:
        has_total = (
            "stat-strip__stat--total" in text
            or "total=" in text
            or "total_label" in text
        )
        has_variant = "stat-strip__stat--" in text or any(
            f"'variant': '{v}'" in text or f'"variant": "{v}"' in text
            for v in (
                "success", "info", "warning", "danger", "neutral",
                "total", "operational", "outage", "degraded",
                "partial_outage", "maintenance",
            )
        )
        if has_total and has_variant:
            findings["stats"] = "PASS"
        else:
            findings["stats"] = "PARTIAL"
            findings["notes"].append(
                "stat-strip used but missing total tile or variant"
            )
    elif has_kpi_like:
        findings["stats"] = "FAIL?"
        findings["notes"].append(
            "page appears to render counts; consider counter_strip"
        )

    # ---- Modals ---------------------------------------------------------
    modals = []
    for m in re.finditer(
        r"<(div|dialog)\b[^>]*class=\"[^\"]*\bmodal\b[^\"]*\"[^>]*>", text
    ):
        tag = m.group(1)
        snippet = m.group(0)
        offset = m.start()
        end = text.find("</" + tag + ">", offset)
        block = (
            text[offset : end + len(tag) + 3]
            if end != -1
            else text[offset : offset + 4000]
        )
        problems: list[str] = []
        if tag == "dialog":
            problems.append("uses <dialog> not <div>")
        if 'style="display' in snippet:
            problems.append('uses style="display:none"')
        if 'aria-modal="true"' not in snippet:
            problems.append("missing aria-modal")
        if "aria-labelledby" not in snippet and tag == "div":
            problems.append("missing aria-labelledby")
        if 'aria-hidden="true"' not in snippet and tag == "div":
            problems.append("missing aria-hidden")
        if (
            tag == "div"
            and " hidden" not in snippet
            and "hidden>" not in snippet
            and "hidden " not in snippet
        ):
            problems.append("missing hidden attr")
        if "modal__close" not in block:
            problems.append("missing modal__close")
        if "modal__title" not in block:
            problems.append("missing modal__title")
        if "data-modal-close" not in block:
            problems.append("no data-modal-close")
        modals.append({"tag": tag, "problems": problems})

    if modals:
        bad = [mo for mo in modals if mo["problems"]]
        if not bad:
            findings["modals"] = "PASS"
        else:
            findings["modals"] = f"FAIL ({len(bad)}/{len(modals)} non-conforming)"
            for mo in bad[:3]:
                findings["notes"].append(
                    f"modal({mo['tag']}): " + ", ".join(mo["problems"])
                )

    # Cards with primary buttons
    cards_with_primary = len(
        re.findall(r"card[^\"]*\"[^>]*>[\s\S]{0,2000}?button--primary", text)
    )
    if cards_with_primary > 0 and (
        has_header_actions_block or uses_macro or uses_legacy
    ):
        findings["notes"].append(
            f"~{cards_with_primary} primary button(s) inside card bodies"
        )
    return findings


def _collect_rows() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        rel = path.relative_to(TEMPLATES_DIR).as_posix()
        if rel in SKIP_PATHS or any(rel.startswith(p) for p in SKIP_PREFIXES):
            continue
        text = path.read_text(errors="replace")
        if "{% extends" not in text:
            continue
        rows.append(classify(rel, text))
    return rows


def _print(*args) -> None:
    print(*args)


def render_markdown(rows: list[dict]) -> None:
    actions = Counter(r["actions"] for r in rows)
    stats = Counter(r["stats"] for r in rows)
    modals_short = Counter(r["modals"].split(" ")[0] for r in rows)

    _print("# MyPortal Design Audit")
    _print()
    _print(
        "Generated by [`docs/design_audit_scan.py`](design_audit_scan.py) — "
        "a scripted check of every page"
    )
    _print(
        "template under `app/templates/` against the gold standards in"
    )
    _print("[`docs/design.md`](design.md).")
    _print()
    _print(
        "Each row records a programmatic verdict for the three primary"
    )
    _print(
        "standards. The classifier is intentionally conservative — `PARTIAL`"
    )
    _print("/ `FAIL?` rows should be hand-confirmed before remediation.")
    _print()
    _print("## Verdict legend")
    _print()
    _print("| Verdict | Meaning |")
    _print("|---|---|")
    _print("| `PASS` | Matches the gold standard |")
    _print("| `PARTIAL` | Standard partially applied — needs cleanup |")
    _print("| `FAIL` | Standard violated and must be remediated |")
    _print("| `FAIL?` | Heuristic flag — manual review required |")
    _print(
        "| `OK` | Standard not applicable to this page (e.g. no actions to render) |"
    )
    _print("| `n/a` | Page does not have this UI element |")
    _print()
    _print("## Summary")
    _print()
    _print(f"- **Pages audited:** {len(rows)}")
    _print()
    _print("| Standard | PASS | PARTIAL | FAIL | FAIL? | OK | n/a |")
    _print("|---|---:|---:|---:|---:|---:|---:|")
    for label, c in [
        ("Header actions menu", actions),
        ("Statistics strip", stats),
        ("Popup modals", modals_short),
    ]:
        _print(
            f"| {label} | {c.get('PASS', 0)} | {c.get('PARTIAL', 0)} | "
            f"{c.get('FAIL', 0)} | {c.get('FAIL?', 0)} | "
            f"{c.get('OK', 0)} | {c.get('n/a', 0)} |"
        )
    _print()
    _print("## Per-template results")
    _print()
    _print(
        "Grouped by directory. `Notes` is a short auto-generated description "
        "of the most likely violations; consult the template directly for context."
    )
    _print()

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        parts = r["rel"].split("/")
        grp = parts[0] if len(parts) > 1 else "_root_"
        groups[grp].append(r)

    for grp in sorted(groups):
        _print(f"### `{grp}/`")
        _print()
        _print("| Template | Actions | Stats | Modals | Notes |")
        _print("|---|---|---|---|---|")
        for r in sorted(groups[grp], key=lambda x: x["rel"]):
            notes = "; ".join(r["notes"]) if r["notes"] else ""
            if len(notes) > 200:
                notes = notes[:197] + "..."
            notes = notes.replace("|", "\\|")
            _print(
                f"| `{r['rel']}` | {r['actions']} | {r['stats']} | "
                f"{r['modals']} | {notes} |"
            )
        _print()

    _print("## Remediation priority")
    _print()
    _print(
        "The following lists collapse the per-template findings into the"
    )
    _print(
        "remediation batches outlined in the implementation plan."
    )
    _print()

    modal_fails = [r for r in rows if r["modals"].startswith("FAIL")]
    _print(f"### Modals to convert ({len(modal_fails)} templates)")
    _print()
    _print(
        "Convert `<dialog class=\"modal\">` and "
        "`<div class=\"modal\" style=\"display:none\">` to the standard"
    )
    _print(
        "`<div class=\"modal\" role=\"dialog\" aria-modal=\"true\" "
        "aria-labelledby=\"…\" aria-hidden=\"true\" hidden>` pattern with the"
    )
    _print(
        "`modal__close` / `modal__title` / `modal__subtitle` / `.form-actions`"
    )
    _print("structure documented in `docs/design.md` §3.")
    _print()
    for r in sorted(modal_fails, key=lambda x: x["rel"]):
        _print(f"- [ ] `{r['rel']}` — {r['modals']}")
    _print()

    action_fix = [r for r in rows if r["actions"] in ("FAIL", "PARTIAL")]
    _print(f"### Header actions to align ({len(action_fix)} templates)")
    _print()
    _print(
        "Adopt the `page_header_actions` macro (or the legacy `header-title-menu`"
    )
    _print(
        "markup) so each page has at most one `button--primary` and a single"
    )
    _print(
        "overflow `Actions ▾` menu. Move stray buttons out of card bodies."
    )
    _print()
    for r in sorted(action_fix, key=lambda x: x["rel"]):
        _print(f"- [ ] `{r['rel']}` — {r['actions']}")
    _print()

    stats_fix = [r for r in rows if r["stats"] in ("FAIL?", "PARTIAL")]
    _print(f"### Stat strip candidates ({len(stats_fix)} templates)")
    _print()
    _print(
        "Pages that surface aggregate counts but do not use `counter_strip`"
    )
    _print(
        "should adopt it (with semantic variants and a `Total` tile), or have"
    )
    _print(
        "the stat-strip explicitly waived in this audit if the counts are not"
    )
    _print(
        "the page-level KPIs (e.g. inline counts inside detail panels)."
    )
    _print()
    for r in sorted(stats_fix, key=lambda x: x["rel"]):
        _print(f"- [ ] `{r['rel']}` — {r['stats']}")
    _print()

    _print("## Methodology")
    _print()
    _print(
        "This audit was produced by a scripted classifier that:"
    )
    _print()
    _print(
        "1. Walks every `*.html` template under `app/templates/` that extends"
    )
    _print(
        "   `base.html` (partials, macros, and the BCP layout shell are skipped)."
    )
    _print(
        "2. For **header actions** — checks for `{% block header_actions %}`,"
    )
    _print(
        "   the `page_header_actions` macro, or the legacy `header-title-menu`"
    )
    _print(
        "   markup. Pages with neither but containing `button--primary` are"
    )
    _print(
        "   flagged `FAIL`; pages with no primary button at all are `OK`."
    )
    _print(
        "3. For **stats** — checks for `stat-strip` / `counter_strip` usage."
    )
    _print(
        "   Pages with KPI-shaped variables (`*_count`, `total_count`, etc.)"
    )
    _print(
        "   that do **not** use the strip are flagged `FAIL?` for review."
    )
    _print(
        "4. For **modals** — finds every element matching "
        "`<(div|dialog) … class=\"…modal…\">`"
    )
    _print(
        "   and verifies it is a `<div>`, has `role=\"dialog\"`, `aria-modal`,"
    )
    _print(
        "   `aria-labelledby`, `aria-hidden`, the `hidden` attribute, a"
    )
    _print(
        "   `modal__close` button, a `modal__title` heading, and a"
    )
    _print(
        "   `data-modal-close` close-trigger somewhere inside the block."
    )
    _print()
    _print(
        "Re-run the classifier (`python3 docs/design_audit_scan.py > docs/design_audit.md`)"
    )
    _print(
        "after each remediation batch to track progress; commit an updated copy"
    )
    _print(
        "of this document so the standard remains measurable."
    )


if __name__ == "__main__":
    render_markdown(_collect_rows())
