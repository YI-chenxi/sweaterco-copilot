"""Tests for deterministic intent and slot validation."""

from app.agent.intents import Intent, IntentDecision, find_missing_slots


def test_find_missing_slots_infers_blank_and_absent_values():
    decision = IntentDecision(
        intent=Intent.ORDER_FEASIBILITY,
        slots={"category": " ", "pieces": 800},
    )

    assert find_missing_slots(decision) == ("category", "requested_delivery_date")


def test_find_missing_slots_preserves_declared_missing_slots():
    decision = IntentDecision(
        intent=Intent.ORDER_STATUS,
        slots={"order_id": "ORD-057"},
        missing_slots=("confirmation",),
    )

    assert find_missing_slots(decision) == ("confirmation",)
