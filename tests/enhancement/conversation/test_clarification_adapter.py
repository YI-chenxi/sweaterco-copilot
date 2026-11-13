"""Person 4 adapters tested against unchanged Memory and Person 5 entry points."""

import unittest
from copy import deepcopy
from datetime import date

from app.agent.contracts import AgentReply
from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    SlotUpdate,
    TaskStatus,
)
from app.agent.enhancement.conversation.clarification import validate_request
from app.agent.enhancement.conversation.clarification_adapter import (
    apply_validated_update,
    attach_clarification,
    prepare_state,
    run_validated_tasks,
)
from app.agent.enhancement.conversation.memory import add_task
from app.agent.enhancement.understanding.parser import EnhancedIntentParser
from app.agent.intents import Intent, IntentDecision
from app.agent.llm_client import MockLLMClient
from app.agent.orchestrator import AgentOrchestrator
from app.agent.settings import AgentSettings
from tests.enhancement.conversation.test_clarification import FEASIBLE, POLICY, make_state, ready


class RecordingExecutor:
    def __init__(self):
        self.calls = []
        self.real = AgentOrchestrator(settings=AgentSettings(reference_date=POLICY.reference_date))
        self.settings = self.real.settings

    def handle(self, decision):
        self.calls.append(deepcopy(decision))
        return self.real.handle(decision)


