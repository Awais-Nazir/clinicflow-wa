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
            text("TRUNCATE TABLE outbox_messages, inbox_messages RESTART IDENTITY")
        )

    yield engine
    engine.dispose()


def signed_headers(raw_body: bytes) -> dict[str, str]:
    digest = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}"}


def inbound_text_payload() -> dict[str, object]:
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

    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT count(*) AS message_count,
                       min(message_id) AS message_id,
                       min(sender_id) AS sender_id,
                       min(status) AS status
                FROM inbox_messages
                """
            )
        ).mappings().one()

    assert row == {
        "message_count": 1,
        "message_id": "wamid.duplicate-test",
        "sender_id": "US.123456789",
        "status": "pending",
    }
