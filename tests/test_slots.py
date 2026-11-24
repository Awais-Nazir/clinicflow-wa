"""Booking availability in clinic time with UTC storage."""

from datetime import date, datetime, time, timezone

import pytest

from clinicflow.slots import Slot, available_slots

DAY = date(2026, 11, 26)
WINDOW = time(10), time(12)


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 26, hour, minute, tzinfo=timezone.utc)


def test_slots_convert_karachi_times_and_skip_past_and_overlapping_starts():
    slots = available_slots(
        day=DAY,
        working_hours=WINDOW,
        duration_minutes=30,
        now=utc(5),
        busy=[Slot(utc(5, 45), utc(6, 15))],
    )
    assert slots == [
        Slot(utc(5, 15), utc(5, 45)),
        Slot(utc(6, 15), utc(6, 45)),
        Slot(utc(6, 30), utc(7)),
    ]


def test_closed_and_too_short_workdays_have_no_slots():
    for window in (None, (time(10), time(10, 20))):
        assert available_slots(
            day=DAY, working_hours=window, duration_minutes=30, now=utc(0)
        ) == []


def test_slots_require_an_aware_clock():
    with pytest.raises(ValueError, match="timezone"):
        available_slots(
            day=DAY,
            working_hours=WINDOW,
            duration_minutes=30,
            now=utc(0).replace(tzinfo=None),
        )
