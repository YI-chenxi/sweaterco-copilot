"""Tests for validated single- and dual-intent understanding."""

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    SlotUpdate,
    TaskState,
    TaskStatus,
)
from app.agent.enhancement.understanding.parser import EnhancedIntentParser
from app.agent.intents import Intent, IntentDecision, ResponseLanguage
from app.agent.llm_client import LLMClientError, MockLLMClient


def task(intent="unknown", **overrides):
    result = {
        "intent": intent,
        "order_id": None,
        "stage": None,
        "category": None,
        "pieces": None,
        "requested_delivery_date": None,
        "top_n": None,
        "missing_slots": [],
    }
    result.update(overrides)
    return result


def payload(*tasks, **overrides):
    result = {
        "language": "en",
        "is_ambiguous": False,
        "tasks": list(tasks),
        "slot_updates": [],
        "issues": [],
    }
    result.update(overrides)
    return result


def update(task_id, **overrides):
    result = {
        "task_id": task_id,
        "order_id": None,
        "stage": None,
        "category": None,
        "pieces": None,
        "requested_delivery_date": None,
        "top_n": None,
    }
    result.update(overrides)
    return result


def test_parser_pre_normalizes_safe_order_id_before_model_call():
    client = MockLLMClient(
        [payload(task("order_status", order_id="ORD057"))]
    )

    result = EnhancedIntentParser(client).parse("How is ORD057?")

    assert result.decisions[0].slots == {"order_id": "ORD-057"}
    assert "How is ORD-057?" in client.calls[0]["user_prompt"]
    assert result.issues == []


def test_parser_returns_two_independent_tasks_in_order():
    client = MockLLMClient(
        [
            payload(
                task("order_status", order_id="ORD-057"),
                task("production_normality", stage="assembly"),
                language="zh",
            )
        ]
    )

    result = EnhancedIntentParser(client).parse(
        "检查ORD-057，再看看Assembly是否正常"
    )

    assert [decision.intent for decision in result.decisions] == [
        Intent.ORDER_STATUS,
        Intent.PRODUCTION_NORMALITY,
    ]
    assert result.decisions[0].slots == {"order_id": "ORD-057"}
    assert result.decisions[1].slots == {"stage": "ASSEMBLY"}
    assert result.language == ResponseLanguage.CHINESE


def test_parser_maps_packaging_alias_to_canonical_packing_stage():
    client = MockLLMClient(
        [payload(task("production_normality", stage="PACKAGING"), language="zh")]
    )

    result = EnhancedIntentParser(client).parse("请看看包装工序是否正常。")

    assert result.decisions[0].slots == {"stage": "PACKING"}
    assert result.issues == []


def test_parser_drops_slots_that_do_not_belong_to_task():
    client = MockLLMClient(
        [
            payload(
                task(
                    "order_status",
                    order_id="ORD-057",
                    pieces=800,
                    category="TOPS",
                )
            )
        ]
    )

    result = EnhancedIntentParser(client).parse("Check ORD-057")

    assert result.decisions[0].slots == {"order_id": "ORD-057"}


def test_parser_recomputes_missing_slots_instead_of_trusting_model():
    client = MockLLMClient(
        [
            payload(
                task(
                    "order_feasibility",
                    pieces=800,
                    requested_delivery_date="2026/4/25",
                    missing_slots=[],
                )
            )
        ]
    )

    result = EnhancedIntentParser(client).parse(
        "Can we accept 800 pieces by 2026/4/25?"
    )

    decision = result.decisions[0]
    assert decision.slots == {
        "pieces": 800,
        "requested_delivery_date": "2026-04-25",
    }
    assert decision.missing_slots == ("category",)


def test_non_positive_quantity_becomes_issue_and_remains_missing():
    client = MockLLMClient(
        [
            payload(
                task(
                    "order_feasibility",
                    category="TOPS",
                    pieces=0,
                    requested_delivery_date="2026-04-25",
                )
            )
        ]
    )

    result = EnhancedIntentParser(client).parse(
        "Can we accept 0 TOPS pieces by 2026-04-25?"
    )

    assert result.decisions[0].missing_slots == ("pieces",)
    assert result.issues[0].issue_type == InterpretationIssueType.INVALID_FORMAT
    assert result.issues[0].field == "pieces"


