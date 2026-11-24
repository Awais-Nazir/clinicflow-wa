"""Enable bookable providers for the fictional Noor Clinic."""

from dataclasses import dataclass
from datetime import time
import os

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from clinicflow.database import session_scope
from clinicflow.models import ClinicService, Provider, ProviderHours, ProviderService
from clinicflow.tenants import seed_demo_clinic

DEMO_OFFERINGS = {
    "Dr. Hamza Ali": ("General consultation",),
    "Dr. Sara Ahmed": ("Hydra facial", "Skin consultation"),
}


@dataclass(frozen=True, slots=True)
class DemoOffering:
    provider_id: int
    service_id: int


def seed_demo_booking(phone_number_id: str) -> tuple[int, dict[str, DemoOffering]]:
    tenant = seed_demo_clinic(phone_number_id)
    offerings: dict[str, DemoOffering] = {}
    with session_scope() as session:
        for name, services in DEMO_OFFERINGS.items():
            provider_id = session.scalar(
                insert(Provider)
                .values(tenant_id=tenant.id, name=name)
                .on_conflict_do_update(
                    constraint="uq_providers_tenant_name", set_={"is_active": True}
                )
                .returning(Provider.id)
            )
            for weekday in range(6):
                session.execute(
                    insert(ProviderHours)
                    .values(
                        provider_id=provider_id, weekday=weekday, tenant_id=tenant.id,
                        opens_at=time(10), closes_at=time(19),
                    )
                    .on_conflict_do_nothing()
                )
            for service_name in services:
                service_id = session.execute(select(ClinicService.id).where(
                    ClinicService.tenant_id == tenant.id, ClinicService.name == service_name
                )).scalar_one()
                session.execute(insert(ProviderService).values(
                    tenant_id=tenant.id, provider_id=provider_id, service_id=service_id,
                ).on_conflict_do_nothing())
                offerings[service_name] = DemoOffering(provider_id, service_id)
    return tenant.id, offerings


if __name__ == "__main__":
    tenant_id, offerings = seed_demo_booking(os.environ["WHATSAPP_PHONE_NUMBER_ID"])
    print(f"Enabled {len(offerings)} demo bookable services for tenant {tenant_id}.")
