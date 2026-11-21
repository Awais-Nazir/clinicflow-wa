"""Tests for classifying authenticated WhatsApp webhook events."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from clinicflow.events import (
    FailedDelivery,
    InboundTextMessage,
    extract_failed_deliveries,
    extract_text_messages,
)
from clinicflow.main import app

APP_SECRET = "test-app-secret"
WEBHOOK_URL = "/webhooks/whatsapp"
client = TestClient(app)


def message_payload(message: dict[str, object]) -> dict[str, object]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "phone-456"},
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def text_message() -> dict[str, object]:
    return {
        "from": "15551234567",
        "from_user_id": "US.123456789",
        "id": "wamid.message-1",
        "timestamp": "1749416383",
        "type": "text",
        "text": {"body": "Clinic kab khulti hai?"},
    }


def status_payload(delivery_status: str) -> dict[str, object]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "phone-456"},
                            "statuses": [
                                {
                                    "id": "wamid.outbound-1",
                                    "status": delivery_status,
                                    "timestamp": "1750263773",
                                    "recipient_id": "15551234567",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def failed_status_payload() -> dict[str, object]:
    payload = status_payload("failed")
    status = payload["entry"][0]["changes"][0]["value"]["statuses"][0]  # type: ignore[index]
    status["errors"] = [
        {
            "code": 131031,
            "message": "Business Account locked",
            "error_data": {"details": "Business account has been locked."},
        }
    ]
    return payload


def sign(raw_body: bytes) -> str:
    digest = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def post_signed(payload: object) -> int:
    raw_body = json.dumps(payload, separators=(",", ":")).encode()
    response = client.post(
        WEBHOOK_URL,
        content=raw_body,
        headers={"X-Hub-Signature-256": sign(raw_body)},
    )
    return response.status_code


def test_extracts_an_inbound_text_message() -> None:
    messages = extract_text_messages(message_payload(text_message()))

    assert messages == [
        InboundTextMessage(
            message_id="wamid.message-1",
            sender_id="15551234567",
            phone_number_id="phone-456",
            timestamp="1749416383",
            text="Clinic kab khulti hai?",
        )
    ]


def test_falls_back_to_user_id_when_phone_number_is_absent() -> None:
    message = text_message()
    message.pop("from")

    messages = extract_text_messages(message_payload(message))

    assert messages[0].sender_id == "US.123456789"


@pytest.mark.parametrize("delivery_status", ["delivered", "read"])
def test_ignores_a_delivery_status(
    monkeypatch: pytest.MonkeyPatch,
    delivery_status: str,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    payload = status_payload(delivery_status)

    assert extract_text_messages(payload) == []
    assert post_signed(payload) == 200


def test_extracts_a_failed_delivery_without_recipient_data() -> None:
    failures = extract_failed_deliveries(failed_status_payload())

    assert failures == [
        FailedDelivery(
            message_id="wamid.outbound-1",
            error_code="131031",
            detail="Business account has been locked.",
        )
    ]


def test_ignores_an_unsupported_message_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    image_message = text_message()
    image_message["type"] = "image"
    image_message.pop("text")
    image_message["image"] = {"id": "media-123"}
    payload = message_payload(image_message)

    assert extract_text_messages(payload) == []
    assert post_signed(payload) == 200


def test_ignores_an_unrelated_webhook_object() -> None:
    payload = message_payload(text_message())
    payload["object"] = "page"

    assert extract_text_messages(payload) == []
