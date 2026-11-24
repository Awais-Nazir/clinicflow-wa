"""Tenant-scoped conversation and message persistence."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicflow.database import session_scope
from clinicflow.models import Conversation, ConversationMessage

MessageDirection = Literal["inbound", "outbound"]


@dataclass(frozen=True)
class ConversationContext:
    id: int
    tenant_id: int
    customer_wa_id: str
    status: str


@dataclass(frozen=True)
class StoredMessage:
    id: int
    external_message_id: str | None
    direction: str
    message_type: str
    body: str | None
    occurred_at: datetime


def get_or_create_conversation(
    tenant_id: int,
    customer_wa_id: str,
) -> ConversationContext:
    """Return the stable conversation for one customer at one clinic."""

    with session_scope() as session:
        return get_or_create_conversation_in_session(
            session,
            tenant_id,
            customer_wa_id,
        )


def get_or_create_conversation_in_session(
    session: Session,
    tenant_id: int,
    customer_wa_id: str,
) -> ConversationContext:
    """Get or create a conversation inside the caller's transaction."""

    normalized_customer_id = customer_wa_id.strip()
    if not normalized_customer_id:
        raise ValueError("customer_wa_id cannot be empty")

    statement = (
        insert(Conversation)
        .values(tenant_id=tenant_id, customer_wa_id=normalized_customer_id)
        .on_conflict_do_nothing(constraint="uq_conversations_tenant_customer")
        .returning(
            Conversation.id,
            Conversation.tenant_id,
            Conversation.customer_wa_id,
            Conversation.status,
        )
    )

    row = session.execute(statement).one_or_none()
    if row is None:
        row = session.execute(
            select(
                Conversation.id,
                Conversation.tenant_id,
                Conversation.customer_wa_id,
                Conversation.status,
            ).where(
                Conversation.tenant_id == tenant_id,
                Conversation.customer_wa_id == normalized_customer_id,
            )
        ).one()

    return ConversationContext(
        id=row.id,
        tenant_id=row.tenant_id,
        customer_wa_id=row.customer_wa_id,
        status=row.status,
    )


def record_message(
    *,
    tenant_id: int,
    conversation_id: int,
    direction: MessageDirection,
    message_type: str,
    body: str | None,
    external_message_id: str | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    """Persist a message once and update its conversation's activity time."""

    with session_scope() as session:
        return record_message_in_session(
            session,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            direction=direction,
            message_type=message_type,
            body=body,
            external_message_id=external_message_id,
            occurred_at=occurred_at,
        )


def record_message_in_session(
    session: Session,
    *,
    tenant_id: int,
    conversation_id: int,
    direction: MessageDirection,
    message_type: str,
    body: str | None,
    external_message_id: str | None = None,
    occurred_at: datetime | None = None,
) -> bool:
    """Persist a message inside the caller's transaction."""

    message_time = occurred_at or datetime.now(timezone.utc)
    statement = (
        insert(ConversationMessage)
        .values(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            external_message_id=external_message_id,
            direction=direction,
            message_type=message_type,
            body=body,
            occurred_at=message_time,
        )
        .on_conflict_do_nothing(
            constraint="uq_conversation_messages_external_message_id"
        )
        .returning(ConversationMessage.id)
    )

    inserted_id = session.scalar(statement)
    if inserted_id is None:
        return False

    session.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
        .values(
            last_message_at=func.greatest(Conversation.last_message_at, message_time),
            updated_at=func.now(),
        )
    )

    return True


def list_messages(
    tenant_id: int,
    conversation_id: int,
) -> list[StoredMessage]:
    """List a conversation's messages without crossing the tenant boundary."""

    statement = (
        select(
            ConversationMessage.id,
            ConversationMessage.external_message_id,
            ConversationMessage.direction,
            ConversationMessage.message_type,
            ConversationMessage.body,
            ConversationMessage.occurred_at,
        )
        .where(
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.conversation_id == conversation_id,
        )
        .order_by(
            ConversationMessage.occurred_at,
            ConversationMessage.id,
        )
    )

    with session_scope() as session:
        rows = session.execute(statement).all()

    return [
        StoredMessage(
            id=row.id,
            external_message_id=row.external_message_id,
            direction=row.direction,
            message_type=row.message_type,
            body=row.body,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]