def test_possible_order_id_typo_becomes_issue_not_silent_correction():
    client = MockLLMClient(
        [
            payload(
                task(
                    "order_status",
                    order_id="ODR057",
                    missing_slots=["order_id"],
                ),
                language="zh",
                is_ambiguous=True,
            )
        ]
    )

    result = EnhancedIntentParser(client).parse("请查询ODR057")

    decision = result.decisions[0]
    assert decision.slots == {}
    assert decision.missing_slots == ("order_id",)
    assert result.is_ambiguous is True
    assert len(result.issues) == 1
    assert result.issues[0].issue_type == InterpretationIssueType.POSSIBLE_TYPO
    assert result.issues[0].original_value == "ODR057"
    assert result.issues[0].suggested_value == "ORD-057"
    assert result.issues[0].decision_index == 0


def test_local_guard_removes_model_silent_typo_correction():
    client = MockLLMClient(
        [payload(task("order_status", order_id="ORD-057"), language="zh")]
    )

    result = EnhancedIntentParser(client).parse("请查询ODR057")

    assert result.decisions[0].slots == {}
    assert result.decisions[0].missing_slots == ("order_id",)
    assert result.is_ambiguous is True
    assert result.issues == [
        InterpretationIssue(
            issue_type=InterpretationIssueType.POSSIBLE_TYPO,
            field="order_id",
            original_value="ODR057",
            suggested_value="ORD-057",
            decision_index=0,
        )
    ]


def test_local_guard_removes_model_silent_typo_update():
    context = ConversationState(
        tasks=[
            TaskState(
                task_id="task-001",
                decision=IntentDecision(
                    intent=Intent.ORDER_STATUS,
                    missing_slots=("order_id",),
                ),
                status=TaskStatus.PENDING,
            )
        ]
    )
    client = MockLLMClient(
        [payload(slot_updates=[update("task-001", order_id="ORD-057")])]
    )

    result = EnhancedIntentParser(client).parse(
        "刚才那单是ODR057",
        context=context,
    )

    assert result.slot_updates == []
    assert result.is_ambiguous is True
    assert result.issues == [
        InterpretationIssue(
            issue_type=InterpretationIssueType.POSSIBLE_TYPO,
            field="order_id",
            original_value="ODR057",
            suggested_value="ORD-057",
            task_id="task-001",
        )
    ]


def test_more_than_two_tasks_is_rejected_without_partial_results():
    client = MockLLMClient(
        [
            payload(
                task("order_status", order_id="ORD-001"),
                task("order_status", order_id="ORD-002"),
                task("order_status", order_id="ORD-003"),
            )
        ]
    )

    result = EnhancedIntentParser(client).parse("Check three orders")

    assert result.decisions == []
    assert result.is_ambiguous is True
    assert result.issues[0].issue_type == InterpretationIssueType.TOO_MANY_TASKS


def test_slot_update_targets_existing_task_and_normalizes_value():
    context = ConversationState(
        tasks=[
            TaskState(
                task_id="task-002",
                decision=IntentDecision(
                    intent=Intent.ORDER_FEASIBILITY,
                    slots={"category": "TOPS", "pieces": 800},
                    missing_slots=("requested_delivery_date",),
                ),
                status=TaskStatus.PENDING,
            )
        ],
        language=ResponseLanguage.CHINESE,
    )
    client = MockLLMClient(
        [
            payload(
                language="zh",
                slot_updates=[
                    update(
                        "task-002",
                        requested_delivery_date="2026/4/25",
                    )
                ],
            )
        ]
    )

    result = EnhancedIntentParser(client).parse(
        "TOPS那单交期是2026/4/25",
        context=context,
    )

    assert result.decisions == []
    assert result.slot_updates[0].task_id == "task-002"
    assert result.slot_updates[0].slots == {
        "requested_delivery_date": "2026-04-25"
    }
    assert "task-002" in client.calls[0]["user_prompt"]


