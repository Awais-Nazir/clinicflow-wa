"""Unit tests for the WhatsApp Graph API client."""

import httpx
import pytest

from clinicflow.whatsapp import (
    WhatsAppClient,
    WhatsAppRejectedError,
    WhatsAppRetryableError,
    WhatsAppUncertainError,
)


def test_send_text_posts_expected_graph_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == (
            "https://graph.facebook.com/v27.0/phone-456/messages"
        )
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Content-Type"] == "application/json"
        assert request.read().decode() == (
            '{"messaging_product":"whatsapp","recipient_type":"individual",'
            '"to":"US.123456789","type":"text","text":{"preview_url":false,'
            '"body":"Assalam-o-alaikum"}}'
        )
        return httpx.Response(
            200,
            json={"messages": [{"id": "wamid.sent-123"}]},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = WhatsAppClient("test-token", http_client=http_client)

    provider_message_id = client.send_text(
        phone_number_id="phone-456",
        recipient_id="US.123456789",
        body="Assalam-o-alaikum",
    )

    assert provider_message_id == "wamid.sent-123"
    http_client.close()


def test_send_text_raises_sanitized_graph_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Invalid OAuth access token",
                    "code": 190,
                }
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = WhatsAppClient("secret-token", http_client=http_client)

    with pytest.raises(
        WhatsAppRejectedError,
        match=(
            "WhatsApp Graph API returned HTTP 401, code 190: "
            "Invalid OAuth access token"
        ),
    ) as error:
        client.send_text(
            phone_number_id="phone-456",
            recipient_id="US.123456789",
            body="Hello",
        )

    assert "secret-token" not in str(error.value)
    http_client.close()


def test_send_text_rejects_success_without_message_id() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"messages": []})
        )
    )
    client = WhatsAppClient("test-token", http_client=http_client)

    with pytest.raises(WhatsAppUncertainError, match="did not contain a message ID"):
        client.send_text(
            phone_number_id="phone-456",
            recipient_id="US.123456789",
            body="Hello",
        )

    http_client.close()


def test_send_text_classifies_server_error_as_retryable() -> None:
    http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={"error": {"message": "Try again", "code": 131000}},
            )
        )
    )
    client = WhatsAppClient("test-token", http_client=http_client)

    with pytest.raises(WhatsAppRetryableError, match="HTTP 503"):
        client.send_text(
            phone_number_id="phone-456",
            recipient_id="US.123456789",
            body="Hello",
        )

    http_client.close()


def test_send_text_classifies_read_timeout_as_uncertain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("No response", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = WhatsAppClient("test-token", http_client=http_client)

    with pytest.raises(WhatsAppUncertainError, match="unknown delivery outcome"):
        client.send_text(
            phone_number_id="phone-456",
            recipient_id="US.123456789",
            body="Hello",
        )

    http_client.close()
