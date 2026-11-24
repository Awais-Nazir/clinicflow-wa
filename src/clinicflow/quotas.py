"""Race-safe daily customer limits and FAQ token reservations."""

from dataclasses import dataclass
from datetime import date, datetime
import os
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicflow.agent import MAX_MODEL_ROUNDS
from clinicflow.models import Conversation, InboxMessage, Tenant, UsageCounter
from clinicflow.ollama import OLLAMA_CONTEXT_TOKENS, OLLAMA_MAX_GENERATION_TOKENS

FAQ_TOKEN_RESERVATION = MAX_MODEL_ROUNDS * (OLLAMA_CONTEXT_TOKENS + OLLAMA_MAX_GENERATION_TOKENS)


@dataclass(frozen=True, slots=True)
class Quota:
    tenant_id: int
    conversation_id: int
    day: date

    @classmethod
    def for_message(cls, session: Session, conversation: Conversation, now: datetime) -> "Quota":
        timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == conversation.tenant_id))
        return cls(
            conversation.tenant_id, conversation.id, now.astimezone(ZoneInfo(timezone)).date()
        )

    def record_message(self, session: Session, message: InboxMessage) -> bool:
        if message.quota_day is not None:
            return True
        if not self._increment(
            session, f"customer:{self.conversation_id}", 1, _limit("CUSTOMER_DAILY_MESSAGE_LIMIT", 40)
        ):
            return False
        message.quota_day = self.day
        return True

    def reserve_tokens(self, session: Session) -> bool:
        return self._increment(
            session, "faq_tokens", FAQ_TOKEN_RESERVATION, _limit("TENANT_DAILY_TOKEN_LIMIT", 120000)
        )

    def settle_tokens(self, session: Session, actual: int) -> None:
        session.execute(
            update(UsageCounter).where(*self._identity("faq_tokens")).values(
                units=UsageCounter.units + actual - FAQ_TOKEN_RESERVATION
            )
        )

    def claim_notice(self, session: Session, reason: str) -> bool:
        return self._increment(session, f"notice:{reason}:{self.conversation_id}", 1, 1)

    def _identity(self, subject: str):
        return (
            UsageCounter.tenant_id == self.tenant_id,
            UsageCounter.day == self.day,
            UsageCounter.subject == subject,
        )

    def _increment(self, session: Session, subject: str, units: int, maximum: int) -> bool:
        session.execute(
            insert(UsageCounter).values(tenant_id=self.tenant_id, day=self.day, subject=subject)
            .on_conflict_do_nothing()
        )
        statement = update(UsageCounter).where(*self._identity(subject))
        if maximum:
            statement = statement.where(UsageCounter.units + units <= maximum)
        return session.execute(statement.values(units=UsageCounter.units + units)).rowcount == 1


def _limit(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value