class AdapterTests(unittest.TestCase):
    def test_invalid_value_becomes_persisted_issue_and_pending(self):
        state = make_state(ready(intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "pieces": -1}))
        before = deepcopy(state)
        result = validate_request(state, ["one"], policy=POLICY)
        updated = prepare_state(state, result)
        self.assertEqual(updated.tasks[0].status, TaskStatus.PENDING)
        self.assertEqual(updated.tasks[0].issues[0].field, "pieces")
        self.assertEqual(state, before)

    def test_normalization_reaches_the_executor(self):
        executor = RecordingExecutor()
        state = make_state(ready(slots={"order_id": "ord057"}))
        result = run_validated_tasks(state, ["one"], executor, policy=POLICY)
        self.assertEqual(executor.calls[0].slots["order_id"], "ORD-057")
        self.assertEqual(result.state.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(state.tasks[0].decision.slots["order_id"], "ord057")

    def test_stale_validation_is_rejected(self):
        state = make_state(ready())
        result = validate_request(state, ["one"], policy=POLICY)
        state.tasks[0].decision.slots["order_id"] = "ORD-058"
        with self.assertRaises(ValueError):
            prepare_state(state, result)

    def test_invalid_update_does_not_replace_old_value_or_clear_issue(self):
        issue = InterpretationIssue(
            InterpretationIssueType.POSSIBLE_TYPO,
            field="order_id",
            original_value="ODR057",
            suggested_value="ORD-057",
            task_id="one",
        )
        task = ready()
        task.status = TaskStatus.PENDING
        task.issues = [issue]
        state = make_state(task)
        updated, result = apply_validated_update(
            state, SlotUpdate("one", {"order_id": "bad"}), policy=POLICY
        )
        self.assertEqual(updated.tasks[0].decision.slots["order_id"], "ORD-057")
        self.assertIn(issue, updated.tasks[0].issues)
        self.assertEqual(updated.tasks[0].status, TaskStatus.PENDING)
        self.assertTrue(result.needs_clarification)

    def test_valid_update_clears_field_issue_and_recalculates(self):
        state = make_state(ready(intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "pieces": -1}))
        result = validate_request(state, ["one"], policy=POLICY)
        state = prepare_state(state, result)
        updated, result = apply_validated_update(
            state, SlotUpdate("one", {"pieces": 800}), policy=POLICY
        )
        self.assertEqual(updated.tasks[0].issues, [])
        self.assertEqual(updated.tasks[0].status, TaskStatus.READY)
        self.assertTrue(result.tasks[0].passed)

    def test_mixed_update_applies_valid_fields_and_preserves_invalid_old_field(self):
        state = make_state(ready(intent=Intent.ORDER_FEASIBILITY, slots=FEASIBLE))
        updated, _ = apply_validated_update(
            state,
            SlotUpdate("one", {"pieces": -2, "requested_delivery_date": "2026-04-26"}),
            policy=POLICY,
        )
        self.assertEqual(updated.tasks[0].decision.slots["pieces"], 800)
        self.assertEqual(updated.tasks[0].decision.slots["requested_delivery_date"], "2026-04-26")
        self.assertEqual(updated.tasks[0].status, TaskStatus.PENDING)

    def test_explicit_removal_becomes_missing(self):
        state = make_state(ready())
        updated, result = apply_validated_update(
            state, SlotUpdate("one", {"order_id": None}), policy=POLICY
        )
        self.assertNotIn("order_id", updated.tasks[0].decision.slots)
        self.assertEqual(updated.tasks[0].status, TaskStatus.PENDING)
        self.assertEqual(result.problems[0].code, "missing_slot")

    def test_terminal_update_requires_retry(self):
        task = ready()
        task.status = TaskStatus.COMPLETED
        with self.assertRaises(ValueError):
            apply_validated_update(
                make_state(task), SlotUpdate("one", {"order_id": "ORD-058"}), policy=POLICY
            )

    def test_cross_task_values_are_not_overwritten(self):
        state = make_state(
            ready("one"), ready("two", intent=Intent.ORDER_FEASIBILITY, slots=FEASIBLE)
        )
        updated, _ = apply_validated_update(
            state, SlotUpdate("two", {"pieces": 900}), policy=POLICY
        )
        self.assertEqual(updated.tasks[0], state.tasks[0])
        self.assertEqual(state.tasks[1].decision.slots["pieces"], 800)

    def test_global_issue_prevents_all_executor_calls(self):
        executor = RecordingExecutor()
        state = make_state(ready())
        issue = InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE)
        result = run_validated_tasks(state, ["one"], executor, policy=POLICY, issues=[issue])
        self.assertEqual(executor.calls, [])
        self.assertIsNone(result.planning)
        self.assertTrue(result.reply.needs_clarification)
        self.assertTrue(result.clarification.blocks_request)

    def test_global_issue_without_any_task_is_still_shown(self):
        issue = InterpretationIssue(InterpretationIssueType.TOO_MANY_TASKS)
        executor = RecordingExecutor()
        result = run_validated_tasks(
            ConversationState(), [], executor, policy=POLICY, issues=[issue]
        )
        self.assertTrue(result.reply.needs_clarification)
        self.assertIn("up to two", result.reply.suggested_question)
        self.assertEqual(executor.calls, [])

    def test_global_issue_remains_blocking_when_carried_to_next_turn(self):
        issue = InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE)
        executor = RecordingExecutor()
        first = run_validated_tasks(
            make_state(ready()), ["one"], executor, policy=POLICY, issues=[issue]
        )
        second = run_validated_tasks(first.state, ["one"], executor, policy=POLICY, issues=[issue])
        self.assertEqual(executor.calls, [])
        self.assertTrue(second.clarification.blocks_request)

    def test_partial_execution_and_next_turn_no_duplicate(self):
        state = add_task(
            ConversationState(),
            IntentDecision(Intent.ORDER_STATUS, {"order_id": "ORD-057"}),
            "order",
        )
        state = add_task(
            state,
            IntentDecision(Intent.ORDER_FEASIBILITY, {"category": "TOPS", "pieces": 800}),
            "feasibility",
        )
        executor = RecordingExecutor()
        first = run_validated_tasks(state, ["order", "feasibility"], executor, policy=POLICY)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(first.state.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(first.state.tasks[1].status, TaskStatus.PENDING)
        self.assertIn("delivery date", first.reply.suggested_question)
        self.assertTrue(first.reply.evidence)
        updated, _ = apply_validated_update(
            first.state,
            SlotUpdate("feasibility", {"requested_delivery_date": "2026-04-25"}),
            policy=POLICY,
        )
        second = run_validated_tasks(updated, ["order", "feasibility"], executor, policy=POLICY)
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(
            [t.status for t in second.state.tasks], [TaskStatus.COMPLETED, TaskStatus.COMPLETED]
        )
        self.assertFalse(second.reply.needs_clarification)
        self.assertTrue(second.reply.evidence)

    def test_bad_quantity_never_reaches_tools_but_independent_order_does(self):
        executor = RecordingExecutor()
        state = make_state(
            ready("bad", intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "pieces": 1.9}),
            ready("order"),
        )
        result = run_validated_tasks(state, ["bad", "order"], executor, policy=POLICY)
        self.assertEqual([c.intent for c in executor.calls], [Intent.ORDER_STATUS])
        self.assertEqual(result.state.tasks[0].status, TaskStatus.PENDING)
        self.assertIn("positive whole-number", result.reply.suggested_question)

    def test_unknown_never_reaches_tools(self):
        executor = RecordingExecutor()
        result = run_validated_tasks(
            make_state(ready(intent=Intent.UNKNOWN, slots={})), ["one"], executor, policy=POLICY
        )
        self.assertEqual(executor.calls, [])
        self.assertTrue(result.reply.needs_clarification)

    def test_typo_confirm_flow_with_actual_parser_and_memory(self):
        issue = InterpretationIssue(
            InterpretationIssueType.POSSIBLE_TYPO,
            field="order_id",
            original_value="ODR057",
            suggested_value="ORD-057",
            decision_index=0,
        )
        state = add_task(
            ConversationState(), IntentDecision(Intent.ORDER_STATUS), "one", issues=[issue]
        )
        executor = RecordingExecutor()
        first = run_validated_tasks(state, ["one"], executor, policy=POLICY)
        self.assertEqual(executor.calls, [])
        self.assertIn("did you mean ORD-057", first.reply.suggested_question)
        parsed = EnhancedIntentParser(MockLLMClient()).parse("yes", first.state)
        updated, _ = apply_validated_update(first.state, parsed.slot_updates[0], policy=POLICY)
        final = run_validated_tasks(updated, ["one"], executor, policy=POLICY)
        self.assertEqual(final.state.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(len(executor.calls), 1)

    def test_no_data_is_held_after_aggregation_without_fake_input_error(self):
        executor = RecordingExecutor()
        first = run_validated_tasks(
            make_state(ready(slots={"order_id": "ORD-999"})), ["one"], executor, policy=POLICY
        )
        self.assertEqual(first.state.tasks[0].status, TaskStatus.PENDING)
        self.assertEqual(first.execution.state.tasks[0].status, TaskStatus.READY)
        self.assertFalse(first.reply.needs_clarification)
        self.assertEqual(first.state.tasks[0].issues, [])
        second = run_validated_tasks(first.state, ["one"], executor, policy=POLICY)
        self.assertEqual(len(executor.calls), 1)
        self.assertFalse(second.reply.needs_clarification)

    def test_unclassified_reply_is_held(self):
        class Executor:
            def handle(self, decision):
                return AgentReply("Unclassified")

        result = run_validated_tasks(make_state(ready()), ["one"], Executor(), policy=POLICY)
        self.assertEqual(result.state.tasks[0].status, TaskStatus.PENDING)
        self.assertFalse(result.reply.needs_clarification)

    def test_negative_business_result_is_completed(self):
        executor = RecordingExecutor()
        state = make_state(
            ready(
                intent=Intent.ORDER_FEASIBILITY,
                slots={**FEASIBLE, "pieces": 10**8, "requested_delivery_date": "2026-04-02"},
            )
        )
        result = run_validated_tasks(state, ["one"], executor, policy=POLICY)
        self.assertEqual(result.state.tasks[0].status, TaskStatus.COMPLETED)
        self.assertEqual(
            result.reply.metadata["tasks"]["one"]["reply"]["metadata"]["structured_result"][
                "estimate"
            ],
            "NOT_SUPPORTED",
        )

    def test_reference_date_must_match_executor(self):
        executor = RecordingExecutor()
        executor.settings = AgentSettings(reference_date=date(2026, 4, 2))
        with self.assertRaises(ValueError):
            run_validated_tasks(make_state(ready()), ["one"], executor, policy=POLICY)
        self.assertEqual(executor.calls, [])

    def test_overlay_keeps_evidence_warnings_and_source_metadata(self):
        from app.agent.contracts import Evidence

        source = AgentReply(
            "source",
            evidence=[Evidence("test", "one", "detail")],
            warnings=["warning"],
            assumptions=["assumption"],
            metadata={"custom": {"value": [1]}},
        )
        before = deepcopy(source)
        clarification = validate_request(make_state(ready(slots={})), ["one"], policy=POLICY)
        reply = attach_clarification(source, clarification)
        self.assertEqual(source, before)
        self.assertEqual(reply.evidence, source.evidence)
        self.assertEqual(reply.warnings, source.warnings)
        self.assertEqual(reply.assumptions, source.assumptions)
        self.assertEqual(reply.metadata["custom"], source.metadata["custom"])

    def test_execution_clarification_is_held_without_mutating_batch(self):
        class Executor:
            def handle(self, decision):
                return AgentReply(
                    "Need more information",
                    needs_clarification=True,
                    suggested_question="Please confirm the order.",
                )

        result = run_validated_tasks(make_state(ready()), ["one"], Executor(), policy=POLICY)
        self.assertEqual(result.state.tasks[0].status, TaskStatus.PENDING)
        self.assertEqual(result.execution.state.tasks[0].status, TaskStatus.READY)
        self.assertTrue(result.reply.needs_clarification)
        self.assertIn("Please confirm the order", result.reply.suggested_question)

    def test_executor_exception_remains_failed_not_clarification(self):
        class Executor:
            def handle(self, decision):
                raise RuntimeError("private details")

        result = run_validated_tasks(make_state(ready()), ["one"], Executor(), policy=POLICY)
        self.assertEqual(result.state.tasks[0].status, TaskStatus.FAILED)
        self.assertFalse(result.reply.needs_clarification)
        self.assertNotIn("private details", result.reply.text)

    def test_empty_update_does_not_resume_hold(self):
        task = ready()
        task.status = TaskStatus.PENDING
        updated, result = apply_validated_update(
            make_state(task), SlotUpdate("one", {}), policy=POLICY
        )
        self.assertEqual(updated.tasks[0].status, TaskStatus.PENDING)
        self.assertFalse(result.tasks[0].passed)

    def test_two_independent_sessions_do_not_share_issues(self):
        first = make_state(ready())
        second = make_state(ready())
        updated, _ = apply_validated_update(
            first, SlotUpdate("one", {"order_id": "bad"}), policy=POLICY
        )
        self.assertTrue(updated.tasks[0].issues)
        self.assertEqual(second.tasks[0].issues, [])
        self.assertEqual(second.tasks[0].status, TaskStatus.READY)


if __name__ == "__main__":
    unittest.main()
