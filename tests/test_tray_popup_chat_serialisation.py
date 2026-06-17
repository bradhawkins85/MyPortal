from datetime import date, datetime
from decimal import Decimal

from starlette.responses import JSONResponse

from app.api.routes.tray import _serialise_popup_chat_value


def test_popup_chat_serialiser_handles_decimal_values() -> None:
    payload = {
        "room": {
            "id": Decimal("42"),
            "created_at": datetime(2026, 6, 17, 21, 39, 18),
        },
        "messages": [
            {
                "id": Decimal("7"),
                "score": Decimal("12.50"),
                "sent_on": date(2026, 6, 17),
            }
        ],
    }

    serialised = _serialise_popup_chat_value(payload)

    assert serialised == {
        "room": {"id": 42, "created_at": "2026-06-17T21:39:18"},
        "messages": [
            {"id": 7, "score": 12.5, "sent_on": "2026-06-17"},
        ],
    }
    JSONResponse(serialised)
