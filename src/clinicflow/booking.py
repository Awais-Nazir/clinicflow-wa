"""Tenant-scoped availability, booking and appointment changes."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from clinicflow.database import session_scope
from clinicflow.models import (
    Appointment,
    ClinicOpeningHours,
    ClinicService,
    Conversation,
    Provider,
    ProviderHours,
    ProviderService,
    Tenant,
)
from clinicflow.slots import Slot, available_slots


@dataclass(frozen=True, slots=True)
class BookingResult:
    status: Literal["booked", "rescheduled", "cancelled", "slot_unavailable", "not_found"]
    appointment_id: int | None = None
    alternatives: tuple[Slot, ...] = ()


@dataclass(frozen=True, slots=True)
class Offering:
    duration: int
    timezone_name: str


def list_booking_slots(
    tenant_id: int, provider_id: int, service_id: int, day: date, *, now: datetime
) -> list[Slot]:
    with session_scope() as session:
        return list_slots_in_session(session, tenant_id, provider_id, service_id, day, now)


def list_slots_in_session(
    session: Session, tenant_id: int, provider_id: int, service_id: int,
    day: date, now: datetime, *, exclude_appointment_id: int | None = None,
) -> list[Slot]:
    offering = _get_offering(session, tenant_id, provider_id, service_id)
    return (
        _slots_for_day(session, tenant_id, provider_id, day, offering, now, exclude_appointment_id)
        if offering else []
    )


def book_appointment(
    *, tenant_id: int, customer_wa_id: str, provider_id: int, service_id: int,
    starts_at: datetime, customer_name: str, now: datetime,
) -> BookingResult:
    with session_scope() as session:
        return book_in_session(
            session, tenant_id=tenant_id, customer_wa_id=customer_wa_id,
            provider_id=provider_id, service_id=service_id, starts_at=starts_at,
            customer_name=customer_name, now=now,
        )


def book_in_session(
    session: Session, *, tenant_id: int, customer_wa_id: str, provider_id: int,
    service_id: int, starts_at: datetime, customer_name: str, now: datetime,
) -> BookingResult:
    name = customer_name.strip()
    if not name or len(name) > 160:
        raise ValueError("customer name must be between 1 and 160 characters")
    conversation = _owned_conversation(session, tenant_id, customer_wa_id)
    if conversation is None:
        return BookingResult("not_found")
    selected = _select_slot(session, tenant_id, provider_id, service_id, starts_at, now)
    if isinstance(selected, BookingResult):
        return selected
    appointment = Appointment(
        tenant_id=tenant_id, conversation_id=conversation.id, provider_id=provider_id,
        service_id=service_id, customer_name=name, starts_at=selected.starts_at,
        ends_at=selected.ends_at,
    )
    session.add(appointment)
    session.flush()
    return BookingResult("booked", appointment.id)


def reschedule_appointment(
    *, tenant_id: int, customer_wa_id: str, appointment_id: int,
    starts_at: datetime, now: datetime,
) -> BookingResult:
    with session_scope() as session:
        return reschedule_in_session(
            session, tenant_id=tenant_id, customer_wa_id=customer_wa_id,
            appointment_id=appointment_id, starts_at=starts_at, now=now,
        )


def reschedule_in_session(
    session: Session, *, tenant_id: int, customer_wa_id: str,
    appointment_id: int, starts_at: datetime, now: datetime,
) -> BookingResult:
    appointment = _owned_appointment(session, tenant_id, customer_wa_id, appointment_id, now)
    if appointment is None:
        return BookingResult("not_found")
    selected = _select_slot(
        session, tenant_id, appointment.provider_id, appointment.service_id,
        starts_at, now, exclude_appointment_id=appointment.id,
    )
    if isinstance(selected, BookingResult):
        return selected
    appointment.starts_at = selected.starts_at
    appointment.ends_at = selected.ends_at
    return BookingResult("rescheduled", appointment.id)


def cancel_appointment(
    *, tenant_id: int, customer_wa_id: str, appointment_id: int, now: datetime,
) -> BookingResult:
    with session_scope() as session:
        return cancel_in_session(
            session, tenant_id=tenant_id, customer_wa_id=customer_wa_id,
            appointment_id=appointment_id, now=now,
        )


def cancel_in_session(
    session: Session, *, tenant_id: int, customer_wa_id: str,
    appointment_id: int, now: datetime,
) -> BookingResult:
    appointment = _owned_appointment(session, tenant_id, customer_wa_id, appointment_id, now)
    if appointment is None:
        return BookingResult("not_found")
    # Serialize freeing the slot with other bookings for this provider.
    session.scalar(
        select(Provider.id).where(
            Provider.id == appointment.provider_id, Provider.tenant_id == tenant_id
        ).with_for_update()
    )
    appointment.status = "cancelled"
    return BookingResult("cancelled", appointment.id)


def _owned_conversation(
    session: Session, tenant_id: int, customer_wa_id: str
) -> Conversation | None:
    return session.scalar(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.customer_wa_id == customer_wa_id,
        ).with_for_update()
    )


def _owned_appointment(
    session: Session, tenant_id: int, customer_wa_id: str,
    appointment_id: int, now: datetime,
) -> Appointment | None:
    if now.utcoffset() is None:
        raise ValueError("now must include a timezone")
    conversation = _owned_conversation(session, tenant_id, customer_wa_id)
    if conversation is None:
        return None
    return session.scalar(select(Appointment).where(
        Appointment.id == appointment_id,
        Appointment.tenant_id == tenant_id,
        Appointment.conversation_id == conversation.id,
        Appointment.status == "booked",
        Appointment.starts_at > now,
    ))


def _select_slot(
    session: Session, tenant_id: int, provider_id: int, service_id: int,
    starts_at: datetime, now: datetime, *, exclude_appointment_id: int | None = None,
) -> Slot | BookingResult:
    if starts_at.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("booking times must include a timezone")
    provider = session.scalar(select(Provider.id).where(
        Provider.id == provider_id, Provider.tenant_id == tenant_id,
    ).with_for_update())
    if provider is None:
        return BookingResult("not_found")
    offering = _get_offering(session, tenant_id, provider_id, service_id)
    if offering is None:
        return BookingResult("not_found")
    day = starts_at.astimezone(ZoneInfo(offering.timezone_name)).date()
    slots = _slots_for_day(
        session, tenant_id, provider_id, day, offering, now, exclude_appointment_id,
    )
    for slot in slots:
        if slot.starts_at == starts_at.astimezone(timezone.utc):
            return slot
    slots.sort(key=lambda slot: abs(slot.starts_at - starts_at))
    return BookingResult("slot_unavailable", alternatives=tuple(slots[:3]))


def _get_offering(
    session: Session, tenant_id: int, provider_id: int, service_id: int,
) -> Offering | None:
    row = session.execute(
        select(ClinicService.duration_minutes, Tenant.timezone)
        .select_from(ProviderService)
        .join(Provider, and_(
            Provider.id == ProviderService.provider_id,
            Provider.tenant_id == ProviderService.tenant_id,
        ))
        .join(ClinicService, and_(
            ClinicService.id == ProviderService.service_id,
            ClinicService.tenant_id == ProviderService.tenant_id,
        ))
        .join(Tenant, Tenant.id == ProviderService.tenant_id)
        .where(
            ProviderService.tenant_id == tenant_id,
            ProviderService.provider_id == provider_id,
            ProviderService.service_id == service_id,
            Provider.is_active.is_(True),
            ClinicService.is_active.is_(True),
            ClinicService.duration_minutes > 0,
        )
    ).one_or_none()
    return Offering(row.duration_minutes, row.timezone) if row is not None else None


def _slots_for_day(
    session: Session, tenant_id: int, provider_id: int, day: date, offering: Offering,
    now: datetime, exclude_appointment_id: int | None = None,
) -> list[Slot]:
    window = session.execute(
        select(
            ProviderHours.opens_at, ProviderHours.closes_at,
            ClinicOpeningHours.opens_at, ClinicOpeningHours.closes_at,
        )
        .select_from(ProviderHours)
        .join(ClinicOpeningHours, and_(
            ClinicOpeningHours.tenant_id == ProviderHours.tenant_id,
            ClinicOpeningHours.weekday == ProviderHours.weekday,
        ))
        .where(
            ProviderHours.tenant_id == tenant_id,
            ProviderHours.provider_id == provider_id,
            ProviderHours.weekday == day.weekday(),
            ClinicOpeningHours.is_closed.is_(False),
        )
    ).one_or_none()
    if window is None:
        return []
    opens_at = max(window[0], window[2])
    closes_at = min(window[1], window[3])
    if opens_at >= closes_at:
        return []
    zone = ZoneInfo(offering.timezone_name)
    day_start = datetime.combine(day, time.min, zone)
    day_end = datetime.combine(day + timedelta(days=1), time.min, zone)
    busy = select(Appointment.starts_at, Appointment.ends_at).where(
        Appointment.tenant_id == tenant_id,
        Appointment.provider_id == provider_id,
        Appointment.status == "booked",
        Appointment.starts_at < day_end,
        Appointment.ends_at > day_start,
    )
    if exclude_appointment_id is not None:
        busy = busy.where(Appointment.id != exclude_appointment_id)
    return available_slots(
        day=day, working_hours=(opens_at, closes_at), duration_minutes=offering.duration,
        now=now, busy=(Slot(start, end) for start, end in session.execute(busy)),
        timezone_name=offering.timezone_name,
    )
