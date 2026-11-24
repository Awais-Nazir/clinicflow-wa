"""Phase 1 evaluation cases, runner, scoring, and fixture baseline."""

import argparse
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import cast
from unicodedata import decimal

from clinicflow.agent import (
    AgentMessage,
    AgentModel,
    AgentRunResult,
    ModelTurn,
    ReplyLanguage,
    ToolCallRequest,
    run_faq_agent,
)
from clinicflow.grounding import extract_text_numbers, normalize_fact_token
from clinicflow.tenants import seed_demo_clinic

CASES_PATH = Path(__file__).with_name("eval_cases.json")


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    question: str
    expected_language: ReplyLanguage
    expect_fallback: bool
    expected_tool: str | None
    tool_arguments: dict[str, object]
    required_substrings: tuple[str | tuple[str, ...], ...]
    fixture_reply: str


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    category: str
    passed: bool
    failures: tuple[str, ...]
    reply: str
    used_fallback: bool


@dataclass(frozen=True)
class CategoryScore:
    passed: int
    total: int


@dataclass(frozen=True)
class EvalSummary:
    results: tuple[EvalResult, ...]
    categories: dict[str, CategoryScore]

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def all_passed(self) -> bool:
        return self.passed == self.total


ModelFactory = Callable[[EvalCase], AgentModel]


class FixtureEvalModel:
    """A free deterministic baseline that exercises the real agent pipeline."""

    model_name = "fixture-eval-model"

    def __init__(self, case: EvalCase) -> None:
        self.case = case
        self.turn = 0

    def complete(
        self,
        messages: tuple[AgentMessage, ...],
        tools: list[dict[str, object]],
    ) -> ModelTurn:
        self.turn += 1
        if self.turn == 1 and self.case.expected_tool is not None:
            return ModelTurn(
                tool_calls=(
                    ToolCallRequest(
                        id=f"{self.case.id}-tool",
                        name=self.case.expected_tool,
                        arguments=self.case.tool_arguments,
                    ),
                )
            )
        return ModelTurn(text=self.case.fixture_reply)


def load_eval_cases(path: Path = CASES_PATH) -> list[EvalCase]:
    """Load and validate the versioned JSON eval dataset."""

    raw_cases: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("eval dataset must be a JSON list")

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("every eval case must be an object")
        case_id = _required_string(item, "id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate eval case id: {case_id}")
        seen_ids.add(case_id)

        language = _required_string(item, "expected_language")
        if language not in {"en", "roman_ur", "ur"}:
            raise ValueError(f"invalid language for {case_id}: {language}")

        expected_tool = item.get("expected_tool")
        if expected_tool is not None and not isinstance(expected_tool, str):
            raise ValueError(f"expected_tool must be a string for {case_id}")
        tool_arguments = item.get("tool_arguments", {})
        if not isinstance(tool_arguments, dict):
            raise ValueError(f"tool_arguments must be an object for {case_id}")
        required = item.get("required_substrings", [])
        if not isinstance(required, list):
            raise ValueError(f"required_substrings must be a list for {case_id}")
        required_phrases: list[str | tuple[str, ...]] = []
        for value in required:
            if isinstance(value, str) and value.strip():
                required_phrases.append(value)
            elif isinstance(value, list) and value and all(
                isinstance(option, str) and option.strip() for option in value
            ):
                required_phrases.append(tuple(value))
            else:
                raise ValueError(f"invalid reply phrase for {case_id}")
        expect_fallback = item.get("expect_fallback")
        if not isinstance(expect_fallback, bool):
            raise ValueError(f"expect_fallback must be boolean for {case_id}")

        cases.append(
            EvalCase(
                id=case_id,
                category=_required_string(item, "category"),
                question=_required_string(item, "question"),
                expected_language=cast(ReplyLanguage, language),
                expect_fallback=expect_fallback,
                expected_tool=expected_tool,
                tool_arguments=tool_arguments,
                required_substrings=tuple(required_phrases),
                fixture_reply=_required_string(item, "fixture_reply"),
            )
        )
    return cases


