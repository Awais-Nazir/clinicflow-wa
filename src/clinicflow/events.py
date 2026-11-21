"""Classification of authenticated WhatsApp webhook payloads."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundTextMessage:
    """The fields needed to persist and later process an inbound text message."""

    message_id: str
    sender_id: str
    phone_number_id: str
    timestamp: str
    text: str


@dataclass(frozen=True, slots=True)
class FailedDelivery:
    """A non-PII summary of an asynchronous outbound delivery failure."""

    message_id: str
    error_code: str
    detail: str


def _non_empty_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def extract_text_messages(payload: object) -> list[InboundTextMessage]:
    """Extract supported inbound messages and ignore every other event type."""

    if not isinstance(payload, dict):
        return []
    if payload.get("object") != "whatsapp_business_account":
        return []

    extracted: list[InboundTextMessage] = []
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
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue

                text_object = message.get("text")
                if not isinstance(text_object, dict):
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
                text = _non_empty_string(text_object.get("body"))

                if (
                    message_id is None
                    or sender_id is None
                    or timestamp is None
                    or text is None
                ):
                    continue

                extracted.append(
                    InboundTextMessage(
                        message_id=message_id,
                        sender_id=sender_id,
                        phone_number_id=phone_number_id,
                        timestamp=timestamp,
                        text=text,
                    )
                )

    return extracted


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
