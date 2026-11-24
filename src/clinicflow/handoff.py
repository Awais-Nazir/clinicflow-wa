"""Deterministic safety checks and durable staff handoffs."""

from datetime import datetime, timedelta, timezone
import os
import re

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from clinicflow.agent import detect_reply_language
from clinicflow.conversations import get_or_create_conversation_in_session, record_message_in_session
from clinicflow.database import session_scope
from clinicflow.models import Conversation, HandoffCase, WhatsAppPhoneNumber

EMERGENCIES = re.compile(
    r"can(?:not|'t) breathe|difficulty breathing|chest pain|heavy bleeding|"
    r"unconscious|saans nahi|سانس نہیں|سینے میں درد|خون بہت"
)
HUMAN_REQUEST = re.compile(
    r"\b(?:human|real person|staff please|speak to (?:staff|someone)|talk to someone|"
    r"insaan|staff se baat)\b|انسان سے|عملے سے بات"
)
COMPLAINT = re.compile(r"\b(?:complaint|unacceptable|very unhappy|shikayat)\b|شکایت")
MEDICAL_QUESTION = re.compile(
    r"\b(?:diagnose me|what (?:medicine|dose) should|"
    r"i (?:have|am having) (?:a rash|pain|fever|swelling)|"
    r"my (?:rash|wound|skin) (?:hurts|is bleeding|is swollen)|"
    r"mujhe (?:dard|bukhar)|dawai (?:bata|chahiye))\b|مجھے (?:درد|بخار)"
)
MODEL_HANDOFF_REASONS = {
    "model_unavailable", "unsupported_numeric_facts",
    "tool_limit_exceeded", "model_round_limit",
}


def handoff_reason(question: str) -> str | None:
    normalized = question.casefold().replace("’", "'")
    for reason, pattern in (
        ("emergency", EMERGENCIES), ("requested_staff", HUMAN_REQUEST),
        ("complaint", COMPLAINT), ("medical_question", MEDICAL_QUESTION),
    ):
        if pattern.search(normalized):
            return reason
    return None


def handoff_reply(question: str, reason: str) -> str:
    language = detect_reply_language(question)
    notices = {
        "en": "I’ve passed this to the clinic team. A staff member will reply here.",
        "roman_ur": "Maine aapka message clinic staff ko bhej diya hai. Woh yahin reply karein ge.",
        "ur": "میں نے آپ کا پیغام کلینک کے عملے کو بھیج دیا ہے۔ عملہ یہاں جواب دے گا۔",
    }
    if reason == "emergency":
        urgent = {
            "en": "If this may be an emergency, contact local emergency services now. ",
            "roman_ur": "Agar emergency hai, abhi local emergency services se rabta karein. ",
            "ur": "اگر یہ ایمرجنسی ہو سکتی ہے تو ابھی مقامی ایمرجنسی سروس سے رابطہ کریں۔ ",
        }
        return urgent[language] + notices[language]
    return notices[language]


def lock_conversation(
    session: Session, tenant_id: int, conversation_id: int,
) -> Conversation | None:
    return session.scalar(select(Conversation).where(
        Conversation.tenant_id == tenant_id, Conversation.id == conversation_id,
    ).with_for_update())


def start_handoff(
    session: Session, conversation: Conversation, reason: str, now: datetime,
) -> None:
    conversation.status = "human"
    # A staff member should review and restart any interrupted booking draft.
    conversation.booking_draft = None
    pause_seconds = int(os.getenv("HANDOFF_PAUSE_SECONDS", "0"))
    conversation.human_until = now + timedelta(seconds=pause_seconds) if pause_seconds > 0 else None
    session.execute(
        insert(HandoffCase)
        .values(tenant_id=conversation.tenant_id, conversation_id=conversation.id, reason=reason)
        .on_conflict_do_nothing(
            index_elements=[HandoffCase.conversation_id], index_where=HandoffCase.closed_at.is_(None),
        )
    )


def resume_bot(session: Session, conversation: Conversation, now: datetime) -> None:
    conversation.status = "bot"
    conversation.human_until = None
    session.execute(
        update(HandoffCase)
        .where(HandoffCase.conversation_id == conversation.id, HandoffCase.closed_at.is_(None))
        .values(closed_at=now)
    )


def bot_can_reply(session: Session, conversation: Conversation, now: datetime) -> bool:
    if conversation.status == "human" and conversation.human_until and conversation.human_until <= now:
        resume_bot(session, conversation, now)
    return conversation.status == "bot"


def record_staff_echo(
    *, phone_number_id: str, recipient_id: str, message_id: str,
    body: str | None, message_type: str, occurred_at: datetime,
) -> None:
    """Mute the conversation on outbound messages mirrored from the Business app."""

    with session_scope() as session:
        tenant_id = session.scalar(select(WhatsAppPhoneNumber.tenant_id).where(
            WhatsAppPhoneNumber.phone_number_id == phone_number_id
        ))
        if tenant_id is None:
            return
        context = get_or_create_conversation_in_session(session, tenant_id, recipient_id)
        conversation = lock_conversation(session, tenant_id, context.id)
        if not record_message_in_session(
            session, tenant_id=tenant_id, conversation_id=context.id,
            direction="outbound", message_type=message_type, body=body,
            external_message_id=message_id, occurred_at=occurred_at,
        ):
            return
        start_handoff(session, conversation, "staff_reply", datetime.now(timezone.utc))
