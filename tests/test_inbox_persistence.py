"""PostgreSQL integration tests for durable webhook ingestion."""

import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from clinicflow.agent import AgentMessage, AgentModelError, ModelTurn, ToolCallRequest
from clinicflow.main import app
from clinicflow.seed_booking import seed_demo_booking
from clinicflow.tenants import seed_demo_clinic
from clinicflow.voice import VoiceProcessor
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


@pytest.mark.parametrize(
    ("body", "expected_reply", "expected_agent_runs"),
    [
        ("Thanks! 🙏", "You’re welcome!", 0),
        ("Bohat shukriya", "Koi baat nahi!", 0),
        ("بہت شکریہ۔", "کوئی بات نہیں!", 0),
        ("Thanks, what are your opening hours?", "Clinic subah 10:00 baje khulti hai.", 1),
    ],
)
def test_faq_worker_acknowledges_only_standalone_thanks(
    monkeypatch: pytest.MonkeyPatch,
    database_engine: Engine,
    body: str,
    expected_reply: str,
    expected_agent_runs: int,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_clinic("phone-456")
    payload = inbound_text_payload()
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["text"]["body"] = body
    raw_body = json.dumps(payload).encode()
    with TestClient(app) as client:
        assert client.post(
            WEBHOOK_URL, content=raw_body, headers=signed_headers(raw_body)
        ).status_code == 200

    class Model(FaqTestModel):
        def complete(self, messages, tools):
            assert expected_agent_runs, "Standalone thanks should skip the model"
            return super().complete(messages, tools)

    assert prepare_faq_batch(Model()) == 1
    assert prepare_faq_batch(Model()) == 0
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT body FROM outbox_messages")) == expected_reply
        assert connection.scalar(text("SELECT count(*) FROM agent_runs")) == expected_agent_runs


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


def test_voice_booking_requires_typed_confirmation(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_booking("phone-456")
    day = datetime.now(ZoneInfo("Asia/Karachi")).date() + timedelta(days=3)
    if day.weekday() == 6:
        day += timedelta(days=1)

    class Audio:
        def download_audio(self, *, phone_number_id, media_id, max_bytes):
            assert phone_number_id == "phone-456" and max_bytes > 0
            return media_id.encode()

    class ASR:
        def transcribe(self, audio, *, max_seconds):
            assert max_seconds == 60
            return {
                b"1": f"Book a Hydra facial on {day} at 17:00. My name is Sam Ali",
                b"2": "confirm",
                b"3": "",
            }[audio]

    voice = VoiceProcessor(Audio(), ASR())
    with TestClient(app) as client:
        def turn(sequence, *, media_id=None, text_body=""):
            payload = inbound_text_payload()
            message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            message["id"] = f"wamid.voice-{sequence}"
            if media_id:
                message["type"] = "audio"
                message["audio"] = {"id": media_id, "mime_type": "audio/ogg; codecs=opus"}
            else:
                message["text"]["body"] = text_body
            raw = json.dumps(payload).encode()
            assert client.post(WEBHOOK_URL, content=raw, headers=signed_headers(raw)).status_code == 200
            assert prepare_faq_batch(FaqTestModel(), voice=voice) == 1
            with database_engine.connect() as connection:
                return connection.scalar(text("SELECT body FROM outbox_messages ORDER BY id DESC LIMIT 1"))

        proposed = turn(1, media_id="1")
        assert "Sam Ali" in proposed and str(day) in proposed and "17:00" in proposed
        assert "tap Confirm or type confirm" in turn(2, media_id="2")
        with database_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM appointments")) == 0
        assert "booked" in turn(3, text_body="confirm")
        assert "type" in turn(4, media_id="3")
    with database_engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT transcript FROM conversation_messages WHERE external_message_id = 'wamid.voice-2'"
        )) == "confirm"
        assert connection.scalar(text("SELECT count(*) FROM appointments")) == 1


