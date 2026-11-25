"""Unit tests for the local Ollama agent-model adapter."""

from decimal import Decimal
import json

import httpx
import pytest

from clinicflow.agent import AgentMessage, ToolCallRequest
from clinicflow.ollama import OllamaError, OllamaModel


def test_ollama_adapter_sends_tools_and_parses_tool_call() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "search_kb",
                                "arguments": {"query": "payment methods"},
                            },
                        }
                    ],
                },
                "prompt_eval_count": 31,
                "eval_count": 6,
            },
        )

    model = OllamaModel(
        model="qwen3:4b",
        base_url="http://ollama.test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    turn = model.complete(
        (AgentMessage(role="user", content="How can I pay?"),),
        [
            {
                "name": "search_kb",
                "description": "Search clinic policy",
                "parameters": {"type": "object"},
            }
        ],
    )

    assert captured["model"] == "qwen3:4b"
    assert captured["stream"] is False
    assert captured["think"] is False
    assert captured["options"] == {"temperature": 0, "num_ctx": 4096, "num_predict": 256}
    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_kb",
                "description": "Search clinic policy",
                "parameters": {"type": "object"},
            },
        }
    ]
    assert turn.tool_calls == (
        ToolCallRequest(
            id="ollama-1",
            name="search_kb",
            arguments={"query": "payment methods"},
        ),
    )
    assert turn.usage.input_tokens == 31
    assert turn.usage.output_tokens == 6
    assert turn.usage.cost_usd == Decimal("0")


def test_ollama_adapter_serializes_tool_result_and_final_reply() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "The clinic accepts cash.",
                }
            },
        )

    model = OllamaModel(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    turn = model.complete(
        (
            AgentMessage(
                role="assistant",
                content="",
                tool_calls=(
                    ToolCallRequest(
                        id="call-1",
                        name="search_kb",
                        arguments={"query": "payment"},
                    ),
                ),
            ),
            AgentMessage(
                role="tool",
                content='{"results": []}',
                tool_call_id="call-1",
                name="search_kb",
            ),
        ),
        [],
    )

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[0]["tool_calls"][0]["function"]["name"] == "search_kb"
    assert messages[1] == {
        "role": "tool",
        "content": '{"results": []}',
        "tool_name": "search_kb",
    }
    assert turn.text == "The clinic accepts cash."
    assert turn.tool_calls == ()


def test_ollama_adapter_reports_http_failure() -> None:
    model = OllamaModel(
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, text="model failed")
            )
        )
    )

    with pytest.raises(OllamaError, match="request failed"):
        model.complete((AgentMessage(role="user", content="Hello"),), [])


def test_ollama_adapter_sends_access_credentials_on_each_chat_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://ollama.test/api/chat")
        assert request.headers["CF-Access-Client-Id"] == "test-id"
        assert request.headers["CF-Access-Client-Secret"] == "test-secret"
        return httpx.Response(200, json={"message": {"content": "Hello"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = OllamaModel(
            base_url="https://ollama.test",
            access_client_id="test-id",
            access_client_secret="test-secret",
            http_client=client,
        )
        turn = model.complete((AgentMessage(role="user", content="Hello"),), [])

    assert turn.text == "Hello"


def test_ollama_adapter_discards_reply_cut_off_by_token_limit() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "message": {"content": "The clinic accepts"},
                    "done_reason": "length",
                    "eval_count": 256,
                },
            )
        )
    ) as client:
        model = OllamaModel(http_client=client)
        turn = model.complete((AgentMessage(role="user", content="How can I pay?"),), [])

    assert turn.text is None
    assert turn.tool_calls == ()
    assert turn.usage.output_tokens == 256
