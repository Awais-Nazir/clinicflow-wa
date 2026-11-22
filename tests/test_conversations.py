"""PostgreSQL integration tests for tenant-scoped conversations."""

import os
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.conversations import (
    get_or_create_conversation,
    list_messages,
    record_message,
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
        connection.execute(text("TRUNCATE TABLE tenants RESTART IDENTITY CASCADE"))

    yield engine
    engine.dispose()


def test_conversation_and_external_message_are_idempotent(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    first = get_or_create_conversation(tenant.id, "923001234567")
    second = get_or_create_conversation(tenant.id, "923001234567")
    message_time = datetime(2026, 11, 22, 10, 30, tzinfo=timezone.utc)

    inserted = record_message(
        tenant_id=tenant.id,
        conversation_id=first.id,
        external_message_id="wamid.conversation-test",
        direction="inbound",
        message_type="text",
        body="Clinic kab khulti hai?",
        occurred_at=message_time,
    )
    duplicate_inserted = record_message(
        tenant_id=tenant.id,
        conversation_id=first.id,
        external_message_id="wamid.conversation-test",
        direction="inbound",
        message_type="text",
        body="Clinic kab khulti hai?",
        occurred_at=message_time,
    )

    assert first == second
    assert first.status == "bot"
    assert inserted is True
    assert duplicate_inserted is False
    messages = list_messages(tenant.id, first.id)
    assert len(messages) == 1
    assert messages[0].body == "Clinic kab khulti hai?"
    assert messages[0].occurred_at == message_time

    with database_engine.connect() as connection:
        last_message_at = connection.execute(
            text("SELECT last_message_at FROM conversations WHERE id = :id"),
            {"id": first.id},
        ).scalar_one()

    assert last_message_at == message_time


def test_same_customer_has_separate_tenant_conversations(
    database_engine: Engine,
) -> None:
    first_tenant = seed_demo_clinic("phone-456")
    with database_engine.begin() as connection:
        second_tenant_id = connection.execute(
            text(
                """
                INSERT INTO tenants (slug, name, timezone)
                VALUES ('second-demo', 'Second Demo Clinic', 'Asia/Karachi')
                RETURNING id
                """
            )
        ).scalar_one()

    first = get_or_create_conversation(first_tenant.id, "923001234567")
    second = get_or_create_conversation(second_tenant_id, "923001234567")
    record_message(
        tenant_id=first_tenant.id,
        conversation_id=first.id,
        direction="outbound",
        message_type="text",
        body="Noor Clinic reply",
    )

    assert first.id != second.id
    assert list_messages(second_tenant_id, second.id) == []
    assert list_messages(second_tenant_id, first.id) == []


def test_empty_customer_id_is_rejected(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")

    with pytest.raises(ValueError, match="cannot be empty"):
        get_or_create_conversation(tenant.id, "  ")
