"""Locale and date-aware labels must not change the stored IANA time zone."""

from datetime import date

import pytest

from app.ui.proactive.pages import zone_label


@pytest.mark.parametrize(
    "zone,winter,summer",
    [
        ("Europe/London", "UTC+0", "UTC+1"),
        ("America/New_York", "UTC−5", "UTC−4"),
        ("America/Los_Angeles", "UTC−8", "UTC−7"),
        ("Asia/Kolkata", "UTC+5:30", "UTC+5:30"),
    ],
)
def test_zone_offset_tracks_selected_date_and_daylight_saving(zone, winter, summer):
    assert f"({winter})" in zone_label(zone, date(2026, 1, 15), "en")
    assert f"({summer})" in zone_label(zone, date(2026, 7, 15), "en")


def test_zone_country_label_follows_page_language():
    assert "英国" in zone_label("Europe/London", date(2026, 1, 15), "zh")
    assert "UK" in zone_label("Europe/London", date(2026, 1, 15), "en")


def test_calendar_callback_updates_date_and_draft_but_rejects_invalid_values(monkeypatch):
    from app.ui import localized_date

    state = {"date": date(2026, 11, 9), "bridge": {"value": "2026-11-18"}}
    monkeypatch.setattr(localized_date.st, "session_state", state)
    saved = []
    localized_date.apply_date("date", "bridge", lambda: saved.append(state["date"]))
    assert saved == [date(2026, 11, 18)]
    state["bridge"]["value"] = "2026-02-30"
    localized_date.apply_date("date", "bridge", lambda: saved.append(state["date"]))
    assert state["date"] == date(2026, 11, 18) and len(saved) == 1
