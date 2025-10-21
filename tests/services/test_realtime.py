import pytest

from app.services.realtime import RefreshNotifier


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StubWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.accepted = False
        self.sent_messages: list[dict] = []
        self._fail = fail

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self._fail:
            raise RuntimeError("socket closed")
        self.sent_messages.append(payload)


async def test_broadcast_refresh_delivers_payload() -> None:
    notifier = RefreshNotifier()
    websocket = StubWebSocket()

    await notifier.connect(websocket)  # type: ignore[arg-type]

    result = await notifier.broadcast_refresh(reason="test")

    assert websocket.accepted is True
    assert len(websocket.sent_messages) == 1
    payload = websocket.sent_messages[0]
    assert payload["type"] == "refresh"
    assert payload["reason"] == "test"
    assert "timestamp" in payload
    assert result.attempted == 1
    assert result.delivered == 1
    assert result.dropped == 0

    await notifier.disconnect(websocket)  # type: ignore[arg-type]


async def test_broadcast_refresh_cleans_up_failed_sockets() -> None:
    notifier = RefreshNotifier()
    failing_websocket = StubWebSocket(fail=True)

    await notifier.connect(failing_websocket)  # type: ignore[arg-type]

    result = await notifier.broadcast_refresh()

    assert result.attempted == 1
    assert result.delivered == 0
    assert result.dropped == 1

    # A second broadcast should no longer attempt to use the failed websocket.
    second = await notifier.broadcast_refresh()
    assert second.attempted == 0

