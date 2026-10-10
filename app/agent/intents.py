"""Intent and slot contracts for the deterministic B-agent layer."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(str, Enum):
    """Business intents supported by the first B-agent iteration."""

    ORDER_STATUS = "order_status"
    PRODUCTION_NORMALITY = "production_normality"
    MORNING_BRIEFING = "morning_briefing"
    ORDER_FEASIBILITY = "order_feasibility"
    UNKNOWN = "unknown"


class ResponseLanguage(str, Enum):
    """Languages supported by user-facing B-agent interactions."""

    ENGLISH = "en"
    CHINESE = "zh"


REQUIRED_SLOTS: dict[Intent, frozenset[str]] = {
    Intent.ORDER_STATUS: frozenset({"order_id"}),
    Intent.PRODUCTION_NORMALITY: frozenset({"stage"}),
    Intent.MORNING_BRIEFING: frozenset(),
    Intent.ORDER_FEASIBILITY: frozenset(
        {"category", "pieces", "requested_delivery_date"}
    ),
    Intent.UNKNOWN: frozenset(),
}


@dataclass(frozen=True)
class IntentDecision:
    """A structured routing decision produced before tool execution."""

    intent: Intent
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    language: ResponseLanguage = ResponseLanguage.ENGLISH


def required_slots_for(intent: Intent) -> frozenset[str]:
    """Return the slots required to execute an intent."""

    return REQUIRED_SLOTS[intent]


def find_missing_slots(decision: IntentDecision) -> tuple[str, ...]:
    """Combine declared and inferred missing slots in a stable order."""

    missing = set(decision.missing_slots)
    for slot in required_slots_for(decision.intent):
        value = decision.slots.get(slot)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.add(slot)
    return tuple(sorted(missing))
