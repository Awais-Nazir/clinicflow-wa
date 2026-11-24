"""Integration tests for the versioned Phase 1 evaluation suite."""

from collections import Counter
from dataclasses import replace
import os

import pytest

from clinicflow.evals import (
    EvalCase,
    FixtureEvalModel,
    format_summary,
    load_eval_cases,
    run_eval_suite,
)
from clinicflow.tenants import seed_demo_clinic

DATABASE_URL = os.getenv("DATABASE_URL")

requires_database = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


def test_dataset_has_25_unique_cases_in_expected_categories() -> None:
    cases = load_eval_cases()

    assert len(cases) == 25
    assert len({case.id for case in cases}) == 25
    assert Counter(case.category for case in cases) == {
        "services": 6,
        "clinic_info": 6,
        "policy": 6,
        "unanswerable": 4,
        "multilingual": 3,
    }


@requires_database
def test_fixture_baseline_passes_full_eval_suite() -> None:
    tenant = seed_demo_clinic("eval-phone-number")

    summary = run_eval_suite(
        cases=load_eval_cases(),
        tenant_id=tenant.id,
        model_factory=FixtureEvalModel,
    )

    assert summary.total == 25
    assert summary.passed == 25
    assert summary.all_passed is True
    assert all(
        score.passed == score.total for score in summary.categories.values()
    )
    assert "overall: 25/25" in format_summary(summary)


@requires_database
def test_eval_suite_reports_a_regression() -> None:
    tenant = seed_demo_clinic("eval-phone-number")
    case = EvalCase(
        id="deliberate-failure",
        category="regression",
        question="Do you offer an unknown treatment?",
        expected_language="en",
        expect_fallback=False,
        expected_tool=None,
        tool_arguments={},
        required_substrings=("invented answer",),
        fixture_reply="Invented answer",
    )

    summary = run_eval_suite(
        cases=[case],
        tenant_id=tenant.id,
        model_factory=FixtureEvalModel,
    )

    assert summary.all_passed is False
    assert summary.passed == 0
    report = format_summary(summary)
    assert "FAIL deliberate-failure" in report
    assert "fallback expected False, got True" in report
    assert "reply missing 'invented answer'" in report


@requires_database
def test_eval_accepts_equivalent_price_and_clock_formats() -> None:
    tenant = seed_demo_clinic("eval-phone-number")
    replies = {
        "hydra-price-en": "The Hydra facial costs 8,500 PKR.",
        "monday-hours-en": "Monday hours are 10 am to 7 pm.",
        "monday-hours-ur": "کلینک پیر کو 10:00 سے 19:00 تک کھلا ہے۔",
    }
    cases = [
        replace(case, fixture_reply=replies[case.id])
        for case in load_eval_cases()
        if case.id in replies
    ]

    summary = run_eval_suite(
        cases=cases, tenant_id=tenant.id, model_factory=FixtureEvalModel
    )

    assert summary.passed == summary.total == 3
