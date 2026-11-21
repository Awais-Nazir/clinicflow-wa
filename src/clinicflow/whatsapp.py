"""Small client for the WhatsApp Cloud API."""

import os
from types import TracebackType
from typing import Any, Self

import httpx

DEFAULT_GRAPH_API_VERSION = "v27.0"
DEFAULT_GRAPH_API_BASE_URL = "https://graph.facebook.com"
DEFAULT_TIMEOUT_SECONDS = 20.0


class WhatsAppAPIError(RuntimeError):
    """Raised when Meta cannot accept or confirm an outbound message."""


class WhatsAppClient:
    """Send WhatsApp messages through Meta's versioned Graph API."""

    def __init__(
        self,
        access_token: str,
        *,
        api_version: str = DEFAULT_GRAPH_API_VERSION,
        base_url: str = DEFAULT_GRAPH_API_BASE_URL,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("access_token cannot be empty")

        self._access_token = access_token
        self._api_version = api_version.strip("/")
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS
        )
        self._owns_http_client = http_client is None

    @classmethod
    def from_environment(cls) -> Self:
        """Build a client from Phase 0's single Meta access token."""

        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        if not access_token:
            raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not configured")

        return cls(
            access_token,
            api_version=os.getenv(
                "META_GRAPH_API_VERSION",
                DEFAULT_GRAPH_API_VERSION,
            ),
        )

    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        """Send text and return the provider's WhatsApp message ID."""

        url = (
            f"{self._base_url}/{self._api_version}/"
            f"{phone_number_id}/messages"
        )
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": body,
            },
        }

        try:
            response = self._http_client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.RequestError as exc:
            raise WhatsAppAPIError("Could not reach the WhatsApp Graph API") from exc

        if response.is_error:
            raise WhatsAppAPIError(_format_graph_error(response))

        try:
            response_data = response.json()
            provider_message_id = response_data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WhatsAppAPIError(
                "WhatsApp Graph API response did not contain a message ID"
            ) from exc

        if not isinstance(provider_message_id, str) or not provider_message_id:
            raise WhatsAppAPIError(
                "WhatsApp Graph API returned an invalid message ID"
            )

        return provider_message_id

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _format_graph_error(response: httpx.Response) -> str:
    """Extract useful Meta error details without logging credentials."""

    details: dict[str, Any] = {}
    try:
        response_data = response.json()
        if isinstance(response_data, dict):
            candidate = response_data.get("error")
            if isinstance(candidate, dict):
                details = candidate
    except ValueError:
        pass

    message = details.get("message", "request rejected")
    code = details.get("code")
    code_suffix = f", code {code}" if code is not None else ""
    return f"WhatsApp Graph API returned HTTP {response.status_code}{code_suffix}: {message}"
