"""PostgreSQL integration tests for inbox-to-outbox processing."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.worker import prepare_echo_batch

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
            text("TRUNCATE TABLE outbox_messages, inbox_messages RESTART IDENTITY")
        )

    yield engine
    engine.dispose()


def insert_inbox_message(engine: Engine) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text(
                """
                INSERT INTO inbox_messages (
                    message_id,
                    phone_number_id,
                    sender_id,
                    message_type,
                    body,
                    message_timestamp,
                    payload
                )
                VALUES (
                    'wamid.worker-test',
                    'phone-456',
                    'US.123456789',
                    'text',
                    'Assalam-o-alaikum',
                    now(),
                    '{}'::jsonb
                )
                RETURNING id
                """
            )
        ).scalar_one()


def test_worker_prepares_one_durable_echo(database_engine: Engine) -> None:
    inbox_id = insert_inbox_message(database_engine)

    assert prepare_echo_batch() == 1
    assert prepare_echo_batch() == 0

    with database_engine.connect() as connection:
        inbox = connection.execute(
            text(
                """
                SELECT status, processed_at IS NOT NULL AS was_processed
                FROM inbox_messages
                WHERE id = :inbox_id
                """
            ),
            {"inbox_id": inbox_id},
        ).mappings().one()
        outbox = connection.execute(
            text(
                """
                SELECT inbox_message_id, phone_number_id, recipient_id,
                       message_type, body, status, attempts
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert inbox == {"status": "processed", "was_processed": True}
    assert outbox == {
        "inbox_message_id": inbox_id,
        "phone_number_id": "phone-456",
        "recipient_id": "US.123456789",
        "message_type": "text",
        "body": "Assalam-o-alaikum",
        "status": "pending",
        "attempts": 0,
    }


def test_worker_skips_a_row_locked_by_another_worker(
    database_engine: Engine,
) -> None:
    inbox_id = insert_inbox_message(database_engine)

    with database_engine.connect() as locking_connection:
        transaction = locking_connection.begin()
        locking_connection.execute(
            text("SELECT id FROM inbox_messages WHERE id = :id FOR UPDATE"),
            {"id": inbox_id},
        )

        assert prepare_echo_batch() == 0
        transaction.rollback()

    assert prepare_echo_batch() == 1
