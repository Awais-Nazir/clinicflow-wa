"""PostgreSQL integration tests for inbox-to-outbox processing."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.whatsapp import WhatsAppAPIError
from clinicflow.worker import prepare_echo_batch, send_outbox_batch

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


class SuccessfulSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        self.calls.append(
            {
                "phone_number_id": phone_number_id,
                "recipient_id": recipient_id,
                "body": body,
            }
        )
        return "wamid.sent-123"


class FailingSender:
    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        raise WhatsAppAPIError("Meta rejected this message")


def test_worker_sends_and_records_provider_message_id(
    database_engine: Engine,
) -> None:
    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1
    sender = SuccessfulSender()

    result = send_outbox_batch(sender)

    assert result.claimed == 1
    assert result.sent == 1
    assert result.failed == 0
    assert sender.calls == [
        {
            "phone_number_id": "phone-456",
            "recipient_id": "US.123456789",
            "body": "Assalam-o-alaikum",
        }
    ]
    assert send_outbox_batch(sender).claimed == 0

    with database_engine.connect() as connection:
        outbox = connection.execute(
            text(
                """
                SELECT status, attempts, provider_message_id,
                       sent_at IS NOT NULL AS was_sent, last_error
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert outbox == {
        "status": "sent",
        "attempts": 1,
        "provider_message_id": "wamid.sent-123",
        "was_sent": True,
        "last_error": None,
    }


def test_worker_preserves_a_failed_outbox_message(
    database_engine: Engine,
) -> None:
    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1

    result = send_outbox_batch(FailingSender())

    assert result.claimed == 1
    assert result.sent == 0
    assert result.failed == 1

    with database_engine.connect() as connection:
        outbox = connection.execute(
            text(
                """
                SELECT status, attempts, provider_message_id, sent_at, last_error
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert outbox == {
        "status": "failed",
        "attempts": 1,
        "provider_message_id": None,
        "sent_at": None,
        "last_error": "Meta rejected this message",
    }
