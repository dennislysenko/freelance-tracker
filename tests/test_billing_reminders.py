from datetime import datetime

from billing_reminders import (
    collect_due_reminders,
    mark_reminder_sent,
    reminder_key,
    reminder_notification,
    resolve_day_of_month,
)


def _sample_reminder(**overrides):
    reminder = {
        "enabled": True,
        "project_name": "Acme Inc",
        "task": "invoice",
        "weekday": "friday",
        "time": "14:00",
    }
    reminder.update(overrides)
    return reminder


def test_collect_due_reminders_matches_weekday_and_time():
    reminder = _sample_reminder()

    due = collect_due_reminders(
        [reminder],
        now=datetime(2026, 4, 17, 14, 0),
        state={},
    )

    assert due == [reminder]


def test_collect_due_reminders_skips_before_scheduled_minute():
    due = collect_due_reminders(
        [_sample_reminder()],
        now=datetime(2026, 4, 17, 13, 59),
        state={},
    )

    assert due == []


def test_mark_reminder_sent_blocks_repeat_delivery_same_day():
    reminder = _sample_reminder()
    now = datetime(2026, 4, 17, 14, 2)
    state = mark_reminder_sent(reminder, delivered_on=now.date(), state={})

    due = collect_due_reminders([reminder], now=now, state=state)

    assert due == []
    assert state[reminder_key(reminder)] == "2026-04-17"


def test_reminder_notification_formats_invoice_copy():
    title, subtitle, message = reminder_notification(_sample_reminder())

    assert title == "Billing reminder"
    assert subtitle == "Acme Inc"
    assert message == "Invoice for Acme Inc."


def test_resolve_day_of_month_positive_maps_directly():
    assert resolve_day_of_month(15, 2026, 4) == 15
    # 28 is the maximum positive value — always valid, including February
    assert resolve_day_of_month(28, 2026, 2) == 28
    assert resolve_day_of_month(28, 2024, 2) == 28


def test_resolve_day_of_month_negative_counts_from_end():
    # April 2026 has 30 days
    assert resolve_day_of_month(-1, 2026, 4) == 30
    assert resolve_day_of_month(-2, 2026, 4) == 29
    assert resolve_day_of_month(-3, 2026, 4) == 28
    # February 2024 (leap) — last day is 29
    assert resolve_day_of_month(-1, 2024, 2) == 29


def test_resolve_day_of_month_rejects_out_of_range():
    assert resolve_day_of_month(0, 2026, 4) is None
    assert resolve_day_of_month(-4, 2026, 4) is None
    assert resolve_day_of_month(32, 2026, 4) is None
    assert resolve_day_of_month("15", 2026, 4) is None


def test_collect_due_reminders_fires_on_nth_day_of_month():
    reminder = _sample_reminder(weekday=None, day_of_month=15, time="09:00")
    # April 15, 2026 is a Wednesday — weekday doesn't matter for monthly mode
    due = collect_due_reminders(
        [reminder],
        now=datetime(2026, 4, 15, 9, 0),
        state={},
    )
    assert due == [reminder]


def test_collect_due_reminders_skips_wrong_day_of_month():
    reminder = _sample_reminder(weekday=None, day_of_month=15, time="09:00")
    due = collect_due_reminders(
        [reminder],
        now=datetime(2026, 4, 14, 9, 0),
        state={},
    )
    assert due == []


def test_collect_due_reminders_fires_on_last_day_of_month():
    reminder = _sample_reminder(weekday=None, day_of_month=-1, time="08:00")
    # April 30 is the last day of April 2026
    due = collect_due_reminders(
        [reminder],
        now=datetime(2026, 4, 30, 8, 0),
        state={},
    )
    assert due == [reminder]


def test_collect_due_reminders_fires_on_second_to_last_day():
    reminder = _sample_reminder(weekday=None, day_of_month=-2, time="08:00")
    due = collect_due_reminders(
        [reminder],
        now=datetime(2026, 4, 29, 8, 0),
        state={},
    )
    assert due == [reminder]


def test_reminder_key_distinguishes_monthly_from_weekly():
    weekly = _sample_reminder()
    monthly = _sample_reminder(weekday=None, day_of_month=15)
    assert reminder_key(weekly) != reminder_key(monthly)


def test_day_of_month_dedup_blocks_same_day_redelivery():
    reminder = _sample_reminder(weekday=None, day_of_month=15, time="09:00")
    now = datetime(2026, 4, 15, 9, 5)
    state = mark_reminder_sent(reminder, delivered_on=now.date(), state={})

    due = collect_due_reminders([reminder], now=now, state=state)
    assert due == []
