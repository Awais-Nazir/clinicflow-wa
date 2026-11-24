"""Pure slot calculation for a provider's local working day."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class Slot:
    starts_at: datetime
    ends_at: datetime

    def overlaps(self, other: "Slot") -> bool:
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at


def available_slots(
    *,
    day: date,
    working_hours: tuple[time, time] | None,
    duration_minutes: int,
    now: datetime,
    busy: Iterable[Slot] = (),
    timezone_name: str = "Asia/Karachi",
    step_minutes: int = 15,
) -> list[Slot]:
    """List future starts on the working window's grid, with UTC endpoints."""

    if duration_minutes <= 0 or step_minutes <= 0:
        raise ValueError("duration and step must be positive")
    if now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if working_hours is None:
        return []
    opens_at, closes_at = working_hours
    if opens_at.tzinfo or closes_at.tzinfo or opens_at >= closes_at:
        raise ValueError("working hours must be a same-day local interval")

    zone = ZoneInfo(timezone_name)
    start = _unique_utc_time(datetime.combine(day, opens_at), zone)
    close = _unique_utc_time(datetime.combine(day, closes_at), zone)
    if start is None or close is None:
        return []

    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)
    busy_slots = tuple(busy)
    slots: list[Slot] = []
    while start + duration <= close:
        slot = Slot(start, start + duration)
        if start > now and not any(slot.overlaps(block) for block in busy_slots):
            slots.append(slot)
        start += step
    return slots


def _unique_utc_time(local: datetime, zone: ZoneInfo) -> datetime | None:
    first = local.replace(tzinfo=zone, fold=0)
    second = local.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None
    return first.astimezone(timezone.utc)