def test_staff_handoff_mutes_bot_and_queues_one_staff_reply(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    token = "local-staff-test-token-only-for-tests"
    monkeypatch.setenv("STAFF_DEV_TOKEN", token)
    monkeypatch.setenv("STAFF_DEV_TENANT_SLUG", "noor-clinic-demo")
    seed_demo_clinic("phone-456")
    auth = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        def inbound(sequence, body):
            payload = inbound_text_payload()
            message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            message["id"] = f"wamid.handoff-{sequence}"
            message["text"]["body"] = body
            raw = json.dumps(payload).encode()
            return client.post(WEBHOOK_URL, content=raw, headers=signed_headers(raw))

        assert inbound(1, "I want to speak to a human").status_code == 200
        assert prepare_faq_batch(FaqTestModel()) == 1
        assert inbound(2, "When does the clinic open?").status_code == 200
        assert prepare_faq_batch(FaqTestModel()) == 0
        assert client.get("/staff/handoffs").status_code == 401
        cases = client.get("/staff/handoffs", headers=auth).json()
        assert [case["reason"] for case in cases] == ["requested_staff"]
        case_url = f"/staff/handoffs/{cases[0]['id']}"
        assert client.get(case_url, headers=auth).json()["recent_messages"][-1]["message"]
        reply = {"request_id": str(uuid4()), "text": "We can help you at reception."}
        assert client.post(case_url + "/reply", headers=auth, json=reply).json() == {"queued": True}
        assert client.post(case_url + "/reply", headers=auth, json=reply).json() == {"queued": False}
        assert client.post(case_url + "/resume", headers=auth).status_code == 204
        assert inbound(3, "Clinic kab khulti hai?").status_code == 200
        assert prepare_faq_batch(FaqTestModel()) == 1

    with database_engine.connect() as connection:
        assert connection.execute(text(
            "SELECT status FROM conversations"
        )).scalar_one() == "bot"
        assert connection.scalar(text("SELECT count(*) FROM outbox_messages")) == 3


def test_coexistence_staff_echo_suppresses_prepared_bot_reply(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    seed_demo_clinic("phone-456")
    inbound = json.dumps(inbound_text_payload()).encode()
    echo = {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{
            "field": "smb_message_echoes",
            "value": {
                "metadata": {"phone_number_id": "phone-456"},
                "message_echoes": [{
                    "to": "+15551234567", "id": "wamid.staff-echo",
                    "timestamp": "1749416430", "type": "text",
                    "text": {"body": "Hello from the clinic team"},
                }],
            },
        }]}],
    }
    with TestClient(app) as client:
        assert client.post(WEBHOOK_URL, content=inbound, headers=signed_headers(inbound)).status_code == 200
        assert prepare_faq_batch(FaqTestModel()) == 1
        raw = json.dumps(echo).encode()
        for _ in range(2):
            assert client.post(WEBHOOK_URL, content=raw, headers=signed_headers(raw)).status_code == 200

    class Sender:
        def send_text(self, **fields):
            pytest.fail("a queued bot reply must be suppressed during a staff handoff")

    assert send_outbox_batch(Sender()).sent == 0
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT status FROM outbox_messages")) == "suppressed"
        assert connection.scalar(text("SELECT reason FROM handoff_cases")) == "staff_reply"
        assert connection.scalar(text(
            "SELECT count(*) FROM conversation_messages WHERE external_message_id = 'wamid.staff-echo'"
        )) == 1


@pytest.mark.parametrize(("limit_name", "value", "expected_reason"), [
    ("CUSTOMER_DAILY_MESSAGE_LIMIT", "1", "message_limit"),
    ("TENANT_DAILY_TOKEN_LIMIT", "1", "token_budget"),
])
def test_worker_limits_send_one_notice_and_handoff(
    monkeypatch: pytest.MonkeyPatch, database_engine: Engine, limit_name, value, expected_reason,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("HANDOFF_PAUSE_SECONDS", "0")
    monkeypatch.setenv(limit_name, value)
    seed_demo_clinic("phone-456")

    with TestClient(app) as client:
        for index in range(4):
            payload = inbound_text_payload()
            message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            message["id"] = f"wamid.spam-{index}"
            raw = json.dumps(payload).encode()
            assert client.post(WEBHOOK_URL, content=raw, headers=signed_headers(raw)).status_code == 200
            prepare_faq_batch(FaqTestModel())
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT reason FROM handoff_cases")) == expected_reason
        assert connection.scalar(text(
            "SELECT count(*) FROM outbox_messages WHERE allow_during_handoff"
        )) == 1
        assert connection.scalar(text("SELECT status FROM conversations")) == "human"
        traces = connection.scalar(text("SELECT count(*) FROM agent_runs"))
        assert traces == (1 if expected_reason == "message_limit" else 0)


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
