"""Narrow evidence for single-record FAQ lookups that the code can recognize."""

import re

SERVICE_QUESTION = re.compile(
    r"^(?:how much (?:is|does) (?:a |an |the )?|"
    r"(?:do you|does (?:the|your) clinic) (?:offer|provide|have) |"
    r"how long (?:is|does) (?:a |an |the )?)"
    r"(?P<name>[\w -]+?)(?: (?:cost|take))?[?.!]*$",
    re.IGNORECASE,
)
DOCTOR_QUESTION = re.compile(
    r"\bdr\.?\s+(?P<name>[\w -]+?)"
    r"(?:['’]s\b|\s+(?:work|practice|available|take appointments)\b|[?!.]|$)",
    re.IGNORECASE,
)


def scope_faq_evidence(
    question: str, tool_name: str, output: dict[str, object]
) -> dict[str, object] | None:
    """Select the named record, or return None when the clinic lacks it."""

    if tool_name == "get_services":
        match = SERVICE_QUESTION.fullmatch(question)
        key = "services"
    elif tool_name == "get_clinic_info":
        match = DOCTOR_QUESTION.search(question)
        key = "doctors"
    else:
        return output
    if match is None:
        return output

    name = _name_key(match["name"])
    # Catalogue requests have no individual record to look up.
    if key == "services" and (
        name in {"services", "treatments", "consultations", "any services", "any treatments"}
        or re.search(r"\b(?:and|or)\b", name)
    ):
        return output

    records = output.get(key)
    if not isinstance(records, list):
        return None
    matching = [
        record
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("name"), str)
        and _name_key(record["name"]) == name
    ]
    return {key: matching} if matching else None


def is_named_price_question(question: str) -> bool:
    return question.casefold().startswith("how much ") and SERVICE_QUESTION.fullmatch(
        question
    ) is not None


def render_service_price(output: dict[str, object]) -> str | None:
    """Render a price only for an unambiguous catalog record."""

    services = output.get("services")
    if not isinstance(services, list) or len(services) != 1:
        return None
    service = services[0]
    if not isinstance(service, dict):
        return None
    price = service.get("price")
    if not isinstance(price, dict):
        return None
    name, currency, amount = service.get("name"), price.get("currency"), price.get("amount")
    if not all(isinstance(value, str) and value for value in (name, currency, amount)):
        return None
    return f"{name} costs {currency} {amount}."


def _name_key(name: str) -> str:
    return re.sub(r"^dr\s+", "", " ".join(re.findall(r"\w+", name.casefold())))
