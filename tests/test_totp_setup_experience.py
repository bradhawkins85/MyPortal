import base64
from pathlib import Path

from app.api.routes.auth import _totp_qr_code_data_uri


def test_totp_setup_qr_is_embedded_png():
    data_uri = _totp_qr_code_data_uri("otpauth://totp/MyPortal:user@example.com?secret=ABC")

    prefix, encoded = data_uri.split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")


def test_totp_enrolment_hides_manual_values_and_navigation_by_default():
    template = Path("app/templates/auth/totp_enrol.html").read_text()

    assert "{% block sidebar_menu %}{% endblock %}" in template
    assert "data-totp-qr" in template
    assert "Cannot scan the code?" in template
    assert 'data-totp-manual hidden' in template
    assert 'aria-expanded="false"' in template
    assert 'aria-controls="totp-manual-setup"' in template


def test_totp_enrolment_toggle_controls_both_manual_values():
    script = Path("app/static/js/totp_enrol.js").read_text()

    assert "manualSetup.hidden = !showing" in script
    assert "manualToggle.setAttribute('aria-expanded', String(showing))" in script
    assert "showing ? 'Hide manual setup' : 'Cannot scan the code?'" in script
    assert "secretInput.value = expanded ? setupSecret : ''" in script
    assert "linkInput.value = expanded ? setupLink : ''" in script
    assert "startSetup()" not in script[script.index("manualToggle.addEventListener"):script.index("if (logoutButton)")]


def test_totp_enrolment_has_compact_loading_and_copy_feedback():
    script = Path("app/static/js/totp_enrol.js").read_text()
    stylesheet = Path("app/static/css/app.css").read_text()

    assert "setQrLoading(true)" in script
    assert "setQrLoading(false)" in script
    assert "button.textContent = 'Copied'" in script
    assert "width: 240px" in stylesheet
    assert ".totp-qr__placeholder" in stylesheet
    assert "aspect-ratio: 1" in stylesheet


def test_totp_enrolment_layout_stacks_without_horizontal_overflow():
    stylesheet = Path("app/static/css/app.css").read_text()

    assert "width: min(100%, 960px)" in stylesheet
    assert "grid-template-columns: auto minmax(0, 1fr)" in stylesheet
    assert "@media (max-width: 720px)" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr)" in stylesheet
    assert "text-overflow: ellipsis" in stylesheet


def test_chat_menu_requires_chat_permission():
    template = Path("app/templates/base.html").read_text()

    assert "{% if can_access_chat %}" in template
    assert "is_helpdesk_technician | default(false)) or can_access_chat" not in template
