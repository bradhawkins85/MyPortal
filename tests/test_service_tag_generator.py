from __future__ import annotations

from typing import Any

import pytest

from app.services import modules as modules_service
from app.services import tag_generator


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _event_result(response_body: Any, status: str = "succeeded") -> dict[str, Any]:
    """Build the same result shape returned by synchronous module dispatch."""

    return modules_service._build_event_result(
        {
            "id": 42,
            "status": status,
            "response_status": 200,
            "response_body": response_body,
        }
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response_body", "expected"),
    [
        (
            {"response": "Infrastructure, Cloud Services, monitoring."},
            ["infrastructure", "cloud services", "monitoring"],
        ),
        ("Security, Identity Access", ["security", "identity access"]),
    ],
)
async def test_succeeded_module_result_generates_normalised_tags(
    monkeypatch: pytest.MonkeyPatch,
    response_body: Any,
    expected: list[str],
) -> None:
    result = _event_result(response_body)

    async def fake_trigger(*args: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["background"] is False
        return result

    monkeypatch.setattr(tag_generator.modules_service, "trigger_module", fake_trigger)

    assert await tag_generator.generate_tags_for_service("Managed service") == expected


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["failed", "skipped", "queued"])
async def test_non_success_statuses_do_not_generate_tags(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    async def fake_trigger(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _event_result({"response": "must, not, be, stored"}, status=status)

    monkeypatch.setattr(tag_generator.modules_service, "trigger_module", fake_trigger)

    assert await tag_generator.generate_tags_for_service("Managed service") == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        {},
        {"status": "succeeded"},
        {"status": "succeeded", "response": 123},
        {"status": "success", "response": "legacy, status"},
        {"status": "completed", "response": "legacy, status"},
    ],
)
async def test_malformed_and_noncanonical_results_do_not_generate_tags(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]
) -> None:
    async def fake_trigger(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return result

    monkeypatch.setattr(tag_generator.modules_service, "trigger_module", fake_trigger)

    assert await tag_generator.generate_tags_for_service("Managed service") == []


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "succeeded"}, True),
        ({"status": " SUCCEEDED "}, True),
        ({"status": "failed"}, False),
        ({"status": "skipped"}, False),
        ({"status": "queued"}, False),
        ({"status": "success"}, False),
        (None, False),
    ],
)
def test_module_result_succeeded_uses_canonical_status(
    result: dict[str, Any] | None, expected: bool
) -> None:
    assert modules_service.module_result_succeeded(result) is expected
