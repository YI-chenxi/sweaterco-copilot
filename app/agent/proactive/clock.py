"""Shared application clock for the fixed Sprint 3 business snapshot."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.agent.settings import AgentSettings

DEMO_ZONE = ZoneInfo("Asia/Singapore")


def action_now(
    *,
    reference_date: date | None = None,
    live_now: datetime | None = None,
) -> datetime:
    """Return the snapshot date with the current Singapore time of day in UTC."""
    current = live_now or datetime.now(DEMO_ZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("live_now must be timezone-aware")
    local = current.astimezone(DEMO_ZONE)
    snapshot = datetime.combine(
        reference_date or AgentSettings().reference_date,
        local.timetz(),
    )
    return snapshot.astimezone(timezone.utc)
