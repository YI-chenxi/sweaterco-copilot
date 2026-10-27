"""Local result aggregation tests; mocks never construct clients or call APIs."""

from copy import deepcopy
from dataclasses import asdict, replace
from unittest.mock import Mock

import pytest

from app.agent.contracts import AgentReply, Evidence
from app.agent.enhancement.contracts import ConversationState, TaskState, TaskStatus
from app.agent.enhancement.planning.planner import (
    ExecutionBatch,
    ExecutionOutcome,
    PlanningNotice,
    PlanningResult,
    build_plan,
    execute_plan,
)
from app.agent.enhancement.planning.response import aggregate_results
from app.agent.intents import Intent, IntentDecision, ResponseLanguage


def task(task_id="first", status=TaskStatus.READY):
    return TaskState(task_id, IntentDecision(Intent.ORDER_STATUS, {"order_id": task_id}), status)


def source_reply(label="first", **kwargs):
    values = {
        "text": f"Original conclusion {label}",
        "evidence": [Evidence("orders.csv", label, f"Evidence {label}")],
        "warnings": [f"Warning {label}"],
        "assumptions": [f"Assumption {label}"],
        "metadata": {
            "intent": "order_status",
            "rule_version": label,
            "structured_result": {"judgement": "AT_RISK", "values": [label]},
            "tasks": {"reserved": [label]},
        },
    }
    values.update(kwargs)
    return AgentReply(**values)


def run_batch(tasks, task_ids, replies):
    state = ConversationState(tasks=tasks)
    planning = build_plan(state, task_ids)
    handler = Mock(spec=["handle"])
    handler.handle.side_effect = replies
    return planning, execute_plan(state, planning, handler)


def aggregate(planning, batch, ids=("first", "second"), language=ResponseLanguage.ENGLISH):
    return aggregate_results(planning, batch, language, task_ids=ids)


def test_two_successful_tasks_preserve_original_requested_order_and_business_content():
    # Request order differs from state order; the explicit request is authoritative.
    ids = ["second", "first"]
    replies = [source_reply("second"), source_reply("first")]
    planning, batch = run_batch([task(), task("second")], ids, replies)
    reply = aggregate(planning, batch, ids)
    assert isinstance(reply, AgentReply)
    assert reply.text.index("Task second") < reply.text.index("Task first")
    assert all(source.text in reply.text for source in replies)
    assert reply.metadata["task_order"] == ids
    assert list(reply.metadata["tasks"]) == ids
    assert reply.metadata["tasks"]["second"]["execution_outcome"] == "success"
    assert reply.metadata["tasks"]["second"]["task_status"] == "completed"
    assert reply.needs_clarification is False
    assert reply.suggested_question is None


def test_pending_before_successful_task_keeps_interleaved_order_and_missing_fields():
    pending = TaskState("waiting", IntentDecision(Intent.ORDER_FEASIBILITY, {"pieces": 800}))
    ids = ["waiting", "first"]
    planning, batch = run_batch([task(), pending], ids, [source_reply()])
    reply = aggregate(planning, batch, ids)
    assert reply.text.index("Task waiting") < reply.text.index("Task first")
    assert reply.metadata["tasks"]["waiting"]["intent"] == "order_feasibility"
    assert reply.metadata["tasks"]["waiting"]["display_status"] == "pending"
    assert reply.metadata["tasks"]["waiting"]["reply"] is None
    assert reply.needs_clarification is True
    assert "category" in reply.suggested_question
    assert "requested_delivery_date" in reply.suggested_question
    assert reply.evidence == source_reply().evidence


def test_success_and_technical_failure_preserve_both_results_without_requesting_clarification():
    failure = source_reply("second", metadata={"intent": "order_status", "status": "failed"})
    planning, batch = run_batch(
        [task(), task("second")], ["first", "second"], [source_reply(), failure]
    )
    reply = aggregate(planning, batch)
    assert "Execution failed" in reply.text
    assert failure.text in reply.text
    assert reply.metadata["tasks"]["second"]["task_status"] == "failed"
    assert reply.metadata["tasks"]["second"]["reply"] == asdict(failure)
    assert reply.needs_clarification is False
    assert reply.suggested_question is None


def test_raised_exception_has_failure_record_and_does_not_fabricate_evidence():
    planning, batch = run_batch([task()], ["first"], [RuntimeError("private error")])
    reply = aggregate(planning, batch, ["first"])
    entry = reply.metadata["tasks"]["first"]
    assert entry["reply"] is None
    assert entry["error_type"] == "RuntimeError"
    assert entry["display_status"] == "failed"
    assert "private error" not in reply.text
    assert reply.evidence == []
    assert reply.needs_clarification is False


