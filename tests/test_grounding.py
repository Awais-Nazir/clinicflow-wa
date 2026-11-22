"""Unit tests for deterministic numeric grounding checks."""

from clinicflow.grounding import (
    check_numeric_grounding,
    extract_text_numbers,
)


def test_number_extraction_normalizes_formats_and_unicode_digits() -> None:
    numbers = extract_text_numbers(
        "Price PKR 8,500.00, time ۱۰:۰۰, Arabic decimal ١٢٫٥."
    )

    assert numbers == {"8500", "10", "0", "12.5"}


def test_equivalent_price_and_duration_formats_are_supported() -> None:
    check = check_numeric_grounding(
        "It costs PKR 8,500 and takes 60 minutes.",
        [
            {
                "services": [
                    {
                        "price": {"amount": "8500.00", "currency": "PKR"},
                        "duration_minutes": 60,
                    }
                ]
            }
        ],
    )

    assert check.is_supported is True
    assert check.unsupported_numbers == ()


def test_unsupported_number_is_reported_and_scores_are_not_facts() -> None:
    check = check_numeric_grounding(
        "The price is 9999 and confidence is 0.5.",
        [{"price": {"amount": "8500.00"}, "score": 0.5}],
    )

    assert check.is_supported is False
    assert check.unsupported_numbers == ("0.5", "9999")
