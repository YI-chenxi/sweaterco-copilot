
"""Tests for shared enhancement contracts."""

from app.agent.enhancement.contracts import (
    ConversationState,
    TaskPlan,
    UnderstandingResult,
)
from app.agent.intents import Intent, IntentDecision, ResponseLanguage


def test_understanding_result():
    decision = IntentDecision(
        intent=Intent.ORDER_STATUS,
        slots={"order_id": "ORD-057"},
    )

    result = UnderstandingResult(decisions=[decision])

    assert len(result.decisions) == 1
    assert result.decisions[0].intent == Intent.ORDER_STATUS


def test_conversation_state_defaults():
    state = ConversationState()

    assert state.pending_tasks == []
    assert state.collected_slots == {}
    assert state.missing_slots == ()
    assert state.language == ResponseLanguage.ENGLISH


def test_task_plan():
    decision = IntentDecision(
        intent=Intent.MORNING_BRIEFING,
        slots={},
    )

    plan = TaskPlan(tasks=[decision])

    assert len(plan.tasks) == 1
    assert plan.requires_clarification is False


def test_conversation_states_are_independent():
    first = ConversationState()
    second = ConversationState()

    first.collected_slots["pieces"] = 800

    assert second.collected_slots == {}
