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


def test_chat_menu_requires_chat_permission():
    template = Path("app/templates/base.html").read_text()

    assert "{% if can_access_chat %}" in template
    assert "is_helpdesk_technician | default(false)) or can_access_chat" not in template