def test_clarification_required_reply_preserves_original_question_and_state():
    question = "Which order did you mean?"
    original = source_reply(needs_clarification=True, suggested_question=question)
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"])
    assert reply.needs_clarification is True
    assert reply.suggested_question == f"[first] {question}"
    assert reply.metadata["tasks"]["first"]["reply"] == asdict(original)
    assert reply.metadata["tasks"]["first"]["task_status"] == "ready"
    assert reply.metadata["unresolved_task_ids"] == ["first"]


def test_two_clarification_questions_keep_associations_even_when_identical():
    original = source_reply(needs_clarification=True, suggested_question="Please confirm.")
    planning, batch = run_batch([task(), task("second")], ["first", "second"], [original, original])
    reply = aggregate(planning, batch)
    assert reply.suggested_question.splitlines() == [
        "[first] Please confirm.",
        "[second] Please confirm.",
    ]
    assert reply.metadata["clarification_questions"] == [
        {"task_id": "first", "question": "Please confirm.", "required": True},
        {"task_id": "second", "question": "Please confirm.", "required": True},
    ]


def test_clarification_without_suggested_question_uses_explicit_generic_fallback():
    original = source_reply(needs_clarification=True)
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"])
    assert reply.needs_clarification is True
    assert "first" in reply.suggested_question
    assert original.text in reply.text
    assert reply.metadata["tasks"]["first"]["reply"]["suggested_question"] is None


def test_no_data_is_explicit_and_not_automatically_a_clarification_request():
    original = source_reply(metadata={"intent": "order_status", "status": "empty"})
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"])
    assert "No data returned" in reply.text
    assert reply.needs_clarification is False
    assert reply.suggested_question is None
    assert reply.metadata["tasks"]["first"]["execution_outcome"] == "no_data"
    assert reply.metadata["tasks"]["first"]["task_status"] == "ready"


def test_no_data_does_not_hide_other_tasks_actual_information_need():
    empty = source_reply(metadata={"status": "empty"})
    pending = task("second", TaskStatus.PENDING)
    planning, batch = run_batch([task(), pending], ["first", "second"], [empty])
    reply = aggregate(planning, batch)
    assert reply.needs_clarification is True
    assert "[second]" in reply.suggested_question
    assert "[first]" not in reply.suggested_question


def test_inactive_source_question_is_preserved_without_promoting_failure_to_clarification():
    original = source_reply(
        metadata={"status": "failed"}, suggested_question="Historical suggestion"
    )
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"])
    assert reply.needs_clarification is False
    assert reply.suggested_question is None
    assert reply.metadata["tasks"]["first"]["clarification_questions"] == ["Historical suggestion"]
    assert reply.metadata["clarification_questions"][0]["required"] is False


def test_completed_and_failed_historical_tasks_are_skipped_without_fabricating_old_replies():
    planning, batch = run_batch(
        [task(status=TaskStatus.COMPLETED), task("second", TaskStatus.FAILED)],
        ["second", "first"],
        [],
    )
    reply = aggregate(planning, batch, ["second", "first"])
    for task_id, status in [("first", "completed"), ("second", "failed")]:
        entry = reply.metadata["tasks"][task_id]
        assert entry["task_status"] == status
        assert entry["display_status"] == "skipped"
        assert entry["reply"] is None
        assert entry["execution_outcome"] is None
    assert reply.evidence == []
    assert reply.needs_clarification is False


def test_stale_task_skipped_after_planning_retains_original_and_current_decisions():
    state = ConversationState(tasks=[task()])
    planning = build_plan(state, ["first"])
    state.tasks[0].decision.slots["order_id"] = "changed"
    handler = Mock(spec=["handle"])
    batch = execute_plan(state, planning, handler)
    reply = aggregate(planning, batch, ["first"])
    entry = reply.metadata["tasks"]["first"]
    assert entry["execution_outcome"] == "skipped"
    assert entry["decision"]["slots"]["order_id"] == "first"
    assert entry["current_decision"]["slots"]["order_id"] == "changed"
    assert "changed since planning" in reply.text
    assert reply.needs_clarification is False
    handler.handle.assert_not_called()


def test_stale_task_with_missing_fields_is_a_genuine_information_need():
    state = ConversationState(tasks=[task()])
    planning = build_plan(state, ["first"])
    state.tasks[0].decision.slots.clear()
    batch = execute_plan(state, planning, Mock(spec=["handle"]))
    reply = aggregate(planning, batch, ["first"])
    assert reply.needs_clarification is True
    assert "order_id" in reply.suggested_question


