"""PostgreSQL integration tests for structured, tenant-scoped clinic facts."""

import os
from collections.abc import Iterator
from datetime import time
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.clinic_facts import (
    get_clinic_info,
    get_doctors,
    get_opening_hours,
    get_services,
)
from clinicflow.tenants import seed_demo_clinic

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    if DATABASE_URL is None:
        pytest.skip("DATABASE_URL is required")

    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE doctors, clinic_opening_hours, "
                "clinic_services, clinic_profiles, "
                "whatsapp_phone_numbers, tenants RESTART IDENTITY CASCADE"
            )
        )

    yield engine
    engine.dispose()


def test_demo_seed_exposes_structured_profile_and_services(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")

    clinic = get_clinic_info(tenant.id)
    services = get_services(tenant.id)

    assert clinic is not None
    assert clinic.city == "Lahore"
    assert clinic.country == "Pakistan"
    assert clinic.contact_phone == "+92 300 0000000"
    assert [service.name for service in services] == [
        "General consultation",
        "Hydra facial",
        "Skin consultation",
    ]
    assert services[0].price_amount == Decimal("2500.00")
    assert services[0].currency == "PKR"
    assert services[0].duration_minutes == 30


def test_facts_do_not_leak_to_an_unknown_tenant(database_engine: Engine) -> None:
    seed_demo_clinic("phone-456")

    assert get_clinic_info(999_999) is None
    assert get_services(999_999) == []
    assert get_opening_hours(999_999) == []
    assert get_doctors(999_999) == []


def test_demo_seed_exposes_weekly_hours_and_doctors(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")

    hours = get_opening_hours(tenant.id)
    doctors = get_doctors(tenant.id)

    assert len(hours) == 7
    assert hours[0].day_name == "Monday"
    assert hours[0].opens_at == time(10, 0)
    assert hours[0].closes_at == time(19, 0)
    assert hours[0].is_closed is False
    assert hours[6].day_name == "Sunday"
    assert hours[6].is_closed is True
    assert hours[6].opens_at is None
    assert hours[6].closes_at is None
    assert [(doctor.name, doctor.specialty) for doctor in doctors] == [
        ("Dr. Hamza Ali", "General Physician"),
        ("Dr. Sara Ahmed", "Dermatologist"),
    ]