def test_unknown_slot_update_target_becomes_ambiguity_issue():
    client = MockLLMClient(
        [payload(slot_updates=[update("task-999", order_id="ORD-057")])]
    )

    result = EnhancedIntentParser(client).parse(
        "Use ORD-057 for that task",
        context=ConversationState(),
    )

    assert result.slot_updates == []
    assert result.is_ambiguous is True
    assert result.issues[0].issue_type == (
        InterpretationIssueType.AMBIGUOUS_REFERENCE
    )


def test_affirmative_reply_confirms_one_persisted_typo_without_model_call():
    context = ConversationState(
        tasks=[
            TaskState(
                task_id="task-001",
                decision=IntentDecision(
                    intent=Intent.ORDER_STATUS,
                    missing_slots=("order_id",),
                    language=ResponseLanguage.CHINESE,
                ),
                status=TaskStatus.PENDING,
                issues=[
                    InterpretationIssue(
                        issue_type=InterpretationIssueType.POSSIBLE_TYPO,
                        field="order_id",
                        original_value="ODR057",
                        suggested_value="ORD-057",
                        task_id="task-001",
                    )
                ],
            )
        ],
        language=ResponseLanguage.CHINESE,
    )
    client = MockLLMClient()

    result = EnhancedIntentParser(client).parse("是的", context=context)

    assert result.slot_updates == [
        SlotUpdate(task_id="task-001", slots={"order_id": "ORD-057"})
    ]
    assert client.calls == []


def test_affirmative_reply_with_two_candidates_uses_model_instead_of_guessing():
    issue = InterpretationIssue(
        issue_type=InterpretationIssueType.POSSIBLE_TYPO,
        field="order_id",
        original_value="ODR057",
        suggested_value="ORD-057",
    )
    context = ConversationState(
        tasks=[
            TaskState(
                task_id="task-001",
                decision=IntentDecision(intent=Intent.ORDER_STATUS),
                issues=[issue],
            ),
            TaskState(
                task_id="task-002",
                decision=IntentDecision(intent=Intent.ORDER_STATUS),
                issues=[issue],
            ),
        ]
    )
    client = MockLLMClient(
        [
            payload(
                is_ambiguous=True,
                issues=[
                    {
                        "issue_type": "ambiguous_reference",
                        "field": "order_id",
                        "original_value": "yes",
                        "suggested_value": None,
                        "decision_index": None,
                        "task_id": None,
                    }
                ],
            )
        ]
    )

    result = EnhancedIntentParser(client).parse("yes", context=context)

    assert result.slot_updates == []
    assert result.is_ambiguous is True
    assert len(client.calls) == 1


def test_affirmative_reply_rejects_invalid_suggested_value():
    context = ConversationState(
        tasks=[
            TaskState(
                task_id="task-001",
                decision=IntentDecision(intent=Intent.ORDER_STATUS),
                status=TaskStatus.PENDING,
                issues=[
                    InterpretationIssue(
                        issue_type=InterpretationIssueType.POSSIBLE_TYPO,
                        field="order_id",
                        original_value="ODR057",
                        suggested_value="still-invalid",
                    )
                ],
            )
        ]
    )
    client = MockLLMClient([payload(is_ambiguous=True)])

    result = EnhancedIntentParser(client).parse("yes", context=context)

    assert result.slot_updates == []
    assert len(client.calls) == 1


def test_model_failure_falls_back_to_unknown_in_user_language():
    client = MockLLMClient([LLMClientError("network unavailable")])

    result = EnhancedIntentParser(client).parse("请检查订单")

    assert result.decisions[0].intent == Intent.UNKNOWN
    assert result.language == ResponseLanguage.CHINESE


def test_empty_input_does_not_call_model():
    client = MockLLMClient()

    result = EnhancedIntentParser(client).parse("  ")

    assert result.decisions[0].intent == Intent.UNKNOWN
    assert client.calls == []
