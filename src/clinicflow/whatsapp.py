"""Small client for the WhatsApp Cloud API."""

import os
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

import httpx

from clinicflow.credentials import CredentialError, resolve_access_token
from clinicflow.voice import VoiceError

DEFAULT_GRAPH_API_VERSION = "v27.0"
DEFAULT_GRAPH_API_BASE_URL = "https://graph.facebook.com"
DEFAULT_TIMEOUT_SECONDS = 20.0


class WhatsAppAPIError(RuntimeError):
    """Raised when Meta cannot accept or confirm an outbound message."""


class WhatsAppRetryableError(WhatsAppAPIError):
    """A send failed before acceptance or received a retryable response."""


class WhatsAppRejectedError(WhatsAppAPIError):
    """Meta definitively rejected the outbound message."""


class WhatsAppUncertainError(WhatsAppAPIError):
    """The request may have succeeded, so an automatic retry is unsafe."""


class WhatsAppClient:
    """Send WhatsApp messages through Meta's versioned Graph API."""

    def __init__(
        self,
        access_token: str | None,
        *,
        api_version: str = DEFAULT_GRAPH_API_VERSION,
        base_url: str = DEFAULT_GRAPH_API_BASE_URL,
        http_client: httpx.Client | None = None,
        token_resolver: Callable[[str], str] | None = None,
        allow_local_media: bool = False,
    ) -> None:
        if not access_token and token_resolver is None:
            raise ValueError("access_token cannot be empty")

        self._access_token = access_token
        self._token_resolver = token_resolver
        self._allow_local_media = allow_local_media
        self._api_version = api_version.strip("/")
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client or httpx.Client(
            timeout=DEFAULT_TIMEOUT_SECONDS
        )
        self._owns_http_client = http_client is None

    @classmethod
    def from_environment(cls) -> Self:
        """Use tenant credentials, or an explicit loopback emulator with a fake token."""

        api_version = os.getenv("META_GRAPH_API_VERSION", DEFAULT_GRAPH_API_VERSION)
        base_url = os.getenv("META_GRAPH_API_BASE_URL", DEFAULT_GRAPH_API_BASE_URL).rstrip("/")
        if base_url != DEFAULT_GRAPH_API_BASE_URL:
            base = httpx.URL(base_url)
            if (
                base.host not in {"localhost", "127.0.0.1", "::1"}
                or base.scheme not in {"http", "https"}
                or base.userinfo or base.path != "/" or base.query or base.fragment
            ):
                raise ValueError("Graph API override must be a loopback emulator URL")
            return cls(
                "clinicflow-emulator-only", base_url=base_url,
                api_version=api_version, allow_local_media=True,
            )
        return cls(None, api_version=api_version, token_resolver=resolve_access_token)

    def send_text(
        self,
        *,
        phone_number_id: str,
        recipient_id: str,
        body: str,
    ) -> str:
        """Send text and return the provider's WhatsApp message ID."""

        return self._send_message(phone_number_id, recipient_id, {
            "type": "text", "text": {"preview_url": False, "body": body},
        })

    def send_buttons(
        self, *, phone_number_id: str, recipient_id: str, body: str,
        buttons: list[dict[str, str]],
    ) -> str:
        """Send a session reply with actions bound to the offered draft."""

        return self._send_message(phone_number_id, recipient_id, {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {"buttons": [{"type": "reply", "reply": item} for item in buttons]},
            },
        })

    def _send_message(
        self, phone_number_id: str, recipient_id: str, message: dict[str, object],
    ) -> str:
        url = (
            f"{self._base_url}/{self._api_version}/"
            f"{phone_number_id}/messages"
        )
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            **message,
        }

        try:
            response = self._http_client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._token_for_phone(phone_number_id)}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise WhatsAppRetryableError(
                "Could not connect to the WhatsApp Graph API"
            ) from exc
        except httpx.RequestError as exc:
            raise WhatsAppUncertainError(
                "WhatsApp Graph API request ended with an unknown delivery outcome"
            ) from exc

        if response.is_error:
            error_message = _format_graph_error(response)
            if response.status_code == 408 or response.status_code == 429:
                raise WhatsAppRetryableError(error_message)
            if response.status_code >= 500:
                raise WhatsAppRetryableError(error_message)
            raise WhatsAppRejectedError(error_message)

        try:
            response_data = response.json()
            provider_message_id = response_data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WhatsAppUncertainError(
                "WhatsApp Graph API response did not contain a message ID"
            ) from exc

        if not isinstance(provider_message_id, str) or not provider_message_id:
            raise WhatsAppUncertainError(
                "WhatsApp Graph API returned an invalid message ID"
            )

        return provider_message_id

    def download_audio(
        self, *, phone_number_id: str, media_id: str, max_bytes: int,
    ) -> bytes:
        """Resolve a media ID scoped to this phone, then download bounded audio."""

        if not media_id.isascii() or not media_id.isdecimal() or max_bytes < 1:
            raise VoiceError("invalid audio reference")
        try:
            headers = {"Authorization": f"Bearer {self._token_for_phone(phone_number_id)}"}
            metadata = self._http_client.get(
                f"{self._base_url}/{self._api_version}/{media_id}",
                params={"phone_number_id": phone_number_id},
                headers=headers,
                follow_redirects=False,
            )
            metadata.raise_for_status()
            details = metadata.json()
            if not isinstance(details, dict):
                raise VoiceError("invalid audio metadata")
            size = details.get("file_size")
            if size is not None and int(size) > max_bytes:
                raise VoiceError("audio exceeds the size limit")
            raw_url = details.get("url")
            if not isinstance(raw_url, str):
                raise VoiceError("missing audio URL")
            url = httpx.URL(raw_url)
            base = httpx.URL(self._base_url)
            same_origin = (url.scheme, url.host, url.port) == (base.scheme, base.host, base.port)
            if not _authorized_media_url(url) and not (
                self._allow_local_media and same_origin and not url.userinfo
            ):
                raise VoiceError("unrecognized WhatsApp media host")
            with self._http_client.stream(
                "GET", url, headers=headers, follow_redirects=False,
            ) as response:
                response.raise_for_status()
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if not (mime.startswith("audio/") or mime in {"application/ogg", "application/octet-stream"}):
                    raise VoiceError("unsupported audio content type")
                audio = bytearray()
                for chunk in response.iter_bytes(chunk_size=16384):
                    if len(audio) + len(chunk) > max_bytes:
                        raise VoiceError("audio exceeds the size limit")
                    audio.extend(chunk)
                return bytes(audio)
        except (WhatsAppAPIError, httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError) as error:
            raise VoiceError("could not retrieve WhatsApp audio") from error

    def _token_for_phone(self, phone_number_id: str) -> str:
        if self._token_resolver is None:
            assert self._access_token is not None
            return self._access_token
        try:
            return self._token_resolver(phone_number_id)
        except CredentialError as error:
            raise WhatsAppRejectedError("WhatsApp credentials are unavailable for this number") from error

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


def _authorized_media_url(url: httpx.URL) -> bool:
    host = url.host.lower()
    return (
        url.scheme == "https" and not url.userinfo and url.port in {None, 443}
        and (
            host == "lookaside.fbsbx.com"
            or host.endswith((".whatsapp.net", ".fbcdn.net"))
        )
    )
