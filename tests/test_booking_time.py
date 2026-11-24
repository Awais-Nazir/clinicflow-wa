"""Relative booking time interpretation must be explicit and repeatable."""

from datetime import date, time

import pytest

from clinicflow.booking_time import (
    BookingDateError,
    booking_period,
    parse_booking_clock,
    parse_booking_date,
)


def test_relative_date_uses_supplied_clinic_day_and_clarifies_kal():
    today = date(2026, 11, 25)
    assert parse_booking_date("Thursday evening book karna hai", today=today) == date(2026, 11, 26)
    assert parse_booking_date("next Thursday", today=today) == date(2026, 12, 3)
    assert parse_booking_date("yesterday", today=today) == date(2026, 11, 24)
    with pytest.raises(BookingDateError, match="ambiguous"):
        parse_booking_date("kal book karna hai", today=today)


def test_booking_clock_understands_explicit_local_time_and_evening():
    assert parse_booking_clock("shaam 6 baje") == time(18)
    assert parse_booking_clock("جمعرات ۱۷:۳۰") == time(17, 30)
    assert parse_booking_clock("at 5:15 p.m.") == time(17, 15)
    assert booking_period("Thursday evening") == "evening"
    with pytest.raises(BookingDateError, match="ambiguous"):
        parse_booking_clock("6 baje")
