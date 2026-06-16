from app.core.config import Settings
from app.features.receive_sms import PACK
from app.features.receive_sms.routes import _decode_message, _normalise_phone, _parse_sms_datetime


def test_receive_sms_pack_metadata_and_default_enabled():
    assert PACK.slug == "receive_sms"
    assert PACK.routers
    default_feature_packs = str(Settings.model_fields["feature_packs"].default).split(",")
    assert "receive_sms" in default_feature_packs


def test_receive_sms_helpers_decode_and_normalise():
    assert _decode_message("SGVsbG8gV29ybGQ=") == "Hello World"
    assert _normalise_phone("+61 (400) 123-456") == "61400123456"
    parsed, day = _parse_sms_datetime("2026-06-16", "14:30")
    assert parsed.isoformat() == "2026-06-16T14:30:00+00:00"
    assert day.isoformat() == "2026-06-16"
