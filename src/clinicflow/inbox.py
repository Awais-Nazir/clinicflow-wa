"""Durable inbox writes for authenticated WhatsApp messages."""

from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from clinicflow.conversations import (
    get_or_create_conversation_in_session,
    record_message_in_session,
)
from clinicflow.database import session_scope
from clinicflow.events import InboundTextMessage
from clinicflow.models import InboxMessage, Tenant, WhatsAppPhoneNumber

LOGGER = logging.getLogger(__name__)


def enqueue_text_messages(
    messages: list[InboundTextMessage],
    payload: dict[str, object],
) -> int:
    """Insert new messages and silently skip already-seen Meta message IDs."""

    if not messages:
        return 0

    inserted_count = 0

    with session_scope() as session:
        for message in messages:
            tenant_id = session.scalar(
                select(Tenant.id)
                .join(
                    WhatsAppPhoneNumber,
                    WhatsAppPhoneNumber.tenant_id == Tenant.id,
                )
                .where(
                    WhatsAppPhoneNumber.phone_number_id
                    == message.phone_number_id
                )
            )
            if tenant_id is None:
                LOGGER.warning(
                    "Ignoring message for unknown phone_number_id=%s",
                    message.phone_number_id,
                )
                continue

            conversation = get_or_create_conversation_in_session(
                session,
                tenant_id,
                message.sender_id,
            )
            message_time = datetime.fromtimestamp(
                int(message.timestamp),
                tz=timezone.utc,
            )
            inserted_id = session.scalar(
                insert(InboxMessage)
                .values(
                    tenant_id=tenant_id,
                    conversation_id=conversation.id,
                    message_id=message.message_id,
                    phone_number_id=message.phone_number_id,
                    sender_id=message.sender_id,
                    message_type="text",
                    body=message.text,
                    message_timestamp=message_time,
                    payload=payload,
                )
                .on_conflict_do_nothing(
                    constraint="uq_inbox_messages_message_id"
                )
                .returning(InboxMessage.id)
            )
            if inserted_id is None:
                continue

            record_message_in_session(
                session,
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                external_message_id=message.message_id,
                direction="inbound",
                message_type="text",
                body=message.text,
                occurred_at=message_time,
            )
            inserted_count += 1

    return inserted_count
