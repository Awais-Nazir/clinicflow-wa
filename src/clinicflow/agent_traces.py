"""Persistence and tenant-scoped reads for FAQ-agent execution traces."""

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from clinicflow.database import session_scope
from clinicflow.models import AgentRun

if TYPE_CHECKING:
    from clinicflow.agent import AgentRunResult


@dataclass(frozen=True)
class AgentRunTrace:
    id: int
    tenant_id: int
    conversation_id: int | None
    prompt_version: str
    model_name: str
    status: str
    guardrail_reason: str | None
    unsupported_numbers: list[str]
    question: str
    reply: str
    tool_events: list[dict[str, object]]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal


def save_agent_run(
    *,
    tenant_id: int,
    conversation_id: int | None,
    prompt_version: str,
    question: str,
    result: "AgentRunResult",
) -> int:
    """Persist one completed or fallback run and return its trace ID."""

    serialized_events = [
        {
            "call_id": event.call_id,
            "name": event.name,
            "arguments": event.arguments,
            "output": event.output,
            "error": event.error,
            "latency_ms": event.latency_ms,
        }
        for event in result.tool_events
    ]
    statement = (
        insert(AgentRun)
        .values(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            prompt_version=prompt_version,
            model_name=result.model_name,
            status="fallback" if result.used_fallback else "completed",
            guardrail_reason=result.guardrail_reason,
            unsupported_numbers=list(result.unsupported_numbers),
            question=question,
            reply=result.reply,
            tool_events=serialized_events,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
        )
        .returning(AgentRun.id)
    )

    with session_scope() as session:
        run_id = session.scalar(statement)

    if run_id is None:
        raise RuntimeError("Failed to persist agent run")
    return run_id


def get_agent_run(tenant_id: int, run_id: int) -> AgentRunTrace | None:
    """Read one trace without crossing the tenant boundary."""

    statement = select(
        AgentRun.id,
        AgentRun.tenant_id,
        AgentRun.conversation_id,
        AgentRun.prompt_version,
        AgentRun.model_name,
        AgentRun.status,
        AgentRun.guardrail_reason,
        AgentRun.unsupported_numbers,
        AgentRun.question,
        AgentRun.reply,
        AgentRun.tool_events,
        AgentRun.input_tokens,
        AgentRun.output_tokens,
        AgentRun.latency_ms,
        AgentRun.cost_usd,
    ).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)

    with session_scope() as session:
        row = session.execute(statement).one_or_none()

    if row is None:
        return None
    return AgentRunTrace(
        id=row.id,
        tenant_id=row.tenant_id,
        conversation_id=row.conversation_id,
        prompt_version=row.prompt_version,
        model_name=row.model_name,
        status=row.status,
        guardrail_reason=row.guardrail_reason,
        unsupported_numbers=row.unsupported_numbers,
        question=row.question,
        reply=row.reply,
        tool_events=row.tool_events,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        latency_ms=row.latency_ms,
        cost_usd=row.cost_usd,
    )
