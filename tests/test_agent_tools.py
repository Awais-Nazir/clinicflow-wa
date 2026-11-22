"""Integration tests for tenant-bound model tools."""

import json
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.agent_tools import ClinicToolbox, ToolExecutionError
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
        connection.execute(text("TRUNCATE TABLE tenants RESTART IDENTITY CASCADE"))

    yield engine
    engine.dispose()


def test_tool_schemas_never_expose_tenant_id(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    toolbox = ClinicToolbox(tenant.id)

    serialized = json.dumps(toolbox.definitions())

    assert "tenant_id" not in serialized
    assert [item["name"] for item in toolbox.definitions()] == [
        "get_services",
        "get_clinic_info",
        "search_kb",
    ]


def test_services_return_exact_structured_prices(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    toolbox = ClinicToolbox(tenant.id)

    output = toolbox.execute("get_services", {})

    services = output["services"]
    assert isinstance(services, list)
    assert services[0] == {
        "name": "General consultation",
        "description": "A general in-clinic consultation.",
        "price": {"amount": "2500.00", "currency": "PKR"},
        "duration_minutes": 30,
    }


def test_clinic_info_combines_profile_hours_and_doctors(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    toolbox = ClinicToolbox(tenant.id)

    output = toolbox.execute("get_clinic_info", {})

    profile = output["profile"]
    hours = output["opening_hours"]
    doctors = output["doctors"]
    assert isinstance(profile, dict)
    assert profile["city"] == "Lahore"
    assert isinstance(hours, list)
    assert hours[0]["opens_at"] == "10:00"
    assert hours[6]["is_closed"] is True
    assert isinstance(doctors, list)
    assert doctors[1]["specialty"] == "Dermatologist"


def test_search_kb_returns_grounding_chunks(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    toolbox = ClinicToolbox(tenant.id)

    output = toolbox.execute("search_kb", {"query": "cancel appointment"})

    results = output["results"]
    assert isinstance(results, list)
    assert results[0]["source_key"] == "appointment-changes"
    assert "4 hours" in results[0]["content"]


def test_model_cannot_override_tenant(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    toolbox = ClinicToolbox(tenant.id)

    with pytest.raises(ToolExecutionError, match="unexpected: tenant_id"):
        toolbox.execute("get_services", {"tenant_id": 999_999})
    with pytest.raises(ToolExecutionError, match="unexpected: tenant_id"):
        toolbox.execute(
            "search_kb",
            {"query": "secret", "tenant_id": 999_999},
        )


def test_invalid_tool_calls_are_rejected(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    toolbox = ClinicToolbox(tenant.id)

    with pytest.raises(ToolExecutionError, match="unknown tool"):
        toolbox.execute("delete_clinic", {})
    with pytest.raises(ToolExecutionError, match="non-empty string"):
        toolbox.execute("search_kb", {"query": "  "})
    with pytest.raises(ToolExecutionError, match="JSON object"):
        toolbox.execute("get_services", [])  # type: ignore[arg-type]
