"""Deterministic numeric grounding checks for model-generated replies."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata

NUMBER_PATTERN = re.compile(
    r"(?<![\w])[-+]?\d(?:[\d,.\u066b\u066c]*\d)?(?![\w])"
)
NON_FACT_KEYS = {"score", "latency_ms"}


@dataclass(frozen=True)
class NumericGroundingCheck:
    reply_numbers: tuple[str, ...]
    supported_numbers: tuple[str, ...]
    unsupported_numbers: tuple[str, ...]

    @property
    def is_supported(self) -> bool:
        return not self.unsupported_numbers


def check_numeric_grounding(
    reply: str,
    tool_outputs: list[dict[str, object]],
) -> NumericGroundingCheck:
    """Require every digit-based reply number to occur in a tool result."""

    reply_numbers = extract_text_numbers(reply)
    supported: set[str] = set()
    for output in tool_outputs:
        supported.update(extract_value_numbers(output))

    unsupported = reply_numbers - supported
    return NumericGroundingCheck(
        reply_numbers=tuple(sorted(reply_numbers)),
        supported_numbers=tuple(sorted(supported)),
        unsupported_numbers=tuple(sorted(unsupported)),
    )


def extract_text_numbers(text: str) -> set[str]:
    """Extract canonical decimal values from ASCII or Urdu/Arabic digits."""

    numbers: set[str] = set()
    for match in NUMBER_PATTERN.finditer(text):
        normalized = _normalize_number(match.group())
        if normalized is not None:
            numbers.add(normalized)
    return numbers


def extract_value_numbers(value: object) -> set[str]:
    """Recursively collect user-visible numeric facts from a tool output."""

    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, str):
        return extract_text_numbers(value)
    if isinstance(value, int | Decimal):
        normalized = _normalize_number(str(value))
        return {normalized} if normalized is not None else set()
    if isinstance(value, float):
        return set()
    if isinstance(value, dict):
        numbers: set[str] = set()
        for key, item in value.items():
            if key in NON_FACT_KEYS:
                continue
            numbers.update(extract_value_numbers(item))
        return numbers
    if isinstance(value, list | tuple):
        numbers = set()
        for item in value:
            numbers.update(extract_value_numbers(item))
        return numbers
    return set()


def _normalize_number(raw: str) -> str | None:
    converted: list[str] = []
    for character in raw:
        if character in {",", "\u066c"}:
            continue
        if character in {".", "\u066b"}:
            converted.append(".")
            continue
        if character in {"+", "-"}:
            converted.append(character)
            continue
        try:
            converted.append(str(unicodedata.digit(character)))
        except (TypeError, ValueError):
            return None

    try:
        number = Decimal("".join(converted))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    if number == 0:
        return "0"
    return format(number.normalize(), "f")
