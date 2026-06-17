import asyncio
from datetime import datetime, timezone

from app.core.config import Settings
from app.features.receive_sms import PACK
from app.features.receive_sms import routes as receive_sms_routes
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


def test_receive_sms_created_ticket_refreshes_ai(monkeypatch):
    async def run_test():
        calls: list[tuple[str, int]] = []

        async def fake_find_sms_ticket(*_args, **_kwargs):
            return None

        monkeypatch.setattr(receive_sms_routes, "_find_sms_ticket", fake_find_sms_ticket)

        async def fake_find_contact(_phone):
            return {"requester_id": 11, "requester_staff_id": None, "company_id": 22}

        async def fake_resolve_status(_status):
            return "new"

        async def fake_create_ticket(**_kwargs):
            return {"id": 123, "requester_id": 11, "status": "new"}

        async def fake_execute(*_args, **_kwargs):
            return None

        async def fake_summary(ticket_id):
            calls.append(("summary", ticket_id))

        async def fake_tags(ticket_id):
            calls.append(("tags", ticket_id))

        async def fake_emit(*_args, **_kwargs):
            return None

        monkeypatch.setattr(receive_sms_routes, "_find_contact", fake_find_contact)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "resolve_status_or_default", fake_resolve_status)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "create_ticket", fake_create_ticket)
        monkeypatch.setattr(receive_sms_routes.db, "execute", fake_execute)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "refresh_ticket_ai_summary", fake_summary)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "refresh_ticket_ai_tags", fake_tags)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "emit_ticket_updated_event", fake_emit)

        payload = receive_sms_routes.ReceiveSMSPayload(
            type="SMSIn",
            **{"from": "+61 400 123 456"},
            name="Customer",
            message="UHJpbnRlciBpcyBqYW1tZWQ=",
            date="2026-06-16",
            time="14:30",
        )

        result = await receive_sms_routes.receive_sms(payload, request=None, api_key_record={"id": 7})

        assert result["status"] == "created"
        assert result["ticket_id"] == 123
        assert calls == [("summary", 123), ("tags", 123)]

    asyncio.run(run_test())


def test_receive_sms_existing_ticket_reply_refreshes_ai(monkeypatch):
    async def run_test():
        calls: list[tuple[str, int]] = []

        async def fake_find_sms_ticket(*_args, **_kwargs):
            return {"id": 456, "requester_id": 11, "status": "open"}

        async def fake_create_reply(**kwargs):
            calls.append(("reply", int(kwargs["ticket_id"])))
            return {"id": 99}

        async def fake_summary(ticket_id):
            calls.append(("summary", ticket_id))

        async def fake_tags(ticket_id):
            calls.append(("tags", ticket_id))

        async def fake_emit(*_args, **_kwargs):
            return None

        monkeypatch.setattr(receive_sms_routes, "_find_sms_ticket", fake_find_sms_ticket)
        monkeypatch.setattr(receive_sms_routes.tickets_repo, "create_reply", fake_create_reply)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "refresh_ticket_ai_summary", fake_summary)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "refresh_ticket_ai_tags", fake_tags)
        monkeypatch.setattr(receive_sms_routes.tickets_service, "emit_ticket_updated_event", fake_emit)

        payload = receive_sms_routes.ReceiveSMSPayload(
            type="SMSIn",
            **{"from": "+61 400 123 456"},
            message="QW55IHVwZGF0ZT8=",
            date="2026-06-16",
            time="15:00",
        )

        result = await receive_sms_routes.receive_sms(payload, request=None, api_key_record={"id": 7})

        assert result["status"] == "appended"
        assert result["ticket_id"] == 456
        assert calls == [("reply", 456), ("summary", 456), ("tags", 456)]

    asyncio.run(run_test())