def test_all_source_fields_and_duplicate_items_preserve_ownership_and_order():
    shared_evidence = Evidence("source", "same", "identical")
    originals = [
        source_reply("first", evidence=[shared_evidence], warnings=["same"], assumptions=["same"]),
        source_reply("second", evidence=[shared_evidence], warnings=["same"], assumptions=["same"]),
    ]
    planning, batch = run_batch([task(), task("second")], ["first", "second"], originals)
    reply = aggregate(planning, batch)
    assert reply.evidence == [shared_evidence, shared_evidence]
    assert reply.warnings == ["same", "same"]
    assert reply.assumptions == ["same", "same"]
    for task_id, original in zip(["first", "second"], originals, strict=True):
        assert reply.metadata["tasks"][task_id]["reply"] == asdict(original)


def test_source_metadata_collisions_do_not_overwrite_task_or_aggregate_metadata():
    originals = [source_reply("first"), source_reply("second")]
    planning, batch = run_batch([task(), task("second")], ["first", "second"], originals)
    reply = aggregate(planning, batch)
    assert reply.metadata["language"] == "en"
    for task_id in ("first", "second"):
        metadata = reply.metadata["tasks"][task_id]["reply"]["metadata"]
        assert metadata["rule_version"] == task_id
        assert metadata["tasks"] == {"reserved": [task_id]}


def test_aggregation_and_editing_output_cannot_mutate_any_input():
    original = source_reply()
    planning, batch = run_batch([task()], ["first"], [original])
    ids = ["first"]
    before = deepcopy((planning, batch, original, ids))
    reply = aggregate(planning, batch, ids)
    assert (planning, batch, original, ids) == before
    reply.evidence.clear()
    reply.warnings.clear()
    reply.assumptions.clear()
    entry = reply.metadata["tasks"]["first"]
    entry["reply"]["metadata"]["structured_result"]["values"].append("mutated")
    entry["decision"]["slots"]["order_id"] = "mutated"
    entry["current_decision"]["slots"]["order_id"] = "mutated"
    assert (planning, batch, original, ids) == before


@pytest.mark.parametrize(
    "language, marker",
    [(ResponseLanguage.CHINESE, "已完成"), (ResponseLanguage.ENGLISH, "Completed")],
)
def test_requested_language_localizes_labels_but_preserves_original_content(language, marker):
    original = source_reply(text="CONDITIONAL：有条件可行，原文不得改写。")
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"], language)
    assert marker in reply.text
    assert original.text in reply.text
    assert reply.metadata["language"] == language.value


def test_negative_business_conclusion_remains_completed():
    original = source_reply(text="Cannot accept order: NOT_SUPPORTED")
    original.metadata["structured_result"] = {"estimate": "NOT_SUPPORTED"}
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"])
    assert original.text in reply.text
    assert reply.metadata["tasks"]["first"]["display_status"] == "completed"
    assert "Execution failed" not in reply.text


def test_empty_request_with_unrelated_history_produces_empty_reply():
    planning = PlanningResult()
    batch = ExecutionBatch(ConversationState(tasks=[task("unrelated")]))
    reply = aggregate(planning, batch, [])
    assert reply.text == "No tasks were requested."
    assert reply.metadata["tasks"] == {}
    assert reply.metadata["task_order"] == []
    assert reply.needs_clarification is False


def test_empty_execution_batch_does_not_claim_planned_tasks_were_completed():
    state = ConversationState(tasks=[task()])
    planning = build_plan(state, ["first"])
    reply = aggregate(planning, ExecutionBatch(state), ["first"])
    assert reply.metadata["tasks"]["first"]["display_status"] == "not_executed"
    assert reply.metadata["tasks"]["first"]["execution_outcome"] is None
    assert reply.evidence == []
    assert reply.needs_clarification is False


def test_unclassified_reply_does_not_invent_success_failure_or_clarification():
    original = AgentReply("Unrecognized source reply")
    planning, batch = run_batch([task()], ["first"], [original])
    reply = aggregate(planning, batch, ["first"])
    assert reply.metadata["tasks"]["first"]["execution_outcome"] == "unclassified"
    assert reply.metadata["tasks"]["first"]["task_status"] == "ready"
    assert reply.needs_clarification is False


@pytest.mark.parametrize(
    "ids",
    [["first", "first"], ["missing"], [""], [None], "first", ["first", "second", "third"], []],
)
def test_invalid_requested_ids_raise_instead_of_losing_results(ids):
    planning, batch = run_batch([task()], ["first"], [source_reply()])
    with pytest.raises(ValueError):
        aggregate(planning, batch, ids)


