"""WhatsApp webhook HTTP routes."""

import os
import secrets
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    mode: Annotated[str, Query(alias="hub.mode")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
    verify_token: Annotated[str, Query(alias="hub.verify_token")],
) -> PlainTextResponse:
    """Complete Meta's webhook verification challenge."""

    expected_token = os.getenv("META_VERIFY_TOKEN")
    if expected_token is None:
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
