import asyncio
from datetime import datetime, timedelta, timezone

from app.services import dynamic_variables, value_templates


def test_render_value_async_populates_active_assets(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_count_active_assets(*, company_id=None, since=None):
        calls.append({"company_id": company_id, "since": since})
        return 5 if len(calls) == 1 else 2

    monkeypatch.setattr(
        dynamic_variables.assets_repo,
        "count_active_assets",
        fake_count_active_assets,
    )

    fixed_now = datetime(2024, 4, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dynamic_variables, "_utcnow", lambda: fixed_now)

    payload = {
        "monthly": "{{ ACTIVE_ASSETS }}",
        "custom": "{{ ACTIVE_ASSETS:7 }}",
    }
    context = {"ticket": {"company_id": 42}}

    result = asyncio.run(value_templates.render_value_async(payload, context))

    assert result["monthly"] == "5"
    assert result["custom"] == "2"
    assert len(calls) == 2

    first_call = calls[0]
    second_call = calls[1]

    assert first_call["company_id"] == 42
    assert first_call["since"] == datetime(2024, 4, 1, tzinfo=timezone.utc)

    assert second_call["company_id"] == 42
    assert second_call["since"] == fixed_now - timedelta(days=7)
