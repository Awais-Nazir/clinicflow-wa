"""Interpret explicit dates and common clinic booking phrases."""

import re
from datetime import date, datetime, time, timedelta
from unicodedata import decimal

WEEKDAYS = (
    ("monday", "peer", "pir", "پیر"),
    ("tuesday", "mangal", "منگل"),
    ("wednesday", "budh", "بدھ"),
    ("thursday", "jumeraat", "jumerat", "جمعرات"),
    ("friday", "jumma", "جمعہ"),
    ("saturday", "hafta", "ہفتہ"),
    ("sunday", "itwar", "اتوار"),
)
DATE_PATTERN = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
CLOCK_PATTERN = re.compile(
    r"(?<![\w:-])(?P<hour>\d{1,2})(?:(?::(?P<minute>\d{2}))(?P<clock_meridiem>\s*[ap]\.?m\.?)?"
    r"|\s*(?P<meridiem>[ap]\.?m\.?|baje|بجے))(?![\w:-])"
)
PERIODS = {
    "morning": ("morning", "subah", "صبح"),
    "afternoon": ("afternoon", "dopahar", "دوپہر"),
    "evening": ("evening", "shaam", "sham", "شام"),
}


class BookingDateError(ValueError):
    """The stated date or time needs clarification."""


def normalize_booking_text(text: str) -> str:
    return " ".join(
        "".join(str(decimal(char)) if char.isdecimal() else char for char in text.casefold()).split()
    )


def parse_booking_date(question: str, *, today: date) -> date | None:
    text = normalize_booking_text(question)
    explicit = DATE_PATTERN.search(text)
    if explicit:
        try:
            return date.fromisoformat(explicit[0])
        except ValueError as error:
            raise BookingDateError("invalid calendar date") from error
    if _has_word(text, ("today", "aaj", "آج")):
        return today
    if _has_word(text, ("tomorrow",)):
        return today + timedelta(days=1)
    if _has_word(text, ("yesterday",)):
        return today - timedelta(days=1)
    for weekday, words in enumerate(WEEKDAYS):
        if _has_word(text, words):
            offset = (weekday - today.weekday()) % 7
            if _has_word(text, ("next",)):
                offset = 7 - today.weekday() + weekday
            return today + timedelta(days=offset)
    if _has_word(text, ("kal", "کل")):
        raise BookingDateError("ambiguous kal")
    return None


def booking_period(question: str) -> str | None:
    text = normalize_booking_text(question)
    return next((period for period, words in PERIODS.items() if _has_word(text, words)), None)


def parse_booking_clock(question: str) -> time | None:
    text = normalize_booking_text(question)
    match = CLOCK_PATTERN.search(text)
    if match is None:
        return None
    hour = int(match["hour"])
    minute = int(match["minute"] or 0)
    suffix = (match["clock_meridiem"] or match["meridiem"] or "").strip()
    if suffix.startswith(("a", "p")):
        if not 1 <= hour <= 12:
            raise BookingDateError("invalid am/pm time")
        hour = hour % 12 + (12 if suffix.startswith("p") else 0)
    elif suffix in {"baje", "بجے"}:
        period = booking_period(text)
        if period in {"evening", "afternoon"} and 1 <= hour < 12:
            hour += 12
        elif period is None and 1 <= hour <= 12:
            raise BookingDateError("ambiguous hour")
    try:
        return time(hour, minute)
    except ValueError as error:
        raise BookingDateError("invalid clock time") from error


def fits_booking_period(value: datetime, period: str | None) -> bool:
    if period == "morning":
        return value.hour < 12
    if period == "afternoon":
        return 12 <= value.hour < 17
    if period == "evening":
        return value.hour >= 17
    return True


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) for word in words)
