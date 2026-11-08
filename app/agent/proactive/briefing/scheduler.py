"""Timezone-aware schedule calculation for morning briefings."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def calculate_next_run(
    start_date: date,
    local_time: time,
    timezone_name: str,
    interval_days: int,
    now: datetime,
    *,
    inclusive: bool = True,
) -> datetime:
    """Return the next anchored occurrence as a UTC datetime.

    inclusive=True includes an occurrence exactly at 'now'.
    inclusive=False returns an occurrence strictly after 'now'.
    """
    if isinstance(interval_days, bool) or not isinstance(interval_days, int) or interval_days <= 0:
        raise ValueError("interval_days must be a positive integer")

    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")

    if not isinstance(start_date, date) or isinstance(start_date, datetime):
        raise ValueError("start_date must be a date")

    if not isinstance(local_time, time) or local_time.tzinfo is not None:
        raise ValueError("local_time must be a naive time")

    tz = ZoneInfo(timezone_name)
    local_now = now.astimezone(tz)

    elapsed_days = (local_now.date() - start_date).days
    periods = max(0, elapsed_days // interval_days)

    while True:
        occurrence_date = start_date + timedelta(days=periods * interval_days)
        occurrence_local = datetime.combine(occurrence_date, local_time, tzinfo=tz)
        occurrence_utc = occurrence_local.astimezone(timezone.utc)

        if occurrence_utc > now or (inclusive and occurrence_utc == now):
            return occurrence_utc

        periods += 1
