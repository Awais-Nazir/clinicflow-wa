"""PostgreSQL integration tests for durable webhook ingestion."""

import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from clinicflow.agent import AgentMessage, AgentModelError, ModelTurn, ToolCallRequest
from clinicflow.main import app
from clinicflow.seed_booking import seed_demo_booking
from clinicflow.tenants import seed_demo_clinic
from clinicflow.worker import prepare_echo_batch, prepare_faq_batch, send_outbox_batch

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


class FaqTestModel:
    model_name = "test-faq"

    def complete(
        self, messages: tuple[AgentMessage, ...], tools: list[dict[str, object]]
    ) -> ModelTurn:
        if messages[-1].role == "user":
            return ModelTurn(tool_calls=(ToolCallRequest("hours", "get_clinic_info", {}),))
        return ModelTurn(text="Clinic subah 10:00 baje khulti hai.")


def test_faq_webhook_saves_one_trace_intent_and_sent_message(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_clinic("phone-456")
    raw_body = json.dumps(inbound_text_payload()).encode()
    with TestClient(app) as client:
        for _ in range(2):
            assert client.post(
                WEBHOOK_URL, content=raw_body, headers=signed_headers(raw_body)
            ).status_code == 200

    # A dead worker held the inbox claim before completing the reply.
    with database_engine.begin() as connection:
        connection.execute(text("UPDATE inbox_messages SET faq_claimed_at = now()"))
    assert prepare_faq_batch(FaqTestModel()) == 0
    with database_engine.begin() as connection:
        connection.execute(text(
            "UPDATE inbox_messages SET faq_claimed_at = now() - INTERVAL '16 minutes'"
        ))
    assert prepare_faq_batch(FaqTestModel()) == 1
    assert prepare_faq_batch(FaqTestModel()) == 0

    class Sender:
        def send_text(self, *, phone_number_id: str, recipient_id: str, body: str) -> str:
            assert phone_number_id == "phone-456"
            assert recipient_id == "15551234567"
            assert body == "Clinic subah 10:00 baje khulti hai."
            return "wamid.faq-sent"

    assert send_outbox_batch(Sender()).sent == 1
    assert send_outbox_batch(Sender()).sent == 0
    with database_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT status, model_name FROM agent_runs"
        )).one() == ("completed", "test-faq")
        assert connection.execute(text(
            "SELECT direction, external_message_id FROM conversation_messages "
            "ORDER BY id"
        )).all() == [
            ("inbound", "wamid.duplicate-test"),
            ("outbound", "wamid.faq-sent"),
        ]


def test_faq_worker_sends_localized_retry_notice_when_model_is_down(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    class UnavailableModel(FaqTestModel):
        def complete(self, messages, tools):
            raise AgentModelError("provider details stay out of the reply")

    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_clinic("phone-456")
    raw_body = json.dumps(inbound_text_payload()).encode()
    with TestClient(app) as client:
        assert client.post(
            WEBHOOK_URL, content=raw_body, headers=signed_headers(raw_body)
        ).status_code == 200

    assert prepare_faq_batch(UnavailableModel()) == 1
    with database_engine.connect() as connection:
        reply = connection.scalar(text("SELECT body FROM outbox_messages"))
        reason = connection.scalar(text("SELECT guardrail_reason FROM agent_runs"))

    assert "dobara try karein" in reply
    assert reason == "model_unavailable"


def test_whatsapp_booking_confirms_latest_draft_once(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_booking("phone-456")
    day = datetime.now(ZoneInfo("Asia/Karachi")).date() + timedelta(days=2)
    if day.weekday() == 6:
        day += timedelta(days=1)

    class Sender:
        messages = []

        def send_text(self, *, phone_number_id, recipient_id, body):
            self.messages.append((body, []))
            return f"wamid.sent-{len(self.messages)}"

        def send_buttons(self, *, phone_number_id, recipient_id, body, buttons):
            self.messages.append((body, buttons))
            return f"wamid.sent-{len(self.messages)}"

    sender = Sender()
    with TestClient(app) as client:
        def turn(sequence, *, body="", button_id=None):
            payload = inbound_text_payload()
            message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            message["id"] = f"wamid.booking-{sequence}"
            message["timestamp"] = str(int(datetime.now().timestamp()))
            if button_id:
                message["type"] = "interactive"
                message["interactive"] = {"type": "button_reply", "button_reply": {"id": button_id}}
            else:
                message["text"]["body"] = body
            raw = json.dumps(payload).encode()
            assert client.post(WEBHOOK_URL, content=raw, headers=signed_headers(raw)).status_code == 200
            assert prepare_faq_batch(FaqTestModel()) == 1
            assert send_outbox_batch(sender).sent == 1
            return sender.messages[-1]

        prompt, buttons = turn(1, body=f"Book a Hydra facial on {day} at 17:00")
        assert "name" in prompt and not buttons
        prompt, buttons = turn(2, body="Awais Nazir")
        assert "17:00" in prompt and "Awais Nazir" in prompt
        old_button = buttons[0]["id"]
        prompt, buttons = turn(3, body="Actually at 17:30")
        assert "17:30" in prompt
        correct_button = buttons[0]["id"]
        assert correct_button != old_button
        assert "expired" in turn(4, button_id=old_button)[0]
        with database_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM appointments")) == 0
        assert "booked" in turn(5, button_id=correct_button)[0]
        assert "expired" in turn(6, button_id=correct_button)[0]
        assert "cancel" in turn(7, body="Cancel my appointment")[0].lower()
        assert "cancelled" in turn(8, body="confirm")[0]

    with database_engine.connect() as connection:
        row = connection.execute(text("SELECT customer_name, starts_at, status FROM appointments")).one()
        assert row[0] == "Awais Nazir"
        assert row[1].astimezone(ZoneInfo("Asia/Karachi")).strftime("%H:%M") == "17:30"
        assert row[2] == "cancelled"


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
