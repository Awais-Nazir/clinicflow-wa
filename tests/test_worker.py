"""PostgreSQL integration tests for inbox-to-outbox processing."""

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.whatsapp import (
    WhatsAppAPIError,
    WhatsAppRetryableError,
    WhatsAppUncertainError,
)
from clinicflow.worker import (
    STALE_SEND_ERROR,
    prepare_echo_batch,
    recover_stale_sends,
    send_outbox_batch,
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
            text("TRUNCATE TABLE outbox_messages, inbox_messages RESTART IDENTITY")
        )

    yield engine
    engine.dispose()


def insert_inbox_message(
    engine: Engine,
    *,
    message_id: str = "wamid.worker-test",
    body: str = "Assalam-o-alaikum",
) -> int:
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
                    :message_id,
                    'phone-456',
                    'US.123456789',
                    'text',
                    :body,
                    now(),
                    '{}'::jsonb
                )
                RETURNING id
                """
            ),
            {"message_id": message_id, "body": body},
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


def test_restart_after_prepare_keeps_echo_sendable(
    database_engine: Engine,
) -> None:
    """A committed outbox intent survives the boundary between worker runs."""

    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1

    sender = SuccessfulSender()
    result = send_outbox_batch(sender)

    assert result.sent == 1
    assert sender.calls[0]["body"] == "Assalam-o-alaikum"


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
    assert result.retrying == 0
    assert result.failed == 0
    assert result.uncertain == 0
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
    assert result.retrying == 0
    assert result.failed == 1
    assert result.uncertain == 0

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


class RetryableSender:
    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        raise WhatsAppRetryableError("Meta is temporarily unavailable")


class UncertainSender:
    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        raise WhatsAppUncertainError("The response timed out")


class CrashingSender:
    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        raise RuntimeError("Worker stopped unexpectedly")


def test_worker_retries_safe_failure_with_backoff(database_engine: Engine) -> None:
    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1

    result = send_outbox_batch(
        RetryableSender(),
        max_attempts=3,
        retry_base_seconds=60,
    )

    assert result.retrying == 1
    assert result.failed == 0
    assert send_outbox_batch(SuccessfulSender()).claimed == 0

    with database_engine.connect() as connection:
        outbox = connection.execute(
            text(
                """
                SELECT status, attempts, next_attempt_at > now() AS waits_to_retry,
                       claimed_at, last_error
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert outbox == {
        "status": "pending",
        "attempts": 1,
        "waits_to_retry": True,
        "claimed_at": None,
        "last_error": "Meta is temporarily unavailable",
    }


def test_worker_stops_retrying_at_attempt_limit(database_engine: Engine) -> None:
    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE outbox_messages SET attempts = 2")
        )

    result = send_outbox_batch(
        RetryableSender(),
        max_attempts=3,
        retry_base_seconds=0,
    )

    assert result.retrying == 0
    assert result.failed == 1
    with database_engine.connect() as connection:
        outbox = connection.execute(
            text(
                """
                SELECT status, attempts, next_attempt_at, claimed_at
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert outbox == {
        "status": "failed",
        "attempts": 3,
        "next_attempt_at": None,
        "claimed_at": None,
    }


def test_worker_quarantines_uncertain_send(database_engine: Engine) -> None:
    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1

    result = send_outbox_batch(UncertainSender())

    assert result.uncertain == 1
    assert result.retrying == 0
    with database_engine.connect() as connection:
        outbox = connection.execute(
            text(
                """
                SELECT status, attempts, next_attempt_at, claimed_at, last_error
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert outbox == {
        "status": "uncertain",
        "attempts": 1,
        "next_attempt_at": None,
        "claimed_at": None,
        "last_error": "The response timed out",
    }


def test_worker_recovers_stale_sending_row_as_uncertain(
    database_engine: Engine,
) -> None:
    insert_inbox_message(database_engine)
    assert prepare_echo_batch() == 1
    old_claim = datetime.now(timezone.utc) - timedelta(minutes=10)
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE outbox_messages
                SET status = 'sending', claimed_at = :old_claim
                """
            ),
            {"old_claim": old_claim},
        )

    assert recover_stale_sends(stale_after_seconds=300) == 1
    assert recover_stale_sends(stale_after_seconds=300) == 0

    with database_engine.connect() as connection:
        outbox = connection.execute(
            text(
                """
                SELECT status, claimed_at, next_attempt_at, last_error
                FROM outbox_messages
                """
            )
        ).mappings().one()

    assert outbox == {
        "status": "uncertain",
        "claimed_at": None,
        "next_attempt_at": None,
        "last_error": STALE_SEND_ERROR,
    }


def test_worker_claims_only_the_message_it_is_about_to_send(
    database_engine: Engine,
) -> None:
    insert_inbox_message(database_engine)
    insert_inbox_message(
        database_engine,
        message_id="wamid.worker-test-2",
        body="Second message",
    )
    assert prepare_echo_batch() == 2

    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        send_outbox_batch(CrashingSender())

    with database_engine.connect() as connection:
        statuses = connection.execute(
            text("SELECT status FROM outbox_messages ORDER BY id")
        ).scalars().all()

    assert statuses == ["sending", "pending"]
