"""Month-end billing tasks default to the next free one-minute slot."""

from __future__ import annotations

from app.services import scheduled_task_slots as slots


def _task(command: str, cron: str) -> dict[str, str]:
    return {"command": command, "cron": cron}


def test_defaults_to_base_cron_without_existing_tasks():
    assert slots.next_month_end_cron("generate_invoice", []) == "1 0 L * *"
    assert slots.next_month_end_cron("sync_to_xero", []) == "1 15 L * *"
    assert slots.next_month_end_cron("sync_to_xero_auto_send", []) == "1 16 L * *"


def test_follows_latest_task_for_same_command():
    tasks = [
        _task("generate_invoice", "1 18 L * *"),
        _task("generate_invoice", "3 18 L * *"),
        _task("generate_invoice", "2 18 L * *"),
    ]
    assert slots.next_month_end_cron("generate_invoice", tasks) == "4 18 L * *"


def test_rolls_into_next_hour():
    tasks = [_task("sync_to_xero", "59 15 L * *")]
    assert slots.next_month_end_cron("sync_to_xero", tasks) == "0 16 L * *"


def test_skips_slots_taken_by_other_month_end_tasks():
    tasks = [
        _task("sync_to_xero", "59 15 L * *"),
        _task("sync_to_xero_auto_send", "0 16 L * *"),
        _task("sync_to_xero_auto_send", "1 16 L * *"),
    ]
    assert slots.next_month_end_cron("sync_to_xero", tasks) == "2 16 L * *"
    assert slots.next_month_end_cron("sync_to_xero_auto_send", tasks) == "2 16 L * *"


def test_ignores_crons_that_do_not_follow_month_end_pattern():
    tasks = [
        _task("generate_invoice", "30 2 * * *"),
        _task("generate_invoice", "*/5 18 L * *"),
        _task("generate_invoice", None),
    ]
    assert slots.next_month_end_cron("generate_invoice", tasks) == "1 0 L * *"


def test_wraps_past_midnight():
    tasks = [_task("generate_invoice", "59 23 L * *")]
    assert slots.next_month_end_cron("generate_invoice", tasks) == "0 0 L * *"


def test_unbill_keeps_fixed_default_and_unknown_command_has_none():
    tasks = [_task("unbill_time_entries", "0 0 L * *")]
    assert slots.next_month_end_cron("unbill_time_entries", tasks) == "0 0 L * *"
    assert slots.next_month_end_cron("sync_staff", tasks) is None


def test_command_default_crons_covers_billing_commands():
    defaults = slots.command_default_crons([_task("generate_invoice", "3 18 L * *")])
    assert defaults == {
        "unbill_time_entries": "0 0 L * *",
        "generate_invoice": "4 18 L * *",
        "sync_to_xero": "1 15 L * *",
        "sync_to_xero_auto_send": "1 16 L * *",
    }


def test_allocate_consecutive_free_slots():
    tasks = [
        _task("generate_invoice", "3 18 L * *"),
        _task("sync_to_xero", "5 18 L * *"),
    ]
    assert slots.allocate_month_end_crons("generate_invoice", tasks, 3) == [
        "4 18 L * *",
        "6 18 L * *",
        "7 18 L * *",
    ]


def test_allocate_from_start_cron_skips_taken_slots():
    tasks = [_task("generate_invoice", "2 0 L * *")]
    assert slots.allocate_month_end_crons(
        "generate_invoice", tasks, 2, start_cron="1 0 L * *"
    ) == ["1 0 L * *", "3 0 L * *"]


def test_allocate_falls_back_for_other_commands_and_patterns():
    assert slots.allocate_month_end_crons("sync_staff", [], 2) is None
    assert slots.allocate_month_end_crons("generate_invoice", [], 2, start_cron="0 2 * * *") is None
