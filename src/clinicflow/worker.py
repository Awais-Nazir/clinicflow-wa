"""Database-backed worker for preparing outbound WhatsApp replies."""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, aliased

from clinicflow.agent import AgentModel, PROMPT_VERSION, run_faq_agent
from clinicflow.agent_traces import save_agent_run_in_session
from clinicflow.booking_chat import handle_booking_message
from clinicflow.conversations import record_message_in_session
from clinicflow.database import session_scope
from clinicflow.handoff import (
    MODEL_HANDOFF_REASONS, bot_can_reply, handoff_reason, handoff_reply,
    lock_conversation, start_handoff,
)
from clinicflow.models import Conversation, ConversationMessage, InboxMessage, OutboxMessage, Tenant
from clinicflow.observability import configure_error_reporting
from clinicflow.quotas import Quota
from clinicflow.voice import VoiceError, VoiceProcessor, WhisperTranscriber
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
FAQ_LEASE = timedelta(minutes=15)
VOICE_FALLBACKS = {
    "en": "I couldn't catch that voice note. Please type your message.",
    "ur": "آواز واضح نہیں تھی۔ براہِ کرم اپنا پیغام لکھ کر بھیجیں۔",
    "roman_ur": "Voice note samajh nahi aaya. Please apna message type kar dein.",
}
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
    buttons: list[dict[str, str]] | None


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
        .where(
            InboxMessage.status == "pending", InboxMessage.message_type == "text",
            _faq_claim_available(),
        )
        .order_by(InboxMessage.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )

    prepared = 0
    with session_scope() as session:
        messages = session.scalars(statement).all()
        for message in messages:
            conversation = _conversation_for_inbox(session, message)
            if conversation and not bot_can_reply(session, conversation, datetime.now(timezone.utc)):
                _ignore_inbox(message)
                continue
            _prepare_reply(session, message, message.body)
            prepared += 1

    return prepared


