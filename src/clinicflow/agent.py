"""Provider-independent orchestration for the grounded clinic FAQ agent."""

from dataclasses import dataclass, field, replace
from decimal import Decimal
import json
import re
from time import perf_counter
from typing import Literal, Protocol

from clinicflow.agent_tools import ClinicToolbox, ToolExecutionError
from clinicflow.faq_evidence import (
    is_named_price_question,
    render_service_price,
    scope_faq_evidence,
)
from clinicflow.grounding import check_numeric_grounding

NO_CLINIC_ANSWER = "NO_CLINIC_ANSWER"
SYSTEM_PROMPT = f"""You answer clinic FAQ messages. For a clinic question,
your first response must call the relevant tool:
- get_services for treatments offered, prices, or treatment durations.
- get_clinic_info for hours, location, contact details, or doctors.
- search_kb for policies and FAQs such as cancellation, payments, parking,
  or what help the clinic offers over WhatsApp. Pass short English keywords in
  the query argument.
The two list tools take no arguments. If a tool reports invalid arguments,
retry the call with the correct arguments.

After the tool returns, answer the specific question briefly in the customer's
language and script. Use only the relevant facts in the result. Copy numeric
values as given, using local digits if appropriate. Use plain bullets for lists.
A result on a related topic
or a list that omits the requested person or treatment is insufficient. If the
result does not answer the question, output only {NO_CLINIC_ANSWER}. Never
diagnose or recommend treatment.
"""
PROMPT_VERSION = "faq-v4"
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
    cost_usd: Decimal = Decimal("0")


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


class AgentModelError(RuntimeError):
    """The configured model could not complete its request."""


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
    cost_usd: Decimal
    latency_ms: int
    used_fallback: bool
    guardrail_reason: str | None = None
    unsupported_numbers: tuple[str, ...] = ()
    trace_id: int | None = None


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


def _model_unavailable_reply(language: ReplyLanguage) -> str:
    if language == "ur":
        return "میں ابھی کلینک کی معلومات چیک نہیں کر سکا۔ براہ کرم تھوڑی دیر بعد دوبارہ کوشش کریں۔"
    if language == "roman_ur":
        return "Abhi clinic ki maloomat check nahi ho saki. Thori dair baad dobara try karein."
    return "I couldn't check the clinic information right now. Please try again shortly."


def run_faq_agent(
    *,
    question: str,
    tenant_id: int,
    model: AgentModel,
    toolbox: ClinicToolbox | None = None,
    conversation_id: int | None = None,
    persist_trace: bool = True,
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
    cost_usd = Decimal("0")
    has_evidence = False
    fallback_reason = "no_grounding_evidence"
    unsupported_numbers: tuple[str, ...] = ()
    grounded_reply: str | None = None
    run_started = perf_counter()

    for _ in range(MAX_MODEL_ROUNDS):
        try:
            turn = model.complete(tuple(messages), active_toolbox.definitions())
        except AgentModelError:
            fallback_reason = "model_unavailable"
            break
        input_tokens += turn.usage.input_tokens
        output_tokens += turn.usage.output_tokens
        cost_usd += turn.usage.cost_usd

        if turn.tool_calls:
            if len(tool_events) + len(turn.tool_calls) > MAX_TOOL_CALLS:
                fallback_reason = "tool_limit_exceeded"
                break
            messages.append(
                AgentMessage(
                    role="assistant",
                    content=turn.text or "",
                    tool_calls=turn.tool_calls,
                )
            )
            missing_record = False
            for call in turn.tool_calls:
                tool_started = perf_counter()
                try:
                    output = active_toolbox.execute(call.name, call.arguments)
                except ToolExecutionError as error:
                    error_text = str(error)
                    output = None
                else:
                    error_text = None
                    scoped = scope_faq_evidence(normalized_question, call.name, output)
                    if scoped is None:
                        missing_record = True
                        output = {"missing_requested_record": True}
                    else:
                        output = scoped
                        if call.name == "get_services" and is_named_price_question(
                            normalized_question
                        ):
                            grounded_reply = render_service_price(output)
                            missing_record = missing_record or grounded_reply is None
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
            if missing_record:
                grounded_reply = None
                fallback_reason = "answer_not_in_clinic_data"
                break
            if grounded_reply is not None:
                break
            continue

        proposed_reply = (turn.text or "").strip()
        if proposed_reply == NO_CLINIC_ANSWER:
            fallback_reason = "answer_not_in_clinic_data"
            break

        if proposed_reply and has_evidence:
            numeric_check = check_numeric_grounding(
                proposed_reply,
                [
                    event.output
                    for event in tool_events
                    if event.output is not None
                ],
            )
            if not numeric_check.is_supported:
                fallback_reason = "unsupported_numeric_facts"
                unsupported_numbers = numeric_check.unsupported_numbers
                break
            result = _result(
                reply=proposed_reply,
                language=language,
                model=model,
                tool_events=tool_events,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                run_started=run_started,
                used_fallback=False,
                guardrail_reason=None,
                unsupported_numbers=(),
            )
            return _persist_result(
                result=result,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                question=normalized_question,
                persist_trace=persist_trace,
            )
        break
    else:
        fallback_reason = "model_round_limit"

    result = _result(
        reply=grounded_reply or (
            _model_unavailable_reply(language)
            if fallback_reason == "model_unavailable"
            else fallback_reply(language)
        ),
        language=language,
        model=model,
        tool_events=tool_events,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        run_started=run_started,
        used_fallback=grounded_reply is None,
        guardrail_reason=fallback_reason if grounded_reply is None else None,
        unsupported_numbers=unsupported_numbers,
    )
    return _persist_result(
        result=result,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        question=normalized_question,
        persist_trace=persist_trace,
    )


def _contains_evidence(output: dict[str, object]) -> bool:
    return output.get("profile") is not None or any(
        isinstance(output.get(key), list) and bool(output[key])
        for key in ("services", "doctors", "opening_hours", "results")
    )


def _result(
    *,
    reply: str,
    language: ReplyLanguage,
    model: AgentModel,
    tool_events: list[ToolEvent],
    input_tokens: int,
    output_tokens: int,
    cost_usd: Decimal,
    run_started: float,
    used_fallback: bool,
    guardrail_reason: str | None,
    unsupported_numbers: tuple[str, ...],
) -> AgentRunResult:
    return AgentRunResult(
        reply=reply,
        language=language,
        model_name=model.model_name,
        tool_events=tuple(tool_events),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        latency_ms=round((perf_counter() - run_started) * 1000),
        used_fallback=used_fallback,
        guardrail_reason=guardrail_reason,
        unsupported_numbers=unsupported_numbers,
    )


def _persist_result(
    *,
    result: AgentRunResult,
    tenant_id: int,
    conversation_id: int | None,
    question: str,
    persist_trace: bool,
) -> AgentRunResult:
    if not persist_trace:
        return result

    from clinicflow.agent_traces import save_agent_run

    trace_id = save_agent_run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        prompt_version=PROMPT_VERSION,
        question=question,
        result=result,
    )
    return replace(result, trace_id=trace_id)
