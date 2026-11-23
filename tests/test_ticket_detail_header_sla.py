from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


def _render_header(ticket_sla: dict) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(("html",)))
    template = env.get_template("admin/ticket_detail.html")
    context = template.new_context(
        {
            "ai_agent_name": lambda: "Agent",
            "ticket": {"id": 7, "subject": "RE: QTY break pricing"},
            "ticket_sla": ticket_sla,
        }
    )
    return "".join(template.blocks["header_title"](context))


def test_header_hides_sla_badge_when_ticket_has_no_sla():
    header = _render_header({"state": "not_applicable", "label": "No SLA"})

    assert "RE: QTY break pricing" in header
    assert "ticket-detail-header__sla" not in header
    assert "No SLA" not in header


def test_header_shows_sla_badge_after_subject_when_sla_set():
    header = _render_header({"state": "on_track", "label": "On track", "name": "Gold"})

    assert "ticket-detail-header__sla" in header
    assert "Gold · On track" in header
    assert header.index("ticket-detail-header__subject") < header.index("ticket-detail-header__sla")
