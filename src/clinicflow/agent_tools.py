"""Model-facing clinic tools with tenant identity fixed by application code."""

from copy import deepcopy
from decimal import Decimal

from clinicflow.clinic_facts import (
    get_clinic_info,
    get_doctors,
    get_opening_hours,
    get_services,
)
from clinicflow.knowledge import hybrid_search_kb


class ToolExecutionError(ValueError):
    """A safe validation error produced before any tool reaches storage."""


TOOL_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "name": "get_services",
        "description": (
            "List the clinic's active services with exact prices and durations."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_clinic_info",
        "description": (
            "Get the clinic's contact details, regular hours, and doctors."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_kb",
        "description": "Search the clinic's FAQ and policy knowledge base.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The customer's question or search phrase.",
                    "minLength": 1,
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
)


class ClinicToolbox:
    """Execute clinic tools only within the tenant selected by the application."""

    def __init__(self, tenant_id: int) -> None:
        self._tenant_id = tenant_id

    def definitions(self) -> list[dict[str, object]]:
        """Return model-visible JSON schemas; tenant_id is intentionally absent."""

        return deepcopy(list(TOOL_DEFINITIONS))

    def execute(
        self,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        """Validate and execute one requested tool call."""

        if not isinstance(arguments, dict):
            raise ToolExecutionError("arguments must be a JSON object")
        if name == "get_services":
            self._require_exact_arguments(arguments, set())
            return self._get_services()
        if name == "get_clinic_info":
            self._require_exact_arguments(arguments, set())
            return self._get_clinic_info()
        if name == "search_kb":
            self._require_exact_arguments(arguments, {"query"})
            query = arguments["query"]
            if not isinstance(query, str) or not query.strip():
                raise ToolExecutionError("query must be a non-empty string")
            return self._search_kb(query)
        raise ToolExecutionError(f"unknown tool: {name}")

    @staticmethod
    def _require_exact_arguments(
        arguments: dict[str, object],
        expected: set[str],
    ) -> None:
        received = set(arguments)
        if received != expected:
            missing = sorted(expected - received)
            unexpected = sorted(received - expected)
            details: list[str] = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if unexpected:
                details.append(f"unexpected: {', '.join(unexpected)}")
            raise ToolExecutionError("invalid arguments (" + "; ".join(details) + ")")

    def _get_services(self) -> dict[str, object]:
        services = get_services(self._tenant_id)
        return {
            "services": [
                {
                    "name": service.name,
                    "description": service.description,
                    "price": self._format_price(
                        service.price_amount,
                        service.currency,
                    ),
                    "duration_minutes": service.duration_minutes,
                }
                for service in services
            ]
        }

    def _get_clinic_info(self) -> dict[str, object]:
        profile = get_clinic_info(self._tenant_id)
        hours = get_opening_hours(self._tenant_id)
        doctors = get_doctors(self._tenant_id)
        return {
            "profile": (
                None
                if profile is None
                else {
                    "description": profile.description,
                    "address": profile.address,
                    "city": profile.city,
                    "country": profile.country,
                    "contact_phone": profile.contact_phone,
                    "email": profile.email,
                }
            ),
            "opening_hours": [
                {
                    "day": item.day_name,
                    "is_closed": item.is_closed,
                    "opens_at": (
                        item.opens_at.strftime("%H:%M")
                        if item.opens_at is not None
                        else None
                    ),
                    "closes_at": (
                        item.closes_at.strftime("%H:%M")
                        if item.closes_at is not None
                        else None
                    ),
                }
                for item in hours
            ],
            "doctors": [
                {
                    "name": doctor.name,
                    "specialty": doctor.specialty,
                    "bio": doctor.bio,
                }
                for doctor in doctors
            ],
        }

    def _search_kb(self, query: str) -> dict[str, object]:
        results = hybrid_search_kb(self._tenant_id, query)
        return {
            "results": [
                {
                    "source_key": result.source_key,
                    "title": result.title,
                    "content": result.content,
                    "language": result.language,
                    "score": round(result.score, 6),
                }
                for result in results
            ]
        }

    @staticmethod
    def _format_price(
        amount: Decimal | None,
        currency: str,
    ) -> dict[str, str] | None:
        if amount is None:
            return None
        return {"amount": format(amount, ".2f"), "currency": currency}
