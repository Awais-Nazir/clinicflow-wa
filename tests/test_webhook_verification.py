"""Tests for Meta's webhook verification handshake."""

import pytest
from fastapi.testclient import TestClient

from clinicflow.main import app

client = TestClient(app)
WEBHOOK_URL = "/webhooks/whatsapp"


def verification_params(**overrides: str) -> dict[str, str]:
    params = {
        "hub.mode": "subscribe",
        "hub.challenge": "1158201444",
        "hub.verify_token": "test-verify-token",
    }
    params.update(overrides)
    return params


def test_returns_challenge_for_valid_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("META_VERIFY_TOKEN", "test-verify-token")

    response = client.get(WEBHOOK_URL, params=verification_params())

    assert response.status_code == 200
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


def test_rejects_wrong_verify_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_VERIFY_TOKEN", "test-verify-token")

    response = client.get(
        WEBHOOK_URL,
        params=verification_params(**{"hub.verify_token": "wrong-token"}),
    )

    assert response.status_code == 403


def test_rejects_wrong_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("META_VERIFY_TOKEN", "test-verify-token")

    response = client.get(
        WEBHOOK_URL,
        params=verification_params(**{"hub.mode": "unsubscribe"}),
    )

    assert response.status_code == 403


def test_reports_missing_server_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("META_VERIFY_TOKEN", raising=False)

    response = client.get(WEBHOOK_URL, params=verification_params())

    assert response.status_code == 503