@pytest.mark.parametrize(
    "problem",
    [
        "duplicate_plan",
        "duplicate_result",
        "duplicate_state",
        "missing_state",
        "extra_result",
        "wrong_decision",
        "wrong_status",
        "wrong_update_flag",
        "wrong_outcome",
        "missing_reply",
        "unexpected_notice",
        "overlap_plan_notice",
        "wrong_reply_intent",
        "partial_results",
    ],
)
def test_inconsistent_inputs_raise_without_mutating_them(problem):
    planning, batch = run_batch(
        [task(), task("second")], ["first", "second"], [source_reply(), source_reply()]
    )
    if problem == "duplicate_plan":
        planning = replace(planning, planned_tasks=planning.planned_tasks * 2)
    elif problem == "duplicate_result":
        batch = replace(batch, results=batch.results * 2)
    elif problem == "duplicate_state":
        batch.state.tasks.append(deepcopy(batch.state.tasks[0]))
    elif problem == "missing_state":
        batch.state.tasks.pop()
    elif problem == "extra_result":
        batch = replace(
            batch, results=(replace(batch.results[0], task_id="extra"), batch.results[1])
        )
    elif problem == "wrong_decision":
        batch = replace(
            batch,
            results=(
                replace(batch.results[0], decision=IntentDecision(Intent.UNKNOWN)),
                batch.results[1],
            ),
        )
    elif problem == "wrong_status":
        batch.state.tasks[0].status = TaskStatus.READY
    elif problem == "wrong_update_flag":
        batch = replace(
            batch, results=(replace(batch.results[0], state_updated=False), batch.results[1])
        )
    elif problem == "wrong_outcome":
        batch = replace(
            batch,
            results=(
                replace(batch.results[0], outcome=ExecutionOutcome.NO_DATA, state_updated=False),
                batch.results[1],
            ),
        )
        batch.state.tasks[0].status = TaskStatus.READY
    elif problem == "missing_reply":
        batch = replace(batch, results=(replace(batch.results[0], reply=None), batch.results[1]))
    elif problem == "unexpected_notice":
        batch = replace(batch, notices=(PlanningNotice("extra", "pending", "invalid"),))
    elif problem == "overlap_plan_notice":
        planning = replace(planning, notices=(PlanningNotice("first", "pending", "invalid"),))
    elif problem == "wrong_reply_intent":
        batch.results[0].reply.metadata["intent"] = "order_feasibility"
    elif problem == "partial_results":
        batch = replace(batch, results=batch.results[:1])
    before = deepcopy((planning, batch))
    with pytest.raises(ValueError):
        aggregate(planning, batch)
    assert (planning, batch) == before


def test_language_must_be_explicit_supported_enum():
    with pytest.raises(ValueError, match="ResponseLanguage"):
        aggregate(PlanningResult(), ExecutionBatch(ConversationState()), [], "fr")


def test_two_waiting_tasks_get_separate_questions_in_requested_order():
    first = TaskState("first", IntentDecision(Intent.ORDER_STATUS))
    second = TaskState("second", IntentDecision(Intent.PRODUCTION_NORMALITY))
    planning, batch = run_batch([first, second], ["second", "first"], [])
    reply = aggregate(planning, batch, ["second", "first"], ResponseLanguage.CHINESE)
    assert "等待补充信息或校验" in reply.text
    assert reply.suggested_question.index("[second]") < reply.suggested_question.index("[first]")
    questions = reply.metadata["clarification_questions"]
    assert [question["task_id"] for question in questions] == ["second", "first"]
    assert "stage" in questions[0]["question"]
    assert "order_id" in questions[1]["question"]
    assert reply.evidence == []


def test_result_storage_order_does_not_change_requested_display_or_source_item_order():
    originals = [source_reply("second"), source_reply("first")]
    planning, batch = run_batch([task(), task("second")], ["second", "first"], originals)
    batch = replace(batch, results=tuple(reversed(batch.results)))
    reply = aggregate(planning, batch, ["second", "first"])
    assert reply.text.index("Task second") < reply.text.index("Task first")
    assert reply.evidence == originals[0].evidence + originals[1].evidence
    assert reply.warnings == originals[0].warnings + originals[1].warnings
    assert reply.assumptions == originals[0].assumptions + originals[1].assumptions


def test_task_id_matching_aggregate_metadata_key_is_still_namespaced():
    planning, batch = run_batch([task("tasks")], ["tasks"], [source_reply()])
    reply = aggregate(planning, batch, ["tasks"])
    assert reply.metadata["task_order"] == ["tasks"]
    assert reply.metadata["tasks"]["tasks"]["task_id"] == "tasks"
    assert reply.metadata["tasks"]["tasks"]["reply"]["metadata"]["tasks"] == {"reserved": ["first"]}


def test_unrecognized_memory_status_is_reported_as_invalid_input():
    planning, batch = run_batch([task()], ["first"], [source_reply()])
    batch.state.tasks[0].status = "bad_status"
    with pytest.raises(ValueError, match="Invalid task status"):
        aggregate(planning, batch, ["first"])


def test_success_record_cannot_also_claim_exception_failure():
    planning, batch = run_batch([task()], ["first"], [source_reply()])
    batch = replace(batch, results=(replace(batch.results[0], error_type="RuntimeError"),))
    with pytest.raises(ValueError, match="non-failure outcome"):
        aggregate(planning, batch, ["first"])
