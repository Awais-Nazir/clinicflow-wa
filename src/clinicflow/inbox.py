"""Durable inbox writes for authenticated WhatsApp messages."""

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert

from clinicflow.database import session_scope
from clinicflow.events import InboundTextMessage
from clinicflow.models import InboxMessage


def enqueue_text_messages(
    messages: list[InboundTextMessage],
    payload: dict[str, object],
) -> int:
    """Insert new messages and silently skip already-seen Meta message IDs."""

    if not messages:
        return 0

    rows = [
        {
            "message_id": message.message_id,
            "phone_number_id": message.phone_number_id,
            "sender_id": message.sender_id,
            "message_type": "text",
            "body": message.text,
            "message_timestamp": datetime.fromtimestamp(
                int(message.timestamp),
                tz=timezone.utc,
            ),
            "payload": payload,
        }
        for message in messages
    ]

    statement = (
        insert(InboxMessage)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_inbox_messages_message_id")
        .returning(InboxMessage.id)
    )

    with session_scope() as session:
        inserted_ids = session.scalars(statement).all()

    return len(inserted_ids)
