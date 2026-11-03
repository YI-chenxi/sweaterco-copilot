"""Person 4 semantic and issue tests; runnable with unittest or pytest."""

import unittest
from copy import deepcopy
from datetime import date

from app.agent.enhancement.contracts import (
    ConversationState,
    InterpretationIssue,
    InterpretationIssueType,
    TaskState,
    TaskStatus,
)
from app.agent.enhancement.conversation.clarification import ValidationPolicy, validate_request
from app.agent.intents import Intent, IntentDecision, ResponseLanguage

POLICY = ValidationPolicy(date(2026, 4, 1))
FEASIBLE = {"category": "TOPS", "pieces": 800, "requested_delivery_date": "2026-04-25"}


def make_task(task_id="one", intent=Intent.ORDER_STATUS, slots=None, **kwargs):
    if slots is None:
        slots = {"order_id": "ORD-057"}
    return TaskState(task_id, IntentDecision(intent, deepcopy(slots)), **kwargs)


def make_state(*tasks, language=ResponseLanguage.ENGLISH):
    return ConversationState(list(tasks), language)


def ready(task_id="one", intent=Intent.ORDER_STATUS, slots=None):
    return make_task(task_id, intent, slots, status=TaskStatus.READY)


def check(task, **kwargs):
    return validate_request(make_state(task), [task.task_id], policy=POLICY, **kwargs)


