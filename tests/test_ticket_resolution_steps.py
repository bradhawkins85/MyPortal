from pathlib import Path

from app.services import rag_index
from app.services import tickets as tickets_service


def test_resolution_prompt_emphasises_flagged_entries_without_dropping_context():
    prompt = tickets_service._render_resolution_prompt(
        {"id": 7, "subject": "Printer offline", "description": "Cannot print", "status": "resolved"},
        [
            {"id": 1, "body": "Reported error 42", "is_resolution_step": False},
            {"id": 2, "body": "Replaced the damaged cable", "is_resolution_step": True},
            {"id": 3, "body": "Unrelated lunch discussion", "is_not_resolution_step": True},
        ],
    )

    assert "FLAGGED RESOLUTION STEP" in prompt
    assert "Replaced the damaged cable" in prompt
    assert "Reported error 42" in prompt
    assert "Unrelated lunch discussion" not in prompt


def test_resolution_response_is_sanitised():
    result = tickets_service._extract_resolution_steps(
        '{"resolution_steps":"<ul><li>Restarted service</li></ul><script>alert(1)</script>"}'
    )

    assert result is not None
    assert "Restarted service" in result
    assert "script" not in result


def test_resolution_response_extracts_openai_compatible_markdown_json():
    result = tickets_service._extract_resolution_steps(
        {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"resolution_steps":"<ul><li>Restarted the PC</li></ul>"}\n```',
                        "reasoning_content": "Internal reasoning must not be displayed.",
                    }
                }
            ]
        }
    )

    assert result == "<ul><li>Restarted the PC</li></ul>"
    assert "reasoning" not in result


def test_ticket_rag_document_contains_resolution_steps():
    document = rag_index.document_from_source(
        "tickets",
        {
            "id": 9,
            "company_id": 3,
            "subject": "VPN failure",
            "resolution_steps": "<ol><li>Renewed the certificate</li></ol>",
            "permission_scope": {"version": 1, "visibility": "super_admin"},
        },
    )

    assert document is not None
    assert "[Resolution steps]" in document.text
    assert "Renewed the certificate" in document.text


def test_admin_ticket_template_exposes_resolution_controls():
    template = Path("app/templates/admin/ticket_detail.html").read_text(encoding="utf-8")

    assert 'name="isResolutionStep"' in template
    assert 'name="isNotResolutionStep"' in template
    assert 'value="excluded"' in template
    assert 'data-ticket-resolution-panel' in template
    assert 'name="resolutionSteps"' in template
    assert 'data-resolution-reprocess' in template
    assert 'replies/{{ reply.id }}/resolution-step' in template
    assert "Resolution step" in template
    assert template.index("AI Summary") < template.index("data-ticket-resolution-panel")
    assert template.index("data-ticket-resolution-panel") < template.index("data-ticket-tasks-card")


def test_resolution_reprocess_uses_global_toast():
    script = Path("app/static/js/ticket_detail.js").read_text(encoding="utf-8")

    assert "window.__portalToast.show" in script
    assert "payload.message || 'Resolution steps will be regenerated shortly.'" in script
