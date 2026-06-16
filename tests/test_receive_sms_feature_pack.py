from datetime import datetime, timezone

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


def test_receive_sms_datetime_defaults_to_current_utc_when_missing():
    current = datetime(2026, 6, 16, 12, 34, 56, tzinfo=timezone.utc)

    parsed, day = _parse_sms_datetime(None, None, now=current)

    assert parsed == current
    assert day.isoformat() == "2026-06-16"


def test_receive_sms_datetime_uses_current_date_when_only_time_sent():
    current = datetime(2026, 6, 16, 12, 34, 56, tzinfo=timezone.utc)

    parsed, day = _parse_sms_datetime(None, "14:30", now=current)

    assert parsed.isoformat() == "2026-06-16T14:30:00+00:00"
    assert day.isoformat() == "2026-06-16"