class ClarificationTests(unittest.TestCase):
    def test_four_supported_intents_pass(self):
        cases = [
            (Intent.ORDER_STATUS, {"order_id": "ORD-057"}),
            (Intent.PRODUCTION_NORMALITY, {"stage": "ASSEMBLY"}),
            (Intent.MORNING_BRIEFING, {}),
            (Intent.ORDER_FEASIBILITY, FEASIBLE),
        ]
        for intent, slots in cases:
            with self.subTest(intent=intent):
                result = check(ready(intent=intent, slots=slots))
                self.assertEqual(result.executable_task_ids, ("one",))
                self.assertFalse(result.needs_clarification)

    def test_missing_fields_reported_independently(self):
        result = check(ready(intent=Intent.ORDER_FEASIBILITY, slots={"pieces": 800}))
        self.assertEqual(
            {p.field for p in result.problems}, {"category", "requested_delivery_date"}
        )
        self.assertTrue(result.needs_clarification)

    def test_blank_and_none_values_are_missing(self):
        for value in (None, "", "  "):
            with self.subTest(value=value):
                result = check(ready(slots={"order_id": value}))
                self.assertEqual(result.problems[0].code, "missing_slot")

    def test_quantities_are_not_truncated_or_coerced_from_booleans(self):
        for value in (0, -1, 1.9, 800.0, True, False, "1.9", "eight", [], {}):
            with self.subTest(value=value):
                result = check(
                    ready(intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "pieces": value})
                )
                self.assertEqual(result.executable_task_ids, ())
                self.assertEqual(result.problems[0].field, "pieces")
                self.assertEqual(result.problems[0].code, "invalid_format")

    def test_integer_string_safely_normalized(self):
        result = check(ready(intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "pieces": "800"}))
        self.assertEqual(result.tasks[0].normalized_slots["pieces"], 800)

    def test_invalid_and_relative_dates_rejected(self):
        for value in ("2026-02-30", "tomorrow", "下周五", "2026-04-01", "2026-03-31", True):
            with self.subTest(value=value):
                result = check(
                    ready(
                        intent=Intent.ORDER_FEASIBILITY,
                        slots={**FEASIBLE, "requested_delivery_date": value},
                    )
                )
                self.assertEqual(result.problems[0].field, "requested_delivery_date")
                self.assertIn("2026-04-01", result.problems[0].question)

    def test_explicit_business_date_used_instead_of_today(self):
        result = check(ready(intent=Intent.ORDER_FEASIBILITY, slots=FEASIBLE))
        self.assertTrue(result.tasks[0].passed)
        later = validate_request(
            make_state(ready(intent=Intent.ORDER_FEASIBILITY, slots=FEASIBLE)),
            ["one"],
            policy=ValidationPolicy(date(2026, 5, 1)),
        )
        self.assertFalse(later.tasks[0].passed)

    def test_categories_restricted_to_injected_policy(self):
        self.assertFalse(
            check(ready(intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "category": "BANANA"}))
            .tasks[0]
            .passed
        )
        policy = ValidationPolicy(date(2026, 4, 1), frozenset({"CUSTOM"}))
        state = make_state(
            ready(intent=Intent.ORDER_FEASIBILITY, slots={**FEASIBLE, "category": "CUSTOM"})
        )
        self.assertTrue(validate_request(state, ["one"], policy=policy).tasks[0].passed)

    def test_stage_alias_reuses_nlu_normalizer(self):
        result = check(ready(intent=Intent.PRODUCTION_NORMALITY, slots={"stage": "packaging"}))
        self.assertEqual(result.tasks[0].normalized_slots["stage"], "PACKING")
        self.assertFalse(
            check(ready(intent=Intent.PRODUCTION_NORMALITY, slots={"stage": "BANANA"}))
            .tasks[0]
            .passed
        )

    def test_typo_is_question_not_automatic_acceptance(self):
        task = ready(slots={})
        task.issues = [
            InterpretationIssue(
                InterpretationIssueType.POSSIBLE_TYPO,
                field="order_id",
                original_value="ODR057",
                suggested_value="ORD-057",
                task_id="one",
            )
        ]
        result = check(task)
        self.assertEqual(len(result.problems), 1)
        self.assertEqual(result.tasks[0].normalized_slots, {})
        self.assertIn("did you mean ORD-057", result.problems[0].question)

    def test_typo_without_candidate_still_asks(self):
        task = ready(slots={})
        task.issues = [
            InterpretationIssue(
                InterpretationIssueType.POSSIBLE_TYPO, field="order_id", task_id="one"
            )
        ]
        self.assertIn("correct order ID", check(task).problems[0].question)

    def test_all_four_issue_types_have_specific_questions(self):
        for kind, fragment in [
            (InterpretationIssueType.POSSIBLE_TYPO, "confirm"),
            (InterpretationIssueType.INVALID_FORMAT, "positive whole-number"),
            (InterpretationIssueType.AMBIGUOUS_REFERENCE, "Which request"),
            (InterpretationIssueType.TOO_MANY_TASKS, "up to two"),
        ]:
            with self.subTest(kind=kind):
                issue = InterpretationIssue(kind, field="pieces")
                result = validate_request(ConversationState(), [], policy=POLICY, issues=[issue])
                self.assertTrue(result.blocks_request)
                self.assertIn(fragment, result.problems[0].question)

    def test_issue_binds_by_decision_index(self):
        state = make_state(ready("one"), ready("two"))
        issue = InterpretationIssue(
            InterpretationIssueType.INVALID_FORMAT, field="order_id", decision_index=1
        )
        result = validate_request(
            state, ["one", "two"], policy=POLICY, issues=[issue], decision_task_ids=["one", "two"]
        )
        self.assertFalse(result.blocks_request)
        self.assertEqual(result.executable_task_ids, ("one",))
        self.assertEqual(result.problems[0].task_id, "two")

    def test_unknown_target_is_global_not_guessed(self):
        for issue in [
            InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE, task_id="missing"),
            InterpretationIssue(
                InterpretationIssueType.INVALID_FORMAT, field="pieces", decision_index=0
            ),
        ]:
            with self.subTest(issue=issue):
                result = check(ready(), issues=[issue])
                self.assertTrue(result.blocks_request)
                self.assertEqual(result.executable_task_ids, ())

    def test_more_than_two_tasks_blocks_all(self):
        state = make_state(ready("one"), ready("two"), ready("three"))
        result = validate_request(state, ["one", "two", "three"], policy=POLICY)
        self.assertTrue(result.blocks_request)
        self.assertEqual(result.executable_task_ids, ())

    def test_one_blocked_task_does_not_block_another(self):
        state = make_state(ready("one"), ready("two", slots={}))
        result = validate_request(state, ["two", "one"], policy=POLICY)
        self.assertEqual(result.executable_task_ids, ("one",))
        self.assertFalse(result.blocks_request)
        self.assertEqual(result.problems[0].task_id, "two")

    def test_unknown_intent_cannot_pass_with_no_required_slots(self):
        result = check(ready(intent=Intent.UNKNOWN, slots={}))
        self.assertEqual(result.executable_task_ids, ())
        self.assertEqual(result.problems[0].code, "unknown_intent")

    def test_completed_and_failed_tasks_are_not_reopened(self):
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            with self.subTest(status=status):
                result = check(make_task(slots={}, status=status))
                self.assertEqual(result.problems, ())
                self.assertEqual(result.executable_task_ids, ())

    def test_pending_without_missing_values_stays_held(self):
        result = check(make_task())
        self.assertFalse(result.needs_clarification)
        self.assertEqual(result.problems[0].code, "pending_review")

    def test_invalid_briefing_limit_is_not_ignored(self):
        result = check(ready(intent=Intent.MORNING_BRIEFING, slots={"top_n": True}))
        self.assertEqual(result.problems[0].field, "top_n")

    def test_bare_ambiguous_flag_is_not_dropped(self):
        self.assertTrue(check(ready(), is_ambiguous=True).blocks_request)

    def test_bound_issue_with_ambiguous_flag_does_not_block_other_task(self):
        state = make_state(ready("one"), ready("two"))
        issue = InterpretationIssue(
            InterpretationIssueType.INVALID_FORMAT, field="order_id", task_id="one"
        )
        result = validate_request(
            state, ["one", "two"], policy=POLICY, issues=[issue], is_ambiguous=True
        )
        self.assertFalse(result.blocks_request)
        self.assertEqual(result.executable_task_ids, ("two",))

    def test_repeat_incomplete_response_keeps_asking(self):
        task = make_task(intent=Intent.ORDER_FEASIBILITY, slots={"pieces": 800})
        first = check(task)
        second = check(task)
        self.assertEqual(first.problems, second.problems)
        self.assertTrue(second.needs_clarification)

    def test_question_language_is_explicit(self):
        result = check(ready(slots={}), language=ResponseLanguage.CHINESE)
        self.assertIn("你想查询哪个订单", result.problems[0].question)

    def test_task_and_result_snapshots_are_independent(self):
        state = make_state(ready(slots={"order_id": "ord057"}))
        before = deepcopy(state)
        result = validate_request(state, ["one"], policy=POLICY)
        self.assertEqual(state, before)
        result.tasks[0].normalized_slots["order_id"] = "ORD-999"
        self.assertEqual(state, before)

    def test_invalid_reference_inputs_raise(self):
        state = make_state(ready())
        for ids in ("one", ["one", "one"], ["missing"], [""]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_request(state, ids, policy=POLICY)
        with self.assertRaises(ValueError):
            validate_request(make_state(ready(), ready()), ["one"], policy=POLICY)

    def test_stored_task_limit_issue_blocks_whole_request(self):
        task = ready()
        task.issues = [InterpretationIssue(InterpretationIssueType.TOO_MANY_TASKS, task_id="one")]
        result = check(task)
        self.assertTrue(result.blocks_request)
        self.assertEqual(result.executable_task_ids, ())

    def test_issue_targeting_terminal_task_is_not_silently_dropped(self):
        task = ready()
        task.status = TaskStatus.COMPLETED
        issue = InterpretationIssue(InterpretationIssueType.AMBIGUOUS_REFERENCE, task_id="one")
        self.assertTrue(check(task, issues=[issue]).blocks_request)

    def test_unsupported_slot_is_an_integration_error(self):
        with self.assertRaises(ValueError):
            check(ready(slots={"order_id": "ORD-057", "pieces": 800}))

    def test_policy_rejects_implicit_or_invalid_date(self):
        with self.assertRaises(ValueError):
            ValidationPolicy("2026-04-01")
        with self.assertRaises(ValueError):
            ValidationPolicy(date(2026, 4, 1), frozenset({"tops"}))


if __name__ == "__main__":
    unittest.main()
