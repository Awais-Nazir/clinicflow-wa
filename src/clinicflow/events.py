"""Classification of authenticated WhatsApp webhook payloads."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """The fields needed to persist and process a supported user message."""

    message_id: str
    sender_id: str
    phone_number_id: str
    timestamp: str
    text: str
    message_type: str = "text"


@dataclass(frozen=True, slots=True)
class FailedDelivery:
    """A non-PII summary of an asynchronous outbound delivery failure."""

    message_id: str
    error_code: str
    detail: str


@dataclass(frozen=True, slots=True)
class StaffEcho:
    phone_number_id: str
    recipient_id: str
    message_id: str
    message_type: str
    body: str | None
    occurred_at: datetime


def _non_empty_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def extract_messages(payload: object) -> list[InboundMessage]:
    """Extract supported inbound messages and ignore every other event type."""

    if not isinstance(payload, dict):
        return []
    if payload.get("object") != "whatsapp_business_account":
        return []

    extracted: list[InboundMessage] = []
    for entry in _list(payload.get("entry")):
        if not isinstance(entry, dict):
            continue

        for change in _list(entry.get("changes")):
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            metadata = value.get("metadata")
            if not isinstance(metadata, dict):
                continue
            phone_number_id = _non_empty_string(metadata.get("phone_number_id"))
            if phone_number_id is None:
                continue

            for message in _list(value.get("messages")):
                if not isinstance(message, dict):
                    continue

                message_type = message.get("type")
                if message_type == "text":
                    text_object = message.get("text")
                    content = text_object.get("body") if isinstance(text_object, dict) else None
                elif message_type == "interactive":
                    interactive = message.get("interactive")
                    if not isinstance(interactive, dict) or interactive.get("type") != "button_reply":
                        continue
                    button = interactive.get("button_reply")
                    content = button.get("id") if isinstance(button, dict) else None
                    if not isinstance(content, str) or not content.startswith("booking:"):
                        continue
                elif message_type == "audio":
                    audio = message.get("audio")
                    content = audio.get("id") if isinstance(audio, dict) else None
                    if not isinstance(content, str) or not content.isascii() or not content.isdecimal():
                        continue
                else:
                    continue

                message_id = _non_empty_string(message.get("id"))
                # Cloud API test-recipient allowlisting is based on the
                # sender's E.164 phone number. Newer payloads can also include
                # a business-scoped user ID, which is only a fallback when a
                # phone number is unavailable (for example, username users).
                sender_id = _non_empty_string(message.get("from"))
                sender_id = sender_id or _non_empty_string(
                    message.get("from_user_id")
                )
                timestamp = _non_empty_string(message.get("timestamp"))
                text = _non_empty_string(content)

                if (
                    message_id is None
                    or sender_id is None
                    or timestamp is None
                    or text is None
                ):
                    continue

                extracted.append(
                    InboundMessage(
                        message_id=message_id,
                        sender_id=sender_id,
                        phone_number_id=phone_number_id,
                        timestamp=timestamp,
                        text=text,
                        message_type=message_type,
                    )
                )

    return extracted


def extract_staff_echoes(payload: object) -> list[StaffEcho]:
    """Classify staff-originated messages from the Coexistence webhook."""

    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []
    echoes = []
    for entry in _list(payload.get("entry")):
        if not isinstance(entry, dict):
            continue
        for change in _list(entry.get("changes")):
            if not isinstance(change, dict) or change.get("field") != "smb_message_echoes":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            meta = value.get("metadata")
            phone = _non_empty_string(meta.get("phone_number_id")) if isinstance(meta, dict) else None
            if phone is None:
                continue
            for message in _list(value.get("message_echoes")):
                if not isinstance(message, dict):
                    continue
                kind = _non_empty_string(message.get("type"))
                recipient = _non_empty_string(message.get("to")) or _non_empty_string(message.get("to_user_id"))
                message_id = _non_empty_string(message.get("id"))
                if not recipient or not message_id or not kind or len(kind) > 32 or kind in {"edit", "revoke"}:
                    continue
                try:
                    occurred_at = datetime.fromtimestamp(int(message.get("timestamp")), tz=timezone.utc)
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
                text = message.get("text")
                body = _non_empty_string(text.get("body")) if isinstance(text, dict) else None
                echoes.append(StaffEcho(
                    phone, recipient.removeprefix("+"), message_id, kind, body, occurred_at,
                ))
    return echoes


def extract_failed_deliveries(payload: object) -> list[FailedDelivery]:
    """Extract failed statuses while ignoring delivered/read status events."""

    if not isinstance(payload, dict):
        return []
    if payload.get("object") != "whatsapp_business_account":
        return []

    failures: list[FailedDelivery] = []
    for entry in _list(payload.get("entry")):
        if not isinstance(entry, dict):
            continue

        for change in _list(entry.get("changes")):
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            for delivery_status in _list(value.get("statuses")):
                if not isinstance(delivery_status, dict):
                    continue
                if delivery_status.get("status") != "failed":
                    continue

                message_id = _non_empty_string(delivery_status.get("id"))
                if message_id is None:
                    continue

                errors = _list(delivery_status.get("errors"))
                if not errors:
                    failures.append(
                        FailedDelivery(
                            message_id=message_id,
                            error_code="unknown",
                            detail="WhatsApp reported a failed delivery",
                        )
                    )
                    continue

                for error in errors:
                    if not isinstance(error, dict):
                        continue
                    code = error.get("code")
                    error_data = error.get("error_data")
                    detail = None
                    if isinstance(error_data, dict):
                        detail = _non_empty_string(error_data.get("details"))
                    detail = detail or _non_empty_string(error.get("message"))
                    detail = detail or "WhatsApp reported a failed delivery"
                    failures.append(
                        FailedDelivery(
                            message_id=message_id,
                            error_code=str(code) if code is not None else "unknown",
                            detail=detail,
                        )
                    )

    return failures
