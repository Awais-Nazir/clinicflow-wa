"""Command-line seed for the fake Phase 1 clinic."""

import os

from clinicflow.tenants import seed_demo_clinic


def main() -> None:
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not configured")

    tenant = seed_demo_clinic(phone_number_id)
    print(f"Seeded {tenant.name} ({tenant.slug})")


if __name__ == "__main__":
    main()
