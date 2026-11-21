"""Database-backed worker for preparing outbound WhatsApp replies."""

import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import select

from clinicflow.database import session_scope
from clinicflow.models import InboxMessage, OutboxMessage

LOGGER = logging.getLogger(__name__)
DEFAULT_BATCH_SIZE = 10
DEFAULT_POLL_SECONDS = 1.0


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


def run_forever() -> None:
    """Poll PostgreSQL continuously for new inbox work."""

    poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS))
    LOGGER.info("ClinicFlow worker started")

    while True:
        processed = prepare_echo_batch()
        if processed:
            LOGGER.info("Prepared %s outbound message(s)", processed)
        else:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
