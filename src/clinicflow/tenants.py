"""Tenant seeding and lookup by WhatsApp phone-number ID."""

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from clinicflow.database import session_scope
from clinicflow.models import (
    ClinicOpeningHours,
    ClinicProfile,
    ClinicService,
    Doctor,
    KnowledgeChunk,
    Tenant,
    WhatsAppPhoneNumber,
)

DEMO_TENANT_SLUG = "noor-clinic-demo"
DEMO_TENANT_NAME = "Noor Clinic (Demo)"
DEMO_TENANT_TIMEZONE = "Asia/Karachi"
DEMO_PROFILE = {
    "description": "A fictional Lahore clinic used to test ClinicFlow safely.",
    "address": "12 Demo Avenue, Gulberg",
    "city": "Lahore",
    "country": "Pakistan",
    "contact_phone": "+92 300 0000000",
    "email": "hello@noor-clinic.example",
}
DEMO_SERVICES = (
    {
        "name": "General consultation",
        "description": "A general in-clinic consultation.",
        "price_amount": Decimal("2500.00"),
        "currency": "PKR",
        "duration_minutes": 30,
    },
    {
        "name": "Hydra facial",
        "description": "A non-invasive facial treatment.",
        "price_amount": Decimal("8500.00"),
        "currency": "PKR",
        "duration_minutes": 60,
    },
    {
        "name": "Skin consultation",
        "description": "A consultation about skin concerns and treatment options.",
        "price_amount": Decimal("3000.00"),
        "currency": "PKR",
        "duration_minutes": 30,
    },
)
DEMO_OPENING_HOURS = tuple(
    {
        "weekday": weekday,
        "is_closed": False,
        "opens_at": time(10, 0),
        "closes_at": time(19, 0),
    }
    for weekday in range(6)
) + (
    {
        "weekday": 6,
        "is_closed": True,
        "opens_at": None,
        "closes_at": None,
    },
)
DEMO_DOCTORS = (
    {
        "name": "Dr. Hamza Ali",
        "specialty": "General Physician",
        "bio": "Provides general consultations at Noor Clinic (Demo).",
    },
    {
        "name": "Dr. Sara Ahmed",
        "specialty": "Dermatologist",
        "bio": "Provides skin consultations at Noor Clinic (Demo).",
    },
)
DEMO_KNOWLEDGE_CHUNKS = (
    {
        "source_key": "appointment-changes",
        "title": "Cancellation and rescheduling",
        "content": (
            "You can cancel or reschedule an appointment at least 4 hours "
            "before its start time. Later changes require clinic approval."
        ),
        "language": "en",
        "chunk_index": 0,
    },
    {
        "source_key": "payment-methods",
        "title": "Payment methods",
        "content": (
            "Noor Clinic (Demo) accepts cash and major debit or credit cards "
            "at reception. Online payment is not available."
        ),
        "language": "en",
        "chunk_index": 0,
    },
    {
        "source_key": "visitor-parking",
        "title": "Visitor parking",
        "content": (
            "Free visitor parking is available in the demo building basement. "
            "Ask reception for parking validation."
        ),
        "language": "en",
        "chunk_index": 0,
    },
    {
        "source_key": "medical-advice",
        "title": "Medical advice on WhatsApp",
        "content": (
            "The WhatsApp assistant provides clinic information only. It "
            "cannot diagnose conditions or recommend treatment; medical advice "
            "requires a consultation with a doctor."
        ),
        "language": "en",
        "chunk_index": 0,
    },
)


@dataclass(frozen=True)
class TenantContext:
    """The tenant identity safe to pass through application services."""

    id: int
    slug: str
    name: str
    timezone: str


def get_tenant_by_phone_number_id(
    phone_number_id: str,
) -> TenantContext | None:
    """Resolve the clinic receiving a WhatsApp message."""

    statement = (
        select(Tenant.id, Tenant.slug, Tenant.name, Tenant.timezone)
        .join(
            WhatsAppPhoneNumber,
            WhatsAppPhoneNumber.tenant_id == Tenant.id,
        )
        .where(WhatsAppPhoneNumber.phone_number_id == phone_number_id)
    )

    with session_scope() as session:
        row = session.execute(statement).one_or_none()

    if row is None:
        return None

    return TenantContext(
        id=row.id,
        slug=row.slug,
        name=row.name,
        timezone=row.timezone,
    )


