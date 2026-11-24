"""Ollama adapter for the provider-independent FAQ agent interface."""

from decimal import Decimal
import json
import os
from typing import cast

import httpx

from clinicflow.agent import (
    AgentMessage,
    AgentModelError,
    ModelTurn,
    TokenUsage,
    ToolCallRequest,
)

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS = 256
OLLAMA_CONTEXT_TOKENS = 4096
OLLAMA_MAX_GENERATION_TOKENS = 1024


class OllamaError(AgentModelError):
    """An invalid or unsuccessful response from the local Ollama server."""


class OllamaModel:
    """Call Ollama's local chat API with ClinicFlow's tool definitions."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        max_output_tokens: int = DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS,
        http_client: httpx.Client | None = None,
    ) -> None:
        normalized_model = model.strip()
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_model:
            raise ValueError("Ollama model cannot be empty")
        if not normalized_base_url:
            raise ValueError("Ollama base URL cannot be empty")
        if not 1 <= max_output_tokens <= OLLAMA_MAX_GENERATION_TOKENS:
            raise ValueError(f"Ollama max output tokens must be between 1 and {OLLAMA_MAX_GENERATION_TOKENS}")

        self.model_name = normalized_model
        self._max_output_tokens = max_output_tokens
        self._chat_url = f"{normalized_base_url}/api/chat"
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.Client(
            timeout=httpx.Timeout(180.0, connect=5.0),
        )
        self._tool_call_sequence = 0

    @classmethod
    def from_env(cls) -> "OllamaModel":
        """Build an adapter from optional local-development environment vars."""

        return cls(
            model=os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            base_url=os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            max_output_tokens=_env_positive_int(
                "OLLAMA_MAX_OUTPUT_TOKENS",
                DEFAULT_OLLAMA_MAX_OUTPUT_TOKENS,
            ),
        )

    def complete(
        self,
        messages: tuple[AgentMessage, ...],
        tools: list[dict[str, object]],
    ) -> ModelTurn:
        """Generate one non-streaming turn, including any requested tools."""

        payload = {
            "model": self.model_name,
            "messages": [_serialize_message(message) for message in messages],
            "tools": [
                {"type": "function", "function": definition}
                for definition in tools
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_ctx": OLLAMA_CONTEXT_TOKENS,
                "num_predict": self._max_output_tokens,
            },
        }
        try:
            response = self._http_client.post(self._chat_url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise OllamaError(f"Ollama request failed: {error}") from error

        try:
            body = response.json()
        except json.JSONDecodeError as error:
            raise OllamaError("Ollama returned invalid JSON") from error
        if not isinstance(body, dict):
            raise OllamaError("Ollama response must be a JSON object")

        usage = TokenUsage(
            input_tokens=_non_negative_int(body.get("prompt_eval_count")),
            output_tokens=_non_negative_int(body.get("eval_count")),
            cost_usd=Decimal("0"),
        )
        if body.get("done_reason") == "length":
            return ModelTurn(usage=usage)

        message = body.get("message")
        if not isinstance(message, dict):
            raise OllamaError("Ollama response is missing message")

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise OllamaError("Ollama message content must be a string")

        tool_calls = self._parse_tool_calls(message.get("tool_calls", []))
        return ModelTurn(
            text=content,
            tool_calls=tool_calls,
            usage=usage,
        )

    def close(self) -> None:
        """Close the internally created HTTP client."""

        if self._owns_client:
            self._http_client.close()

    def _parse_tool_calls(self, value: object) -> tuple[ToolCallRequest, ...]:
        if not isinstance(value, list):
            raise OllamaError("Ollama tool_calls must be a list")

        calls: list[ToolCallRequest] = []
        for item in value:
            if not isinstance(item, dict):
                raise OllamaError("Ollama tool call must be an object")
            function = item.get("function")
            if not isinstance(function, dict):
                raise OllamaError("Ollama tool call is missing function")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise OllamaError("Ollama tool call is missing function name")
            arguments = _parse_arguments(function.get("arguments", {}))
            self._tool_call_sequence += 1
            calls.append(
                ToolCallRequest(
                    id=f"ollama-{self._tool_call_sequence}",
                    name=name,
                    arguments=arguments,
                )
            )
        return tuple(calls)


def _serialize_message(message: AgentMessage) -> dict[str, object]:
    serialized: dict[str, object] = {
        "role": message.role,
        "content": message.content,
    }
    if message.tool_calls:
        serialized["tool_calls"] = [
            {
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments,
                },
            }
            for call in message.tool_calls
        ]
    if message.role == "tool" and message.name is not None:
        serialized["tool_name"] = message.name
    return serialized


def _parse_arguments(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise OllamaError("Ollama tool arguments contain invalid JSON") from error
        if isinstance(decoded, dict):
            return cast(dict[str, object], decoded)
    raise OllamaError("Ollama tool arguments must be an object")


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value
