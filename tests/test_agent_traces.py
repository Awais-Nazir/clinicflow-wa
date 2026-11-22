"""PostgreSQL integration tests for durable FAQ-agent run traces."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.agent import (
    AgentMessage,
    ModelTurn,
    TokenUsage,
    ToolCallRequest,
    run_faq_agent,
)
from clinicflow.agent_traces import get_agent_run
from clinicflow.conversations import get_or_create_conversation
from clinicflow.tenants import seed_demo_clinic

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    if DATABASE_URL is None:
        pytest.skip("DATABASE_URL is required")

    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE tenants RESTART IDENTITY CASCADE"))

    yield engine
    engine.dispose()


class TraceModel:
    model_name = "trace-test-model"

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = turns

    def complete(
        self,
        messages: tuple[AgentMessage, ...],
        tools: list[dict[str, object]],
    ) -> ModelTurn:
        return self.turns.pop(0)


def test_completed_run_persists_full_tenant_scoped_trace(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    conversation = get_or_create_conversation(tenant.id, "923001234567")
    model = TraceModel(
        [
            ModelTurn(
                tool_calls=(
                    ToolCallRequest(
                        id="services-1",
                        name="get_services",
                        arguments={},
                    ),
                ),
                usage=TokenUsage(
                    input_tokens=12,
                    output_tokens=3,
                    cost_usd=Decimal("0.000020"),
                ),
            ),
            ModelTurn(
                text="General consultation costs PKR 2500.00.",
                usage=TokenUsage(
                    input_tokens=24,
                    output_tokens=8,
                    cost_usd=Decimal("0.000103"),
                ),
            ),
        ]
    )

    result = run_faq_agent(
        question="How much is a general consultation?",
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        model=model,
    )

    assert result.trace_id is not None
    assert result.cost_usd == Decimal("0.000123")
    trace = get_agent_run(tenant.id, result.trace_id)
    assert trace is not None
    assert trace.conversation_id == conversation.id
    assert trace.prompt_version == "faq-v1"
    assert trace.model_name == "trace-test-model"
    assert trace.status == "completed"
    assert trace.input_tokens == 36
    assert trace.output_tokens == 11
    assert trace.cost_usd == Decimal("0.000123")
    assert trace.tool_events[0]["name"] == "get_services"
    assert trace.tool_events[0]["output"] is not None
    assert get_agent_run(999_999, result.trace_id) is None


def test_fallback_run_is_persisted(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = TraceModel([ModelTurn(text="Invented answer")])

    result = run_faq_agent(
        question="Laser treatment available hai?",
        tenant_id=tenant.id,
        model=model,
    )

    assert result.trace_id is not None
    trace = get_agent_run(tenant.id, result.trace_id)
    assert trace is not None
    assert trace.status == "fallback"
    assert trace.conversation_id is None
    assert trace.tool_events == []
    assert trace.cost_usd == Decimal("0.000000")


def test_trace_persistence_can_be_disabled_for_pure_evals(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = TraceModel([ModelTurn(text="No evidence")])

    result = run_faq_agent(
        question="Unknown question",
        tenant_id=tenant.id,
        model=model,
        persist_trace=False,
    )

    assert result.trace_id is None
    with database_engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM agent_runs")).scalar_one()
    assert count == 0
