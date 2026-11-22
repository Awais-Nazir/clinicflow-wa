"""Tenant-scoped access to structured clinic facts."""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from sqlalchemy import select

from clinicflow.database import session_scope
from clinicflow.models import (
    ClinicOpeningHours,
    ClinicProfile,
    ClinicService,
    Doctor,
)

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


@dataclass(frozen=True)
class ClinicInfo:
    description: str
    address: str
    city: str
    country: str
    contact_phone: str
    email: str | None


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    description: str
    price_amount: Decimal | None
    currency: str
    duration_minutes: int | None


@dataclass(frozen=True)
class OpeningHoursInfo:
    weekday: int
    day_name: str
    is_closed: bool
    opens_at: time | None
    closes_at: time | None


@dataclass(frozen=True)
class DoctorInfo:
    name: str
    specialty: str
    bio: str


def get_clinic_info(tenant_id: int) -> ClinicInfo | None:
    """Return public clinic details only for the supplied tenant."""

    statement = select(
        ClinicProfile.description,
        ClinicProfile.address,
        ClinicProfile.city,
        ClinicProfile.country,
        ClinicProfile.contact_phone,
        ClinicProfile.email,
    ).where(ClinicProfile.tenant_id == tenant_id)

    with session_scope() as session:
        row = session.execute(statement).one_or_none()

    if row is None:
        return None

    return ClinicInfo(
        description=row.description,
        address=row.address,
        city=row.city,
        country=row.country,
        contact_phone=row.contact_phone,
        email=row.email,
    )


def get_services(tenant_id: int) -> list[ServiceInfo]:
    """Return active services scoped by an application-supplied tenant ID."""

    statement = (
        select(
            ClinicService.name,
            ClinicService.description,
            ClinicService.price_amount,
            ClinicService.currency,
            ClinicService.duration_minutes,
        )
        .where(
            ClinicService.tenant_id == tenant_id,
            ClinicService.is_active.is_(True),
        )
        .order_by(ClinicService.name)
    )

    with session_scope() as session:
        rows = session.execute(statement).all()

    return [
        ServiceInfo(
            name=row.name,
            description=row.description,
            price_amount=row.price_amount,
            currency=row.currency,
            duration_minutes=row.duration_minutes,
        )
        for row in rows
    ]


def get_opening_hours(tenant_id: int) -> list[OpeningHoursInfo]:
    """Return the regular weekly schedule for the supplied tenant."""

    statement = (
        select(
            ClinicOpeningHours.weekday,
            ClinicOpeningHours.is_closed,
            ClinicOpeningHours.opens_at,
            ClinicOpeningHours.closes_at,
        )
        .where(ClinicOpeningHours.tenant_id == tenant_id)
        .order_by(ClinicOpeningHours.weekday)
    )

    with session_scope() as session:
        rows = session.execute(statement).all()

    return [
        OpeningHoursInfo(
            weekday=row.weekday,
            day_name=WEEKDAY_NAMES[row.weekday],
            is_closed=row.is_closed,
            opens_at=row.opens_at,
            closes_at=row.closes_at,
        )
        for row in rows
    ]


def get_doctors(tenant_id: int) -> list[DoctorInfo]:
    """Return active doctors scoped by an application-supplied tenant ID."""

    statement = (
        select(Doctor.name, Doctor.specialty, Doctor.bio)
        .where(Doctor.tenant_id == tenant_id, Doctor.is_active.is_(True))
        .order_by(Doctor.name)
    )

    with session_scope() as session:
        rows = session.execute(statement).all()

    return [
        DoctorInfo(name=row.name, specialty=row.specialty, bio=row.bio)
        for row in rows
    ]
