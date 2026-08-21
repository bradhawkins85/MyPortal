"""Contract tests for Voice Monitor subscription entitlements and billing."""
import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.services import voice_monitor_billing as billing


def subscription(**overrides):
    value = {"id": "sub-1", "customer_id": 7, "category_name": "Voice Monitor",
             "status": "active", "start_date": date(2026, 1, 1),
             "end_date": date(2026, 12, 31), "quantity": 2,
             "unit_price": Decimal("12.00"), "has_pending_change": 0}
    value.update(overrides)
    return value


def test_contract_explicitly_combines_number_included_and_metered_billing():
    shown = billing.contract_display(subscription())
    assert shown["billing_unit"] == "monitored_number"
    assert shown["included_attempts"] == 200
    assert shown["attempt_overage_price"] == Decimal("0.05")
    assert shown["connected_minute_price"] == Decimal("0.02")
    assert shown["transcription_surcharge"] == Decimal("0.10")


@pytest.mark.parametrize("change", [
    {"status": "pending_renewal"}, {"status": "expired"},
    {"status": "canceled"}, {"has_pending_change": 1},
    {"start_date": date(2026, 9, 1)}, {"end_date": date(2025, 12, 31)},
])
def test_non_serviceable_subscription_cannot_queue(change):
    with patch.object(billing.db, "fetch_one", AsyncMock(return_value=subscription(**change))):
        with pytest.raises(ValueError):
            asyncio.run(billing.get_entitlement("sub-1", company_id=7, today=date(2026, 8, 21)))


def test_enabled_numbers_cannot_exceed_purchased_quantity():
    with patch.object(billing, "get_entitlement", AsyncMock(return_value=subscription())), \
         patch.object(billing.db, "fetch_one", AsyncMock(return_value={"used": 2})):
        with pytest.raises(OverflowError):
            asyncio.run(billing.assert_endpoint_capacity("sub-1", company_id=7))


def test_usage_ledger_deduplicates_retries_and_callbacks_and_failed_calls_have_no_minutes():
    attempt = {"id": 9, "company_id": 7, "endpoint_id": 3,
               "subscription_id": "sub-1", "outcome_status": "failed",
               "transcription_enabled": 1}
    execute = AsyncMock(side_effect=[1, 0])
    with patch.object(billing.db, "fetch_one", AsyncMock(return_value=attempt)), \
         patch.object(billing.db, "execute_rowcount", execute), \
         patch.object(billing.db, "is_sqlite", return_value=False):
        assert asyncio.run(billing.record_attempt_usage(9, duration_seconds=125, transcription_completed=True))
        assert not asyncio.run(billing.record_attempt_usage(9, duration_seconds=125, transcription_completed=True))
    assert execute.call_args_list[0].args[1][5:7] == (0, 0)


def test_transcription_only_charged_when_enabled_and_completed():
    attempt = {"id": 10, "company_id": 7, "endpoint_id": 3,
               "subscription_id": "sub-1", "outcome_status": "passed",
               "transcription_enabled": 0}
    execute = AsyncMock(return_value=1)
    with patch.object(billing.db, "fetch_one", AsyncMock(return_value=attempt)), \
         patch.object(billing.db, "execute_rowcount", execute), \
         patch.object(billing.db, "is_sqlite", return_value=False):
        asyncio.run(billing.record_attempt_usage(10, duration_seconds=61, transcription_completed=True))
    assert execute.call_args.args[1][5:7] == (2, 0)


def test_usage_invoice_lines_are_ready_for_existing_invoice_export():
    fetch = AsyncMock(side_effect=[{"quantity": 1}, {"attempts": 103, "minutes": 8, "transcriptions": 2}])
    with patch.object(billing.db, "fetch_one", fetch):
        lines = asyncio.run(billing.usage_invoice_lines("sub-1", date(2026, 7, 1), date(2026, 8, 1)))
    assert lines == [
        {"description": "Voice Monitor attempt overage", "quantity": 3, "unit_price": Decimal("0.05"), "amount": Decimal("0.15")},
        {"description": "Voice Monitor connected minutes", "quantity": 8, "unit_price": Decimal("0.02"), "amount": Decimal("0.16")},
        {"description": "Voice Monitor transcription surcharge", "quantity": 2, "unit_price": Decimal("0.10"), "amount": Decimal("0.20")},
    ]
