"""Database-backed worker for preparing outbound WhatsApp replies."""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select

from clinicflow.database import session_scope
from clinicflow.models import InboxMessage, OutboxMessage
from clinicflow.whatsapp import WhatsAppAPIError, WhatsAppClient

LOGGER = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 10
DEFAULT_POLL_SECONDS = 1.0


class TextSender(Protocol):
    """The sender behavior the worker needs from the Graph API client."""

    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ClaimedOutboxMessage:
    id: int
    phone_number_id: str
    recipient_id: str
    body: str


@dataclass(frozen=True, slots=True)
class SendBatchResult:
    claimed: int
    sent: int
    failed: int


def prepare_echo_batch(batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Atomically turn available inbox rows into durable echo intents."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    statement = (
        select(InboxMessage)
        .where(InboxMessage.status == "pending")
        .order_by(InboxMessage.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    with session_scope() as session:
        messages = session.scalars(statement).all()
        processed_at = datetime.now(timezone.utc)

        for message in messages:
            session.add(
                OutboxMessage(
                    inbox_message_id=message.id,
                    phone_number_id=message.phone_number_id,
                    recipient_id=message.sender_id,
                    message_type="text",
                    body=message.body,
                )
            )
            message.status = "processed"
            message.processed_at = processed_at

    return len(messages)


def claim_outbox_batch(
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ClaimedOutboxMessage]:
    """Claim pending outbound messages so another worker cannot send them."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    statement = (
        select(OutboxMessage)
        .where(OutboxMessage.status == "pending")
        .order_by(OutboxMessage.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    with session_scope() as session:
        messages = session.scalars(statement).all()
        claimed = []

        for message in messages:
            message.status = "sending"
            message.attempts += 1
            message.last_error = None
            claimed.append(
                ClaimedOutboxMessage(
                    id=message.id,
                    phone_number_id=message.phone_number_id,
                    recipient_id=message.recipient_id,
                    body=message.body,
                )
            )

    return claimed


def send_outbox_batch(
    sender: TextSender,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> SendBatchResult:
    """Send one claimed batch and persist each definitive outcome."""

    messages = claim_outbox_batch(batch_size)
    sent = 0
    failed = 0

    for message in messages:
        try:
            provider_message_id = sender.send_text(
                phone_number_id=message.phone_number_id,
                recipient_id=message.recipient_id,
                body=message.body,
            )
        except WhatsAppAPIError as exc:
            _mark_outbox_failed(message.id, str(exc))
            failed += 1
        else:
            _mark_outbox_sent(message.id, provider_message_id)
            sent += 1

    return SendBatchResult(claimed=len(messages), sent=sent, failed=failed)


def _mark_outbox_sent(outbox_id: int, provider_message_id: str) -> None:
    with session_scope() as session:
        message = session.get(OutboxMessage, outbox_id)
        if message is None:
            raise RuntimeError(f"Outbox message {outbox_id} no longer exists")

        message.status = "sent"
        message.provider_message_id = provider_message_id
        message.sent_at = datetime.now(timezone.utc)
        message.last_error = None


def _mark_outbox_failed(outbox_id: int, error: str) -> None:
    with session_scope() as session:
        message = session.get(OutboxMessage, outbox_id)
        if message is None:
            raise RuntimeError(f"Outbox message {outbox_id} no longer exists")

        message.status = "failed"
        message.last_error = error


def run_forever() -> None:
    """Poll PostgreSQL continuously for new inbox work."""

    poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    LOGGER.info("ClinicFlow worker started")

    with WhatsAppClient.from_environment() as sender:
        while True:
            prepared = prepare_echo_batch()
            result = send_outbox_batch(sender)

            if prepared:
                LOGGER.info("Prepared %s outbound message(s)", prepared)
            if result.sent:
                LOGGER.info("Sent %s WhatsApp message(s)", result.sent)
            if result.failed:
                LOGGER.error("Failed to send %s WhatsApp message(s)", result.failed)
            if not prepared and not result.claimed:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
