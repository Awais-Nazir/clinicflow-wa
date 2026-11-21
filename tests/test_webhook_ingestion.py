"""Tests for authenticated Meta webhook ingestion."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from clinicflow.main import app

APP_SECRET = "test-app-secret"
WEBHOOK_URL = "/webhooks/whatsapp"
client = TestClient(app)


def make_signature(raw_body: bytes, secret: str = APP_SECRET) -> str:
    digest = hmac.new(
        key=secret.encode(),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def make_body() -> bytes:
    payload = {"object": "whatsapp_business_account", "entry": []}
    return json.dumps(payload, separators=(",", ":")).encode()


def test_accepts_payload_with_valid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    raw_body = make_body()

    response = client.post(
        WEBHOOK_URL,
        content=raw_body,
        headers={"X-Hub-Signature-256": make_signature(raw_body)},
    )

    assert response.status_code == 200


def test_rejects_payload_with_invalid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    raw_body = make_body()

    response = client.post(
        WEBHOOK_URL,
        content=raw_body,
        headers={"X-Hub-Signature-256": make_signature(b"different-body")},
    )

    assert response.status_code == 401


def test_rejects_payload_without_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)

    response = client.post(WEBHOOK_URL, content=make_body())

    assert response.status_code == 401


def test_rejects_malformed_json_after_validating_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    raw_body = b"not-json"

    response = client.post(
        WEBHOOK_URL,
        content=raw_body,
        headers={"X-Hub-Signature-256": make_signature(raw_body)},
    )

    assert response.status_code == 400


def test_reports_missing_app_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("META_APP_SECRET", raising=False)
    raw_body = make_body()

    response = client.post(
        WEBHOOK_URL,
        content=raw_body,
        headers={"X-Hub-Signature-256": make_signature(raw_body)},
    )

    assert response.status_code == 503
