"""Tenant-scoped access to structured clinic facts."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from clinicflow.database import session_scope
from clinicflow.models import ClinicProfile, ClinicService


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
