"""Tests for timezone-aware briefing schedule calculations."""

from datetime import date, datetime, time, timezone

import pytest

from app.agent.proactive.briefing.scheduler import calculate_next_run

START = date(2026, 11, 3)
MORNING = time(8, 0)
SGT = "Asia/Singapore"


@pytest.mark.parametrize(
    ("interval_days", "expected_day"),
    [
        (1, 4),
        (3, 6),
        (7, 10),
    ],
)
def test_schedule_intervals(interval_days, expected_day):
    """Schedules remain anchored to their original start date."""
    now = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)

    result = calculate_next_run(
        START, MORNING, SGT, interval_days, now
    )

    assert result == datetime(
        2026, 11, expected_day, 0, 0, tzinfo=timezone.utc
    )


def test_exact_execution_time_inclusive():
    """An occurrence at exactly now can be included."""
    now = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)

    result = calculate_next_run(
        START, MORNING, SGT, 3, now, inclusive=True
    )

    assert result == now


def test_exact_execution_time_exclusive():
    """Exclusive calculation moves to the next occurrence."""
    now = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)

    result = calculate_next_run(
        START, MORNING, SGT, 3, now, inclusive=False
    )

    assert result == datetime(
        2026, 11, 6, 0, 0, tzinfo=timezone.utc
    )


def test_delayed_execution_does_not_shift_schedule():
    """A late execution does not change the original schedule anchor."""
    delayed_now = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)

    result = calculate_next_run(
        START, MORNING, SGT, 3, delayed_now, inclusive=False
    )

    assert result == datetime(
        2026, 11, 9, 0, 0, tzinfo=timezone.utc
    )


def test_start_date_in_future():
    """A schedule cannot execute before its start date."""
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    result = calculate_next_run(
        START, MORNING, SGT, 3, now
    )

    assert result == datetime(
        2026, 11, 3, 0, 0, tzinfo=timezone.utc
    )


def test_invalid_interval_rejected():
    """Non-positive intervals are invalid."""
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="positive integer"):
        calculate_next_run(START, MORNING, SGT, 0, now)


def test_naive_now_rejected():
    """Schedule calculations require timezone-aware UTC."""
    naive_now = datetime(2026, 11, 3)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        calculate_next_run(START, MORNING, SGT, 3, naive_now)


def test_timezone_conversion():
    """Singapore 08:00 corresponds to midnight UTC."""
    now = datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)

    result = calculate_next_run(
        START, MORNING, SGT, 1, now
    )

    assert result == datetime(
        2026, 11, 3, 0, 0, tzinfo=timezone.utc
    )
