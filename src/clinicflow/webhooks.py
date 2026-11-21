"""WhatsApp webhook HTTP routes."""

import hashlib
import hmac
import json
import logging
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from clinicflow.events import extract_failed_deliveries, extract_text_messages
from clinicflow.inbox import enqueue_text_messages

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
LOGGER = logging.getLogger(__name__)


def has_valid_signature(
    raw_body: bytes,
    signature_header: str,
    app_secret: str,
) -> bool:
    """Check Meta's HMAC-SHA256 signature against the original request bytes."""

    digest = hmac.new(
        key=app_secret.encode(),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    expected_signature = f"sha256={digest}"
    return secrets.compare_digest(signature_header, expected_signature)


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    mode: Annotated[str, Query(alias="hub.mode")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
    verify_token: Annotated[str, Query(alias="hub.verify_token")],
) -> PlainTextResponse:
    """Complete Meta's webhook verification challenge."""

    expected_token = os.getenv("META_VERIFY_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook verification is not configured",
        )

    token_matches = secrets.compare_digest(verify_token, expected_token)
    if mode != "subscribe" or not token_matches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed",
        )

    return PlainTextResponse(content=challenge)


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def receive_whatsapp_webhook(
    request: Request,
    signature: Annotated[
        str | None,
        Header(alias="X-Hub-Signature-256"),
    ] = None,
) -> Response:
    """Authenticate and acknowledge a Meta webhook event."""

    app_secret = os.getenv("META_APP_SECRET")
    if not app_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook signature verification is not configured",
        )

    raw_body = await request.body()
    if signature is None or not has_valid_signature(raw_body, signature, app_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    try:
        payload: object = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from error

    messages = extract_text_messages(payload)
    if messages and isinstance(payload, dict):
        await run_in_threadpool(enqueue_text_messages, messages, payload)

    for failure in extract_failed_deliveries(payload):
        LOGGER.error(
            "WhatsApp delivery failed message_id=%s code=%s detail=%s",
            failure.message_id,
            failure.error_code,
            failure.detail,
        )

    return Response(status_code=status.HTTP_200_OK)
