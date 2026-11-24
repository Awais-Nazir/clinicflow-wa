"""Provision a second clinic and verify its boundaries on the same database."""

from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from clinicflow.booking import list_booking_slots
from clinicflow.conversations import get_or_create_conversation
from clinicflow.credentials import CredentialError, resolve_access_token
from clinicflow.database import session_scope
from clinicflow.knowledge import hybrid_search_kb, search_kb
from clinicflow.main import app
from clinicflow.models import HandoffCase, ProviderService, Tenant, WhatsAppPhoneNumber
from clinicflow.onboarding import TenantManifest, onboard_tenant
from clinicflow.seed_booking import seed_demo_booking
from clinicflow.tenants import get_tenant_by_phone_number_id, seed_demo_clinic
from clinicflow.whatsapp import WhatsAppClient

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="DATABASE_URL is required for PostgreSQL"
)


def test_second_tenant_scopes_bookings_knowledge_credentials_and_conversations(monkeypatch):
    tag = uuid4().hex
    moon_phone = "noor-" + tag
    noor_id, _ = seed_demo_booking(moon_phone)
    manifest = TenantManifest.model_validate_json(
        (Path(__file__).parents[1] / "examples" / "second-clinic.json").read_bytes()
    ).model_copy(update={"slug": "mehr-" + tag, "phone_number_id": "mehr-phone-" + tag})
    monkeypatch.setenv("CLINICFLOW_TOKEN_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", moon_phone)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "noor-synthetic-token")
    second_id = onboard_tenant(
        manifest, whatsapp_token="mehr-synthetic-token", staff_token="staff-" + tag,
    )
    assert onboard_tenant(manifest) == second_id
    assert resolve_access_token(manifest.phone_number_id) == "mehr-synthetic-token"
    with pytest.raises(CredentialError):
        resolve_access_token("unconfigured-number")

    with session_scope() as session:
        phone = session.scalar(select(WhatsAppPhoneNumber).where(
            WhatsAppPhoneNumber.phone_number_id == manifest.phone_number_id
        ))
        assert phone.access_token_ciphertext != "mehr-synthetic-token"
        assert "mehr-synthetic-token" not in phone.access_token_ciphertext
        assert session.get(Tenant, second_id).reply_tone == "formal"
        offering = session.scalar(select(ProviderService).where(ProviderService.tenant_id == second_id))

    customer = "shared-customer-" + tag
    noor_conversation = get_or_create_conversation(noor_id, customer).id
    mehr_conversation = get_or_create_conversation(second_id, customer).id
    assert noor_conversation != mehr_conversation
    with session_scope() as session:
        noor_case = HandoffCase(tenant_id=noor_id, conversation_id=noor_conversation, reason="test")
        mehr_case = HandoffCase(tenant_id=second_id, conversation_id=mehr_conversation, reason="test")
        session.add_all([noor_case, mehr_case])
    mehr_headers = {"Authorization": "Bearer staff-" + tag}
    with TestClient(app) as client:
        assert client.get(f"/staff/handoffs/{mehr_case.id}", headers=mehr_headers).status_code == 200
        assert client.get(f"/staff/handoffs/{noor_case.id}", headers=mehr_headers).status_code == 404
        assert client.get(
            f"/staff/conversations/{noor_conversation}/traces", headers=mehr_headers,
        ).status_code == 404
    assert search_kb(noor_id, "cedar courtyard") == []
    assert hybrid_search_kb(second_id, "cedar courtyard")[0].source_key == "mehr-courtyard"
    assert all(
        result.source_key != "mehr-courtyard"
        for result in hybrid_search_kb(noor_id, "cedar courtyard")
    )
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    day = now.replace(day=26).date()
    assert not list_booking_slots(noor_id, offering.provider_id, offering.service_id, day, now=now)
    assert list_booking_slots(second_id, offering.provider_id, offering.service_id, day, now=now)

    def graph(request: httpx.Request) -> httpx.Response:
        phone_id = request.url.path.split("/")[-2]
        expected = "noor-synthetic-token" if phone_id == moon_phone else "mehr-synthetic-token"
        assert request.headers["Authorization"] == f"Bearer {expected}"
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{phone_id}"}]})

    with httpx.Client(transport=httpx.MockTransport(graph)) as transport:
        sender = WhatsAppClient(None, http_client=transport, token_resolver=resolve_access_token)
        for number in (moon_phone, manifest.phone_number_id):
            assert sender.send_text(phone_number_id=number, recipient_id=customer, body="Hello")

    with pytest.raises(ValueError, match="already belongs"):
        onboard_tenant(manifest.model_copy(update={"slug": "other-" + tag}))
    with pytest.raises(ValueError, match="already belongs"):
        seed_demo_clinic(manifest.phone_number_id)
    assert get_tenant_by_phone_number_id(manifest.phone_number_id).id == second_id
