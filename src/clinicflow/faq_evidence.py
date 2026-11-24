"""Narrow evidence for single-record FAQ lookups that the code can recognize."""

import re

from clinicflow.booking_time import WEEKDAYS

SERVICE_QUESTION = re.compile(
    r"^(?:how much (?:is|does) (?:a |an |the )?|"
    r"(?:can|could) i book (?:a |an |the )?|"
    r"(?:do you|does (?:the|your) clinic) (?:offer|provide|have) |"
    r"how long (?:is|does) (?:a |an |the )?)"
    r"(?P<name>[\w -]+?)(?: (?:cost|take))?[?.!]*$",
    re.IGNORECASE,
)
PARKING = re.compile(
    r"\bparking\b|\b(?:can|could|do|to)\s+(?:(?:i|we|visitors|patients)\s+)?park\b|پارکنگ",
    re.IGNORECASE,
)
WALK_INS = re.compile(
    r"\bwalk[- ]?ins?\b|\bwithout an appointment\b|بغیر (?:اپائنٹمنٹ|وقت)",
    re.IGNORECASE,
)
CASH = re.compile(r"\bcash\b|نقد", re.IGNORECASE)
CASH_ACCEPTANCE = re.compile(r"\b(?:can (?:i|we) pay|(?:do you|does the clinic) accept)\b|قبول", re.IGNORECASE)
INSTRUCTION_OVERRIDE = re.compile(
    r"\b(?:ignore|forget|override|disregard)\s+(?:(?:all|your|previous|prior|earlier|the)\s+)*"
    r"(?:rules|instructions|system prompt)\b|\brules bhool jao\b|ہدایات بھول",
    re.IGNORECASE,
)
HOURS_QUESTION = re.compile(
    r"\b(?:hours|open|close[ds]?|khult[ai]|band)\b|(?:کھل|کھلا|بند|اوقات)", re.IGNORECASE
)


def policy_search(question: str) -> str | None:
    """Route clearly named policies to the knowledge base."""

    if PARKING.search(question):
        return "parking"
    if WALK_INS.search(question):
        return "walk in without an appointment"
    if CASH.search(question) and CASH_ACCEPTANCE.search(question):
        return "cash payment"
    return None
DOCTOR_QUESTION = re.compile(
    r"\bdr\.?\s+(?P<name>[\w -]+?)"
    r"(?:['’]s\b|\s+(?:work|practice|available|take appointments)\b|[?!.]|$)",
    re.IGNORECASE,
)


def scope_faq_evidence(
    question: str, tool_name: str, output: dict[str, object]
) -> dict[str, object] | None:
    """Select the named record, or return None when the clinic lacks it."""

    policy = policy_search(question)
    if policy is not None and tool_name == "search_kb":
        pattern = {"parking": PARKING, "walk in without an appointment": WALK_INS, "cash payment": CASH}[policy]
        results = output.get("results")
        if not isinstance(results, list):
            return None
        matching = [
            result for result in results if isinstance(result, dict)
            and pattern.search(f"{result.get('title', '')} {result.get('content', '')}")
        ]
        return {"results": matching} if matching else None
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
    return question.casefold().startswith("how much ") and is_service_question(question)


def is_service_question(question: str) -> bool:
    return SERVICE_QUESTION.fullmatch(question) is not None


def render_cash_acceptance(output: dict[str, object], language: str) -> str | None:
    """State an explicit cash policy in the customer's language."""

    results = output.get("results")
    if not isinstance(results, list):
        return None
    signals = set()
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("content"), str):
            continue
        for sentence in re.split(r"[.!؟۔]\s*", result["content"].casefold()):
            if not CASH.search(sentence) or re.search(r"\b(?:only|except|unless)\b", sentence):
                continue
            if re.search(r"\b(?:not|never|don't|doesn't)\s+accept\s+cash\b|cash\s+is\s+not\s+accepted|نقد ادائیگی قبول نہیں", sentence):
                signals.add(False)
            elif re.search(r"\b(?:accepts?\s+cash|cash\s+is\s+accepted)\b|نقد ادائیگی قبول ہے", sentence):
                if not re.search(r"\b(?:not|never|no)\b", sentence):
                    signals.add(True)
    if signals == {True}:
        return {
            "en": "Yes, the clinic accepts cash.",
            "roman_ur": "Ji haan, clinic cash accept karta hai.",
            "ur": "جی ہاں، کلینک نقد ادائیگی قبول کرتا ہے۔",
        }[language]
    if signals == {False}:
        return {
            "en": "The clinic does not accept cash.",
            "roman_ur": "Clinic cash accept nahi karta.",
            "ur": "کلینک نقد ادائیگی قبول نہیں کرتا۔",
        }[language]
    return None


def requested_hours_day(question: str) -> tuple[str, str, str] | None:
    if not HOURS_QUESTION.search(question) or DOCTOR_QUESTION.search(question):
        return None
    days = [
        aliases for aliases in WEEKDAYS
        if any(re.search(rf"(?<!\w){re.escape(day)}(?!\w)", question.casefold()) for day in aliases)
    ]
    if len(days) != 1:
        return None
    english, roman, *other = days[0]
    return english.capitalize(), roman.capitalize(), other[-1]


def render_clinic_hours(
    output: dict[str, object], labels: tuple[str, str, str], language: str,
) -> str | None:
    days = output.get("opening_hours")
    if not isinstance(days, list):
        return None
    hours = next((item for item in days if isinstance(item, dict) and item.get("day") == labels[0]), None)
    if hours is None:
        return None
    english, roman, urdu = labels
    if hours.get("is_closed") is True:
        return {
            "en": f"The clinic is closed on {english}.",
            "roman_ur": f"{roman} ko clinic band hai.",
            "ur": f"کلینک {urdu} کو بند ہے۔",
        }[language]
    start, end = hours.get("opens_at"), hours.get("closes_at")
    if not start or not end:
        return None
    return {
        "en": f"{english} hours are {start} to {end}.",
        "roman_ur": f"{roman} ko clinic {start} se {end} tak khulti hai.",
        "ur": f"کلینک {urdu} کو {start} سے {end} تک کھلا ہے۔",
    }[language]


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
