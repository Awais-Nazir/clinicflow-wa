"""PostgreSQL integration tests for tenant seeding and lookup."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.tenants import (
    DEMO_TENANT_NAME,
    DEMO_TENANT_SLUG,
    DEMO_TENANT_TIMEZONE,
    get_tenant_by_phone_number_id,
    seed_demo_clinic,
)

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
                "TRUNCATE TABLE whatsapp_phone_numbers, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )

    yield engine
    engine.dispose()


def test_seed_is_idempotent_and_number_resolves_to_demo_tenant(
    database_engine: Engine,
) -> None:
    first = seed_demo_clinic("phone-456", display_phone_number="+1 555 123")
    second = seed_demo_clinic("phone-456", display_phone_number="+1 555 123")

    assert first == second
    assert get_tenant_by_phone_number_id("phone-456") == first
    assert first.slug == DEMO_TENANT_SLUG
    assert first.name == DEMO_TENANT_NAME
    assert first.timezone == DEMO_TENANT_TIMEZONE

    with database_engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM tenants) AS tenants,
                    (SELECT count(*) FROM whatsapp_phone_numbers) AS numbers
                """
            )
        ).mappings().one()

    assert counts == {"tenants": 1, "numbers": 1}


def test_unknown_phone_number_has_no_tenant(database_engine: Engine) -> None:
    seed_demo_clinic("phone-456")

    assert get_tenant_by_phone_number_id("phone-unknown") is None


def test_seed_rejects_empty_phone_number_id(database_engine: Engine) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        seed_demo_clinic("  ")
