"""Database-backed worker for preparing outbound WhatsApp replies."""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import or_, select, update

from clinicflow.database import session_scope
from clinicflow.models import InboxMessage, OutboxMessage
from clinicflow.whatsapp import (
    WhatsAppAPIError,
    WhatsAppClient,
    WhatsAppRetryableError,
    WhatsAppUncertainError,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 10
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_MAX_SEND_ATTEMPTS = 3
DEFAULT_RETRY_BASE_SECONDS = 5.0
DEFAULT_STALE_SEND_SECONDS = 300.0
STALE_SEND_ERROR = (
    "Worker stopped while sending; delivery outcome is unknown and was not retried"
)


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
    retrying: int
    failed: int
    uncertain: int


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
        .where(
            OutboxMessage.status == "pending",
            or_(
                OutboxMessage.next_attempt_at.is_(None),
                OutboxMessage.next_attempt_at <= datetime.now(timezone.utc),
            ),
        )
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
            message.claimed_at = datetime.now(timezone.utc)
            message.next_attempt_at = None
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
    *,
    max_attempts: int = DEFAULT_MAX_SEND_ATTEMPTS,
    retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
) -> SendBatchResult:
    """Send one claimed batch and persist each definitive outcome."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if retry_base_seconds < 0:
        raise ValueError("retry_base_seconds cannot be negative")

    claimed = 0
    sent = 0
    retrying = 0
    failed = 0
    uncertain = 0

    for _ in range(batch_size):
        messages = claim_outbox_batch(1)
        if not messages:
            break

        message = messages[0]
        claimed += 1
        try:
            provider_message_id = sender.send_text(
                phone_number_id=message.phone_number_id,
                recipient_id=message.recipient_id,
                body=message.body,
            )
        except WhatsAppUncertainError as exc:
            _mark_outbox_uncertain(message.id, str(exc))
            uncertain += 1
        except WhatsAppRetryableError as exc:
            if _schedule_outbox_retry(
                message.id,
                str(exc),
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
            ):
                retrying += 1
            else:
                failed += 1
        except WhatsAppAPIError as exc:
            _mark_outbox_failed(message.id, str(exc))
            failed += 1
        else:
            _mark_outbox_sent(message.id, provider_message_id)
            sent += 1

    return SendBatchResult(
        claimed=claimed,
        sent=sent,
        retrying=retrying,
        failed=failed,
        uncertain=uncertain,
    )


def _mark_outbox_sent(outbox_id: int, provider_message_id: str) -> None:
    with session_scope() as session:
        message = session.get(OutboxMessage, outbox_id)
        if message is None:
            raise RuntimeError(f"Outbox message {outbox_id} no longer exists")

        message.status = "sent"
        message.provider_message_id = provider_message_id
        message.sent_at = datetime.now(timezone.utc)
        message.last_error = None
        message.claimed_at = None
        message.next_attempt_at = None


def _mark_outbox_failed(outbox_id: int, error: str) -> None:
    with session_scope() as session:
        message = session.get(OutboxMessage, outbox_id)
        if message is None:
            raise RuntimeError(f"Outbox message {outbox_id} no longer exists")

        message.status = "failed"
        message.last_error = error
        message.claimed_at = None
        message.next_attempt_at = None


def _mark_outbox_uncertain(outbox_id: int, error: str) -> None:
    with session_scope() as session:
        message = session.get(OutboxMessage, outbox_id)
        if message is None:
            raise RuntimeError(f"Outbox message {outbox_id} no longer exists")

        message.status = "uncertain"
        message.last_error = error
        message.claimed_at = None
        message.next_attempt_at = None


def _schedule_outbox_retry(
    outbox_id: int,
    error: str,
    *,
    max_attempts: int,
    retry_base_seconds: float,
) -> bool:
    """Schedule exponential backoff, returning False when attempts are exhausted."""

    with session_scope() as session:
        message = session.get(OutboxMessage, outbox_id)
        if message is None:
            raise RuntimeError(f"Outbox message {outbox_id} no longer exists")

        message.last_error = error
        message.claimed_at = None

        if message.attempts >= max_attempts:
            message.status = "failed"
            message.next_attempt_at = None
            return False

        delay_seconds = retry_base_seconds * (2 ** (message.attempts - 1))
        message.status = "pending"
        message.next_attempt_at = datetime.now(timezone.utc) + timedelta(
            seconds=delay_seconds
        )
        return True


def recover_stale_sends(
    stale_after_seconds: float = DEFAULT_STALE_SEND_SECONDS,
) -> int:
    """Quarantine abandoned sends whose delivery result cannot be known."""

    if stale_after_seconds < 0:
        raise ValueError("stale_after_seconds cannot be negative")

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    statement = (
        update(OutboxMessage)
        .where(
            OutboxMessage.status == "sending",
            or_(
                OutboxMessage.claimed_at.is_(None),
                OutboxMessage.claimed_at <= cutoff,
            ),
        )
        .values(
            status="uncertain",
            claimed_at=None,
            next_attempt_at=None,
            last_error=STALE_SEND_ERROR,
        )
    )

    with session_scope() as session:
        result = session.execute(statement)
        return result.rowcount


def run_forever() -> None:
    """Poll PostgreSQL continuously for new inbox work."""

    poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    max_attempts = int(
        os.getenv("WORKER_MAX_SEND_ATTEMPTS", DEFAULT_MAX_SEND_ATTEMPTS)
    )
    retry_base_seconds = float(
        os.getenv("WORKER_RETRY_BASE_SECONDS", DEFAULT_RETRY_BASE_SECONDS)
    )
    stale_send_seconds = float(
        os.getenv("WORKER_STALE_SEND_SECONDS", DEFAULT_STALE_SEND_SECONDS)
    )
    LOGGER.info("ClinicFlow worker started")

    with WhatsAppClient.from_environment() as sender:
        while True:
            recovered = recover_stale_sends(stale_send_seconds)
            prepared = prepare_echo_batch()
            result = send_outbox_batch(
                sender,
                max_attempts=max_attempts,
                retry_base_seconds=retry_base_seconds,
            )

            if recovered:
                LOGGER.error("Quarantined %s uncertain message(s)", recovered)
            if prepared:
                LOGGER.info("Prepared %s outbound message(s)", prepared)
            if result.sent:
                LOGGER.info("Sent %s WhatsApp message(s)", result.sent)
            if result.retrying:
                LOGGER.warning("Scheduled %s message retry/retries", result.retrying)
            if result.failed:
                LOGGER.error("Failed to send %s WhatsApp message(s)", result.failed)
            if result.uncertain:
                LOGGER.error(
                    "Quarantined %s message(s) with unknown delivery",
                    result.uncertain,
                )
            if not recovered and not prepared and not result.claimed:
                time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