def prepare_faq_batch(
    model: AgentModel, batch_size: int = 1, *, voice: VoiceProcessor | None = None,
) -> int:
    """Generate outside the database transaction and commit one durable reply."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    prepared = 0
    for _ in range(batch_size):
        message = _claim_faq_message()
        if message is None:
            break
        from_voice = message.message_type == "audio"
        # Do not fetch or process media in a conversation already owned by staff.
        if from_voice:
            with session_scope() as session:
                current = _lock_active_claim(session, message)
                if current is None:
                    continue
                conversation = _conversation_for_inbox(session, current)
                now = datetime.now(timezone.utc)
                if conversation is None or not bot_can_reply(session, conversation, now):
                    _ignore_inbox(current)
                    continue
                quota = Quota.for_message(session, conversation, now)
                if not quota.record_message(session, current):
                    prepared += _quota_handoff(session, current, conversation, quota, "message_limit", "", now)
                    continue
        question = _transcribe_message(message, voice) if from_voice else message.body.strip()
        with session_scope() as session:
            current = _lock_active_claim(session, message)
            if current is None:
                continue
            now = datetime.now(timezone.utc)
            conversation = _conversation_for_inbox(session, current)
            if conversation is None or not bot_can_reply(session, conversation, now):
                _ignore_inbox(current)
                continue
            settings = session.execute(
                select(Tenant.reply_tone, Tenant.default_language).where(Tenant.id == message.tenant_id)
            ).one()
            if from_voice:
                if not question:
                    _prepare_reply(session, current, VOICE_FALLBACKS[settings.default_language])
                    prepared += 1
                    continue
                session.execute(
                    update(ConversationMessage)
                    .where(*_voice_message_identity(message))
                    .values(transcript=question)
                )
            reason = handoff_reason(question) if message.message_type != "interactive" else None
            if reason:
                start_handoff(session, conversation, reason, now)
                _prepare_reply(session, current, handoff_reply(question, reason), allow_during_handoff=True)
                prepared += 1
                continue
            quota = Quota.for_message(session, conversation, now)
            if not quota.record_message(session, current):
                prepared += _quota_handoff(session, current, conversation, quota, "message_limit", question, now)
                continue
            acknowledgment = _thanks_reply(question) if message.message_type != "interactive" else None
            if acknowledgment is not None:
                _prepare_reply(session, current, acknowledgment)
                prepared += 1
                continue
            booking = handle_booking_message(
                session,
                tenant_id=message.tenant_id,
                conversation_id=message.conversation_id,
                customer_wa_id=message.sender_id,
                body=question if message.message_type != "interactive" else "",
                button_id=message.body if message.message_type == "interactive" else None,
                from_voice=from_voice,
                now=now,
            )
            if booking is not None:
                _prepare_reply(session, current, booking.body, buttons=list(booking.buttons))
                prepared += 1
                continue
            if not question:
                _ignore_inbox(current)
                continue
            if not quota.reserve_tokens(session):
                prepared += _quota_handoff(session, current, conversation, quota, "token_budget", question, now)
                continue
        result = run_faq_agent(
            question=question, tenant_id=message.tenant_id, model=model, persist_trace=False,
            tone=settings.reply_tone,
        )
        with session_scope() as session:
            current = _lock_active_claim(session, message)
            if current is None:
                continue
            conversation = _conversation_for_inbox(session, current)
            quota.settle_tokens(session, result.input_tokens + result.output_tokens)
            save_agent_run_in_session(
                session,
                tenant_id=message.tenant_id,
                conversation_id=message.conversation_id,
                prompt_version=PROMPT_VERSION,
                question=question,
                result=result,
            )
            now = datetime.now(timezone.utc)
            if conversation is None or not bot_can_reply(session, conversation, now):
                _ignore_inbox(current)
                continue
            reason = result.guardrail_reason
            if reason in MODEL_HANDOFF_REASONS:
                start_handoff(session, conversation, reason, now)
                notice = handoff_reply(question, reason)
                if reason == "model_unavailable":
                    notice = result.reply + "\n\n" + notice
                _prepare_reply(session, current, notice, allow_during_handoff=True)
            else:
                _prepare_reply(session, current, result.reply)
            prepared += 1
    return prepared


def _thanks_reply(question: str) -> str | None:
    """Acknowledge standalone thanks before booking or the FAQ agent."""
    phrase = " ".join(question.casefold().split()).rstrip(" .!۔🙏😊\ufe0f")
    if phrase in {"thanks", "thank you", "thanks a lot", "thanks so much", "thank you so much"}:
        return "You’re welcome!"
    if phrase in {"shukriya", "shukria", "bohat shukriya", "bohot shukriya", "bahut shukriya"}:
        return "Koi baat nahi!"
    if phrase in {"شکریہ", "بہت شکریہ"}:
        return "کوئی بات نہیں!"
    return None


def _quota_handoff(
    session: Session, message: InboxMessage, conversation: Conversation, quota: Quota,
    reason: str, question: str, now: datetime,
) -> int:
    start_handoff(session, conversation, reason, now)
    if not quota.claim_notice(session, reason):
        _ignore_inbox(message)
        return 0
    _prepare_reply(session, message, handoff_reply(question, reason), allow_during_handoff=True)
    return 1


def _voice_message_identity(message: InboxMessage):
    return (
        ConversationMessage.tenant_id == message.tenant_id,
        ConversationMessage.conversation_id == message.conversation_id,
        ConversationMessage.external_message_id == message.message_id,
        ConversationMessage.direction == "inbound",
    )


def _conversation_for_inbox(session: Session, message: InboxMessage) -> Conversation | None:
    if message.tenant_id is None or message.conversation_id is None:
        return None
    return lock_conversation(session, message.tenant_id, message.conversation_id)


def _ignore_inbox(message: InboxMessage) -> None:
    message.status = "ignored"
    message.processed_at = datetime.now(timezone.utc)
    message.faq_claimed_at = None


def _transcribe_message(message: InboxMessage, voice: VoiceProcessor | None) -> str:
    with session_scope() as session:
        transcript = session.scalar(
            select(ConversationMessage.transcript).where(*_voice_message_identity(message))
        )
    if transcript:
        return transcript
    if voice is not None:
        try:
            return voice.process(message.phone_number_id, message.body)
        except VoiceError:
            LOGGER.info("Voice transcription unavailable for inbox row %s", message.id)
    return ""


def _faq_claim_available():
    return or_(
        InboxMessage.faq_claimed_at.is_(None),
        InboxMessage.faq_claimed_at <= datetime.now(timezone.utc) - FAQ_LEASE,
    )


def _lock_active_claim(session: Session, message: InboxMessage) -> InboxMessage | None:
    return session.scalar(
        select(InboxMessage)
        .where(
            InboxMessage.id == message.id,
            InboxMessage.status == "pending",
            InboxMessage.attempts == message.attempts,
        )
        .with_for_update()
    )


def _claim_faq_message() -> InboxMessage | None:
    previous = aliased(InboxMessage)
    earlier_pending = select(previous.id).where(
        previous.conversation_id == InboxMessage.conversation_id,
        previous.id < InboxMessage.id,
        previous.status == "pending",
    ).exists()
    with session_scope() as session:
        message = session.scalar(
            select(InboxMessage)
            .where(
                InboxMessage.status == "pending",
                InboxMessage.tenant_id.is_not(None),
                InboxMessage.conversation_id.is_not(None),
                _faq_claim_available(),
                ~earlier_pending,
            )
            .order_by(InboxMessage.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if message is not None:
            message.faq_claimed_at = datetime.now(timezone.utc)
            message.attempts += 1
    return message


def _prepare_reply(
    session: Session, message: InboxMessage, body: str,
    *, buttons: list[dict[str, str]] | None = None, allow_during_handoff: bool = False,
) -> None:
    session.add(
        OutboxMessage(
            inbox_message_id=message.id,
            phone_number_id=message.phone_number_id,
            recipient_id=message.sender_id,
            message_type="interactive" if buttons else "text",
            body=body,
            reply_buttons=buttons or None,
            allow_during_handoff=allow_during_handoff,
        )
    )
    message.status = "processed"
    message.processed_at = datetime.now(timezone.utc)
    message.faq_claimed_at = None


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
            inbound = session.get(InboxMessage, message.inbox_message_id)
            if inbound is not None and not message.allow_during_handoff:
                conversation = _conversation_for_inbox(session, inbound)
                if conversation and not bot_can_reply(session, conversation, datetime.now(timezone.utc)):
                    message.status = "suppressed"
                    continue
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
                    buttons=message.reply_buttons,
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
            provider_message_id = _send_reply(sender, message)
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


def _send_reply(sender: TextSender, message: ClaimedOutboxMessage) -> str:
    fields = dict(
        phone_number_id=message.phone_number_id, recipient_id=message.recipient_id,
        body=message.body,
    )
    send_buttons = getattr(sender, "send_buttons", None)
    if message.buttons and callable(send_buttons):
        return send_buttons(**fields, buttons=message.buttons)
    return sender.send_text(**fields)


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
        inbound = session.get(InboxMessage, message.inbox_message_id)
        if (
            inbound is not None
            and inbound.tenant_id is not None
            and inbound.conversation_id is not None
        ):
            record_message_in_session(
                session,
                tenant_id=inbound.tenant_id,
                conversation_id=inbound.conversation_id,
                direction="outbound",
                message_type=message.message_type,
                external_message_id=provider_message_id,
                body=message.body,
                occurred_at=message.sent_at,
            )


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

    from clinicflow.ollama import OllamaModel

    reply_mode = os.getenv("WORKER_REPLY_MODE", "echo").strip().lower()
    if reply_mode not in {"echo", "faq"}:
        raise ValueError("WORKER_REPLY_MODE must be echo or faq")
    model = OllamaModel.from_env() if reply_mode == "faq" else None
    try:
        _poll_messages(model)
    finally:
        if model is not None:
            model.close()


def _poll_messages(model: AgentModel | None) -> None:
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
        voice_mode = os.getenv("VOICE_TRANSCRIBER", "off").strip().lower()
        if voice_mode not in {"off", "whisper"}:
            raise ValueError("VOICE_TRANSCRIBER must be off or whisper")
        voice = (
            VoiceProcessor(sender, WhisperTranscriber(os.getenv("WHISPER_MODEL", "small")))
            if voice_mode == "whisper" else None
        )
        while True:
            recovered = recover_stale_sends(stale_send_seconds)
            prepared = (
                prepare_faq_batch(model, voice=voice)
                if model is not None else prepare_echo_batch()
            )
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
    configure_error_reporting()
    run_forever()
