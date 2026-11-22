"""Provider-independent orchestration for the grounded clinic FAQ agent."""

from dataclasses import dataclass, field
import json
import re
from time import perf_counter
from typing import Literal, Protocol

from clinicflow.agent_tools import ClinicToolbox, ToolExecutionError

SYSTEM_PROMPT = """You are a clinic information assistant.
Use the available tools before answering factual questions.
Use only facts returned by tools; never invent services, prices, hours, doctors,
policies, or medical claims. If the answer is absent, say you do not know based
on the clinic's available information. Reply in the same language and script as
the customer. Do not diagnose conditions or recommend treatment.
"""
MAX_MODEL_ROUNDS = 4
MAX_TOOL_CALLS = 8

ReplyLanguage = Literal["en", "roman_ur", "ur"]
MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class AgentMessage:
    role: MessageRole
    content: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ModelTurn:
    text: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)


class AgentModel(Protocol):
    """The minimal contract required from any chat-model provider."""

    model_name: str

    def complete(
        self,
        messages: tuple[AgentMessage, ...],
        tools: list[dict[str, object]],
    ) -> ModelTurn:
        """Return either tool calls or a final assistant response."""


@dataclass(frozen=True)
class ToolEvent:
    call_id: str
    name: str
    arguments: dict[str, object]
    output: dict[str, object] | None
    error: str | None
    latency_ms: int


@dataclass(frozen=True)
class AgentRunResult:
    reply: str
    language: ReplyLanguage
    model_name: str
    tool_events: tuple[ToolEvent, ...]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    used_fallback: bool


def detect_reply_language(text: str) -> ReplyLanguage:
    """Choose a deterministic fallback language from the customer's script."""

    if re.search(r"[\u0600-\u06ff]", text):
        return "ur"

    words = set(re.findall(r"[a-z]+", text.casefold()))
    roman_urdu_markers = {
        "hai",
        "hain",
        "kab",
        "kya",
        "kitna",
        "kitni",
        "mujhe",
        "chahiye",
        "karna",
        "ka",
        "ki",
        "ke",
    }
    if words & roman_urdu_markers:
        return "roman_ur"
    return "en"


def fallback_reply(language: ReplyLanguage) -> str:
    """Return the grounded don't-know response in the detected language."""

    if language == "ur":
        return "مجھے دستیاب کلینک کی معلومات میں اس کا جواب نہیں ملا۔"
    if language == "roman_ur":
        return "Mujhe clinic ki available maloomat mein iska jawab nahi mila."
    return "I couldn't find that in the clinic information available to me."


def run_faq_agent(
    *,
    question: str,
    tenant_id: int,
    model: AgentModel,
    toolbox: ClinicToolbox | None = None,
) -> AgentRunResult:
    """Run a bounded tool-use loop and return a trace-ready result."""

    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("question cannot be empty")

    language = detect_reply_language(normalized_question)
    active_toolbox = toolbox or ClinicToolbox(tenant_id)
    messages = [
        AgentMessage(role="system", content=SYSTEM_PROMPT),
        AgentMessage(role="user", content=normalized_question),
    ]
    tool_events: list[ToolEvent] = []
    input_tokens = 0
    output_tokens = 0
    has_evidence = False
    run_started = perf_counter()

    for _ in range(MAX_MODEL_ROUNDS):
        turn = model.complete(tuple(messages), active_toolbox.definitions())
        input_tokens += turn.usage.input_tokens
        output_tokens += turn.usage.output_tokens

        if turn.tool_calls:
            if len(tool_events) + len(turn.tool_calls) > MAX_TOOL_CALLS:
                break
            messages.append(
                AgentMessage(
                    role="assistant",
                    content=turn.text or "",
                    tool_calls=turn.tool_calls,
                )
            )
            for call in turn.tool_calls:
                tool_started = perf_counter()
                try:
                    output = active_toolbox.execute(call.name, call.arguments)
                except ToolExecutionError as error:
                    error_text = str(error)
                    output = None
                else:
                    error_text = None
                    has_evidence = has_evidence or _contains_evidence(output)

                latency_ms = round((perf_counter() - tool_started) * 1000)
                event = ToolEvent(
                    call_id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                    output=output,
                    error=error_text,
                    latency_ms=latency_ms,
                )
                tool_events.append(event)
                tool_payload: dict[str, object]
                if error_text is not None:
                    tool_payload = {"error": error_text}
                else:
                    tool_payload = output or {}
                messages.append(
                    AgentMessage(
                        role="tool",
                        content=json.dumps(
                            tool_payload,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
            continue

        if turn.text and turn.text.strip() and has_evidence:
            return _result(
                reply=turn.text.strip(),
                language=language,
                model=model,
                tool_events=tool_events,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                run_started=run_started,
                used_fallback=False,
            )
        break

    return _result(
        reply=fallback_reply(language),
        language=language,
        model=model,
        tool_events=tool_events,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        run_started=run_started,
        used_fallback=True,
    )


def _contains_evidence(output: dict[str, object]) -> bool:
    services = output.get("services")
    if isinstance(services, list) and services:
        return True
    results = output.get("results")
    if isinstance(results, list) and results:
        return True
    if output.get("profile") is not None:
        return True
    return False


def _result(
    *,
    reply: str,
    language: ReplyLanguage,
    model: AgentModel,
    tool_events: list[ToolEvent],
    input_tokens: int,
    output_tokens: int,
    run_started: float,
    used_fallback: bool,
) -> AgentRunResult:
    return AgentRunResult(
        reply=reply,
        language=language,
        model_name=model.model_name,
        tool_events=tuple(tool_events),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=round((perf_counter() - run_started) * 1000),
        used_fallback=used_fallback,
    )