def seed_demo_clinic(
    phone_number_id: str,
    *,
    display_phone_number: str | None = None,
) -> TenantContext:
    """Create or update the fake clinic and its WhatsApp number mapping."""

    normalized_phone_number_id = phone_number_id.strip()
    if not normalized_phone_number_id:
        raise ValueError("phone_number_id cannot be empty")

    tenant_statement = (
        insert(Tenant)
        .values(
            slug=DEMO_TENANT_SLUG,
            name=DEMO_TENANT_NAME,
            timezone=DEMO_TENANT_TIMEZONE,
        )
        .on_conflict_do_update(
            constraint="uq_tenants_slug",
            set_={
                "name": DEMO_TENANT_NAME,
                "timezone": DEMO_TENANT_TIMEZONE,
            },
        )
        .returning(Tenant.id)
    )

    with session_scope() as session:
        tenant_id = session.scalar(tenant_statement)
        if tenant_id is None:
            raise RuntimeError("Failed to seed demo tenant")

        number_values: dict[str, object] = {
            "tenant_id": tenant_id,
            "phone_number_id": normalized_phone_number_id,
            "display_phone_number": display_phone_number,
        }
        session.execute(
            insert(WhatsAppPhoneNumber)
            .values(**number_values)
            .on_conflict_do_update(
                constraint="uq_whatsapp_phone_numbers_phone_number_id",
                set_={
                    "tenant_id": tenant_id,
                    "display_phone_number": display_phone_number,
                },
            )
        )
        session.execute(
            insert(ClinicProfile)
            .values(tenant_id=tenant_id, **DEMO_PROFILE)
            .on_conflict_do_update(
                index_elements=[ClinicProfile.tenant_id],
                set_=DEMO_PROFILE,
            )
        )
        for service in DEMO_SERVICES:
            session.execute(
                insert(ClinicService)
                .values(tenant_id=tenant_id, **service)
                .on_conflict_do_update(
                    constraint="uq_clinic_services_tenant_name",
                    set_={
                        "description": service["description"],
                        "price_amount": service["price_amount"],
                        "currency": service["currency"],
                        "duration_minutes": service["duration_minutes"],
                        "is_active": True,
                    },
                )
            )
        for hours in DEMO_OPENING_HOURS:
            session.execute(
                insert(ClinicOpeningHours)
                .values(tenant_id=tenant_id, **hours)
                .on_conflict_do_update(
                    constraint="uq_clinic_opening_hours_tenant_weekday",
                    set_={
                        "is_closed": hours["is_closed"],
                        "opens_at": hours["opens_at"],
                        "closes_at": hours["closes_at"],
                    },
                )
            )
        for doctor in DEMO_DOCTORS:
            session.execute(
                insert(Doctor)
                .values(tenant_id=tenant_id, **doctor)
                .on_conflict_do_update(
                    constraint="uq_doctors_tenant_name",
                    set_={
                        "specialty": doctor["specialty"],
                        "bio": doctor["bio"],
                        "is_active": True,
                    },
                )
            )
        for chunk in DEMO_KNOWLEDGE_CHUNKS:
            session.execute(
                insert(KnowledgeChunk)
                .values(tenant_id=tenant_id, **chunk)
                .on_conflict_do_update(
                    constraint="uq_knowledge_chunks_tenant_source_chunk",
                    set_={
                        "title": chunk["title"],
                        "content": chunk["content"],
                        "language": chunk["language"],
                    },
                )
            )

    return TenantContext(
        id=tenant_id,
        slug=DEMO_TENANT_SLUG,
        name=DEMO_TENANT_NAME,
        timezone=DEMO_TENANT_TIMEZONE,
    )
