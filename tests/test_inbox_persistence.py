"""PostgreSQL integration tests for durable webhook ingestion."""

import hashlib
import hmac
import json
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from clinicflow.main import app
from clinicflow.tenants import seed_demo_clinic
from clinicflow.worker import prepare_echo_batch

APP_SECRET = "test-app-secret"
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = "/webhooks/whatsapp"

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    if DATABASE_URL is None:
        pytest.skip("DATABASE_URL is required")

    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE outbox_messages, inbox_messages, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )

    yield engine
    engine.dispose()


def signed_headers(raw_body: bytes) -> dict[str, str]:
    digest = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}"}


def inbound_text_payload(
    phone_number_id: str = "phone-456",
) -> dict[str, object]:
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
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "from_user_id": "US.123456789",
                                    "id": "wamid.duplicate-test",
                                    "timestamp": "1749416383",
                                    "type": "text",
                                    "text": {"body": "Clinic kab khulti hai?"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def test_duplicate_webhook_creates_one_inbox_row(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    tenant = seed_demo_clinic("phone-456")
    raw_body = json.dumps(
        inbound_text_payload(),
        separators=(",", ":"),
    ).encode()

    with TestClient(app) as client:
        first_response = client.post(
            WEBHOOK_URL,
            content=raw_body,
            headers=signed_headers(raw_body),
        )
        duplicate_response = client.post(
            WEBHOOK_URL,
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert prepare_echo_batch() == 1
    assert prepare_echo_batch() == 0

    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT count(*) AS message_count,
                       min(message_id) AS message_id,
                       min(sender_id) AS sender_id,
                       min(tenant_id) AS tenant_id,
                       min(conversation_id) AS conversation_id,
                       min(status) AS status
                FROM inbox_messages
                """
            )
        ).mappings().one()
        outbox_count = connection.execute(
            text("SELECT count(*) FROM outbox_messages")
        ).scalar_one()
        conversation_message = connection.execute(
            text(
                """
                SELECT direction, message_type, body, external_message_id
                FROM conversation_messages
                """
            )
        ).mappings().one()
        conversation_count = connection.execute(
            text("SELECT count(*) FROM conversations")
        ).scalar_one()

    assert row["message_count"] == 1
    assert row["message_id"] == "wamid.duplicate-test"
    assert row["sender_id"] == "15551234567"
    assert row["tenant_id"] == tenant.id
    assert row["conversation_id"] is not None
    assert row["status"] == "processed"
    assert outbox_count == 1
    assert conversation_count == 1
    assert conversation_message == {
        "direction": "inbound",
        "message_type": "text",
        "body": "Clinic kab khulti hai?",
        "external_message_id": "wamid.duplicate-test",
    }


def test_unknown_phone_number_is_acknowledged_without_creating_work(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_clinic("phone-456")
    payload = inbound_text_payload(phone_number_id="phone-unknown")
    raw_body = json.dumps(payload, separators=(",", ":")).encode()

    with TestClient(app) as client:
        response = client.post(
            WEBHOOK_URL,
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 200
    with database_engine.connect() as connection:
        counts = connection.execute(
            text(
                """
                SELECT
                    (SELECT count(*) FROM inbox_messages) AS inbox,
                    (SELECT count(*) FROM conversations) AS conversations,
                    (SELECT count(*) FROM conversation_messages) AS messages
                """
            )
        ).mappings().one()

    assert counts == {"inbox": 0, "conversations": 0, "messages": 0}
