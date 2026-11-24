"""Exercise two fictional clinic numbers through a local Whaloc instance."""

import argparse
import json
import os
from pathlib import Path
from time import perf_counter

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from clinicflow.database import session_scope
from clinicflow.models import Conversation, OutboxMessage
from clinicflow.ollama import OllamaModel
from clinicflow.onboarding import TenantManifest, onboard_tenant
from clinicflow.regression import is_local_test_database
from clinicflow.seed_booking import seed_demo_booking
from clinicflow.whatsapp import WhatsAppClient
from clinicflow.worker import prepare_faq_batch, send_outbox_batch

WHALOC_URL = "http://127.0.0.1:8081"
NOOR_NUMBER = "700000000000001"
CUSTOMER = "12025550125"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("examples/second-clinic.json"))
    args = parser.parse_args()
    database_url = os.getenv("CLINICFLOW_TEST_DATABASE_URL", "")
    if not is_local_test_database(database_url):
        parser.error("set CLINICFLOW_TEST_DATABASE_URL to a dedicated migrated local test database")
    os.environ["DATABASE_URL"] = database_url
    os.environ["META_APP_SECRET"] = os.getenv("WHALOC_APP_SECRET", "clinicflow-local-emulator-secret")
    os.environ["META_GRAPH_API_BASE_URL"] = WHALOC_URL
    os.environ.pop("SENTRY_DSN", None)
    from clinicflow.main import app

    manifest = TenantManifest.model_validate_json(args.manifest.read_bytes())
    with session_scope() as session:
        if session.scalar(select(OutboxMessage.id).where(OutboxMessage.status == "pending").limit(1)):
            parser.error("use a test database with an empty pending outbox")
    noor_id, _ = seed_demo_booking(NOOR_NUMBER)
    started = perf_counter()
    mehr_id = onboard_tenant(manifest)
    elapsed = perf_counter() - started
    questions = {
        NOOR_NUMBER: "How much is the Hydra facial?",
        manifest.phone_number_id: "How much is a Wellness consultation?",
    }
    expected = {
        NOOR_NUMBER: "Hydra facial costs PKR 8500.00.",
        manifest.phone_number_id: "Wellness consultation costs PKR 4200.00.",
    }
    model = OllamaModel.from_env()
    try:
        with httpx.Client(base_url=WHALOC_URL, timeout=10) as emulator, TestClient(app) as app_client:
            for number, question in questions.items():
                posted = emulator.post("/api/inbound", json={
                    "phoneNumberId": number, "from": CUSTOMER, "type": "text", "text": {"body": question},
                })
                posted.raise_for_status()
                message_id = posted.json()["data"]["id"]
                _replay_webhook(emulator, app_client, message_id)
            assert prepare_faq_batch(model, batch_size=2) == 2
            with WhatsAppClient.from_environment() as sender:
                assert send_outbox_batch(sender).sent == 2
    finally:
        model.close()

    with session_scope() as session:
        rows = session.execute(select(OutboxMessage.phone_number_id, OutboxMessage.body).where(
            OutboxMessage.phone_number_id.in_(questions),
            OutboxMessage.recipient_id == CUSTOMER, OutboxMessage.status == "sent",
        ).order_by(OutboxMessage.id.desc()).limit(2)).all()
        conversations = session.scalars(select(Conversation.id).where(
            Conversation.tenant_id.in_((noor_id, mehr_id)),
            Conversation.customer_wa_id == CUSTOMER,
        )).all()
    assert dict(rows) == expected
    assert len(conversations) == 2
    print(f"Second clinic provisioned in {elapsed:.2f}s.")
    for number, answer in rows:
        print(f"Local Graph delivery {number}: {answer}")
    print("Verified two distinct conversations for the same fictional customer.")
    return 0


def _replay_webhook(emulator: httpx.Client, app_client: TestClient, message_id: str) -> None:
    deliveries = emulator.get("/api/webhook-deliveries", params={"limit": 20})
    deliveries.raise_for_status()
    for entry in deliveries.json()["data"]:
        raw = entry["requestBody"]
        if entry["eventType"] == "messages" and message_id in raw:
            message = json.loads(raw)["entry"][0]["changes"][0]["value"]["messages"][0]
            if message["id"] != message_id:
                continue
            result = app_client.post(
                "/webhooks/whatsapp", content=raw.encode("utf-8"), headers=entry["requestHeaders"],
            )
            result.raise_for_status()
            return
    raise RuntimeError("No signed emulator webhook was available for the test message")


if __name__ == "__main__":
    raise SystemExit(main())