def run_eval_suite(
    *,
    cases: list[EvalCase],
    tenant_id: int,
    model_factory: ModelFactory,
    on_result: Callable[[EvalResult], None] | None = None,
) -> EvalSummary:
    """Run cases through the real agent path and score deterministic checks."""

    results: list[EvalResult] = []
    category_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for case in cases:
        agent_result = run_faq_agent(
            question=case.question,
            tenant_id=tenant_id,
            model=model_factory(case),
            persist_trace=False,
        )
        result = _score_case(case, agent_result)
        results.append(result)
        if on_result is not None:
            on_result(result)
        category_totals[case.category][1] += 1
        if result.passed:
            category_totals[case.category][0] += 1

    categories = {
        category: CategoryScore(passed=counts[0], total=counts[1])
        for category, counts in sorted(category_totals.items())
    }
    return EvalSummary(results=tuple(results), categories=categories)


def format_summary(summary: EvalSummary) -> str:
    """Format a compact report suitable for local use and CI logs."""

    lines = [f"overall: {summary.passed}/{summary.total}"]
    for category, score in summary.categories.items():
        lines.append(f"{category}: {score.passed}/{score.total}")
    for result in summary.results:
        if not result.passed:
            lines.append(
                f"FAIL {result.case_id}: {'; '.join(result.failures)}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Run the Phase 1 FAQ eval suite")
    parser.add_argument(
        "--provider",
        choices=("fixture", "ollama"),
        default="fixture",
        help="model provider to evaluate (default: fixture)",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="run only this case ID (repeat to select multiple cases)",
    )
    args = parser.parse_args(argv)

    cases = load_eval_cases()
    if args.case_ids:
        requested = set(args.case_ids)
        known = {case.id for case in cases}
        unknown = requested - known
        if unknown:
            parser.error("unknown case ID: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case.id in requested]
    tenant = seed_demo_clinic("eval-phone-number")
    if args.provider == "fixture":
        summary = run_eval_suite(
            cases=cases,
            tenant_id=tenant.id,
            model_factory=FixtureEvalModel,
            on_result=_print_progress,
        )
    else:
        from clinicflow.ollama import OllamaModel

        model = OllamaModel.from_env()
        try:
            summary = run_eval_suite(
                cases=cases,
                tenant_id=tenant.id,
                model_factory=lambda case: model,
                on_result=_print_progress,
            )
        finally:
            model.close()
    print(format_summary(summary))
    return 0 if summary.all_passed else 1


def _print_progress(result: EvalResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"{status} {result.case_id}", flush=True)


def _score_case(case: EvalCase, result: AgentRunResult) -> EvalResult:
    failures: list[str] = []
    if result.language != case.expected_language:
        failures.append(
            f"language expected {case.expected_language}, got {result.language}"
        )
    uses_urdu_script = any(
        "\u0600" <= character <= "\u06ff" and character.isalpha()
        for character in result.reply
    )
    if uses_urdu_script != (case.expected_language == "ur"):
        failures.append(f"wrong reply script for {case.expected_language}")
    if result.used_fallback != case.expect_fallback:
        failures.append(
            f"fallback expected {case.expect_fallback}, got {result.used_fallback}"
        )
    tool_names = {event.name for event in result.tool_events if event.error is None}
    if case.expected_tool is not None and case.expected_tool not in tool_names:
        failures.append(f"missing tool {case.expected_tool}")

    normalized_reply = _normalize_phrase(result.reply)
    reply_numbers = extract_text_numbers(result.reply)
    for required in case.required_substrings:
        alternatives = (required,) if isinstance(required, str) else required
        if not any(_matches_phrase(option, normalized_reply, reply_numbers) for option in alternatives):
            failures.append(f"reply missing {required!r}")

    return EvalResult(
        case_id=case.id,
        category=case.category,
        passed=not failures,
        failures=tuple(failures),
        reply=result.reply,
        used_fallback=result.used_fallback,
    )


def _normalize_phrase(text: str) -> str:
    return "".join(str(decimal(char)) if char.isdecimal() else char for char in text.casefold())


def _matches_phrase(required: str, reply: str, numbers: set[str]) -> bool:
    fact = normalize_fact_token(required)
    if fact is not None:
        return fact in numbers
    phrase = re.escape(_normalize_phrase(required))
    return re.search(rf"(?<!\w){phrase}(?!\w)", reply) is not None


def _required_string(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
