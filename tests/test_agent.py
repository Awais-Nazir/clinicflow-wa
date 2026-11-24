"""Integration tests for provider-independent FAQ agent orchestration."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.agent import (
    AgentMessage,
    ModelTurn,
    NO_CLINIC_ANSWER,
    TokenUsage,
    ToolCallRequest,
    detect_reply_language,
    run_faq_agent,
)
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


class ScriptedModel:
    model_name = "scripted-free-model"

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = turns
        self.calls: list[tuple[AgentMessage, ...]] = []
        self.tool_schemas: list[list[dict[str, object]]] = []

    def complete(
        self,
        messages: tuple[AgentMessage, ...],
        tools: list[dict[str, object]],
    ) -> ModelTurn:
        self.calls.append(messages)
        self.tool_schemas.append(tools)
        return self.turns.pop(0)


def test_agent_executes_tool_then_returns_grounded_answer(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = ScriptedModel(
        [
            ModelTurn(
                tool_calls=(
                    ToolCallRequest(
                        id="call-1",
                        name="get_services",
                        arguments={},
                    ),
                ),
                usage=TokenUsage(input_tokens=20, output_tokens=5),
            ),
            ModelTurn(
                text="Hydra facial costs PKR 8500.00.",
                usage=TokenUsage(input_tokens=40, output_tokens=10),
            ),
        ]
    )

    result = run_faq_agent(
        question="What are the services and prices?",
        tenant_id=tenant.id,
        model=model,
    )

    assert result.reply == "Hydra facial costs PKR 8500.00."
    assert result.used_fallback is False
    assert result.language == "en"
    assert result.input_tokens == 60
    assert result.output_tokens == 15
    assert result.tool_events[0].name == "get_services"
    assert result.tool_events[0].error is None
    assert result.tool_events[0].output is not None
    assert model.calls[1][-1].role == "tool"
    assert "8500.00" in model.calls[1][-1].content
    assert "tenant_id" not in str(model.tool_schemas)


@pytest.mark.parametrize(
    ("question", "expected_language", "expected_reply"),
    [
        (
            "Laser treatment available hai?",
            "roman_ur",
            "Mujhe clinic ki available maloomat mein iska jawab nahi mila.",
        ),
        (
            "کیا لیزر علاج دستیاب ہے؟",
            "ur",
            "مجھے دستیاب کلینک کی معلومات میں اس کا جواب نہیں ملا۔",
        ),
        (
            "Do you provide laser treatment?",
            "en",
            "I couldn't find that in the clinic information available to me.",
        ),
    ],
)
def test_agent_uses_language_matched_fallback_without_evidence(
    database_engine: Engine,
    question: str,
    expected_language: str,
    expected_reply: str,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = ScriptedModel([ModelTurn(text="Yes, we provide it.")])

    result = run_faq_agent(
        question=question,
        tenant_id=tenant.id,
        model=model,
    )

    assert result.language == expected_language
    assert result.reply == expected_reply
    assert result.used_fallback is True


def test_tool_error_is_returned_to_model_and_cannot_ground_answer(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = ScriptedModel(
        [
            ModelTurn(
                tool_calls=(
                    ToolCallRequest(
                        id="bad-call",
                        name="search_kb",
                        arguments={"query": "", "tenant_id": 999},
                    ),
                )
            ),
            ModelTurn(text="Invented answer"),
        ]
    )

    result = run_faq_agent(
        question="Policy kya hai?",
        tenant_id=tenant.id,
        model=model,
    )

    assert result.used_fallback is True
    assert result.tool_events[0].output is None
    assert result.tool_events[0].error is not None
    assert "unexpected: tenant_id" in result.tool_events[0].error
    assert "error" in model.calls[1][-1].content


def test_agent_uses_localized_fallback_when_tool_result_lacks_answer(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = ScriptedModel(
        [
            ModelTurn(
                tool_calls=(
                    ToolCallRequest(id="call-1", name="get_services", arguments={}),
                )
            ),
            ModelTurn(text=NO_CLINIC_ANSWER),
        ]
    )

    result = run_faq_agent(
        question="Laser treatment available hai?",
        tenant_id=tenant.id,
        model=model,
    )

    assert result.tool_events[0].error is None
    assert result.used_fallback
    assert result.guardrail_reason == "answer_not_in_clinic_data"
    assert result.reply == "Mujhe clinic ki available maloomat mein iska jawab nahi mila."


def test_missing_named_service_cannot_use_unrelated_catalogue() -> None:
    model = ScriptedModel(
        [
            ModelTurn(tool_calls=(ToolCallRequest("lookup", "get_services", {}),)),
            ModelTurn(text="No, we do not provide laser treatment."),
        ]
    )
    tenant = seed_demo_clinic("faq-missing-service-test")

    result = run_faq_agent(
        question="Do you provide laser treatment?",
        tenant_id=tenant.id,
        model=model,
        persist_trace=False,
    )

    assert not model.calls
    assert result.used_fallback
    assert result.guardrail_reason == "answer_not_in_clinic_data"


def test_named_service_cannot_reuse_another_services_price() -> None:
    model = ScriptedModel(
        [
            ModelTurn(tool_calls=(ToolCallRequest("lookup", "get_services", {}),)),
            ModelTurn(text="The Hydra facial costs PKR 3000."),
        ]
    )
    tenant = seed_demo_clinic("faq-price-scope-test")
    result = run_faq_agent(
        question="How much is the Hydra facial?",
        tenant_id=tenant.id,
        model=model,
        persist_trace=False,
    )

    assert result.reply == "Hydra facial costs PKR 8500.00."
    assert not model.calls


def test_agent_rejects_invented_numeric_fact(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = ScriptedModel(
        [
            ModelTurn(
                tool_calls=(
                    ToolCallRequest(
                        id="services-price",
                        name="get_services",
                        arguments={},
                    ),
                )
            ),
            ModelTurn(text="Hydra facial costs PKR 9999."),
        ]
    )

    result = run_faq_agent(
        question="Hydra facial kitne ki hai?",
        tenant_id=tenant.id,
        model=model,
    )

    assert result.used_fallback is True
    assert result.guardrail_reason == "unsupported_numeric_facts"
    assert result.unsupported_numbers == ("9999",)
    assert result.reply == (
        "Mujhe clinic ki available maloomat mein iska jawab nahi mila."
    )


def test_language_detection_is_deterministic() -> None:
    assert detect_reply_language("Clinic kab khulti hai?") == "roman_ur"
    assert detect_reply_language("کلینک کب کھلتا ہے؟") == "ur"
    assert detect_reply_language("When does the clinic open?") == "en"


def test_excessive_tool_calls_use_safe_fallback(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    calls = tuple(
        ToolCallRequest(id=f"call-{index}", name="get_services", arguments={})
        for index in range(9)
    )
    model = ScriptedModel([ModelTurn(tool_calls=calls)])

    result = run_faq_agent(
        question="Services kya hain?",
        tenant_id=tenant.id,
        model=model,
    )

    assert result.used_fallback is True
    assert result.tool_events == ()


def test_empty_question_is_rejected(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    model = ScriptedModel([])

    with pytest.raises(ValueError, match="cannot be empty"):
        run_faq_agent(question="  ", tenant_id=tenant.id, model=model)
