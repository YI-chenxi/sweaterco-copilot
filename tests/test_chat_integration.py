"""Chat integration with queued model outputs and real business tools."""

from copy import deepcopy

import pytest

from app.agent.enhancement.contracts import TaskStatus
from app.agent.intent_parser import IntentParser
from app.agent.llm_client import LLMClientError, MockLLMClient
from app.agent.orchestrator import AgentOrchestrator
from app.agent.service import AgentService
from app.agent.turn_service import ChatSession, reset_request
from app.ui.chat_state import clear_chat_request, ensure_chat_session, run_chat_turn, task_replies


class RecordingOrchestrator(AgentOrchestrator):
    def __init__(self, *, fail_intent=None):
        super().__init__()
        self.calls = []
        self.fail_intent = fail_intent

    def handle(self, decision):
        self.calls.append(deepcopy(decision))
        if decision.intent.value == self.fail_intent:
            raise RuntimeError("private error details")
        return super().handle(decision)


def task(intent="order_status", **slots):
    return {"intent": intent, **slots}


def payload(
    *tasks,
    updates=(),
    issues=(),
    language="en",
    ambiguous=False,
    mode="business",
    needs_clarification=False,
    clarification_question=None,
    conversation_response=None,
):
    return {
        "mode": mode,
        "tasks": list(tasks),
        "slot_updates": list(updates),
        "issues": list(issues),
        "language": language,
        "is_ambiguous": ambiguous,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "conversation_response": conversation_response or {"conclusion": None, "explanation": None},
    }


def service(*responses, fail_intent=None):
    client = MockLLMClient(responses)
    runner = RecordingOrchestrator(fail_intent=fail_intent)
    return AgentService(IntentParser(client), runner), client, runner


def test_single_workflow_exposes_real_result_and_source_rows():
    agent, _, executor = service(payload(task(order_id="ORD-057")))
    result = agent.handle_turn("How is ORD057?")
    assert result.session.state.tasks[0].status == TaskStatus.COMPLETED
    assert result.reply.metadata["intent"] == "order_status"
    assert result.reply.metadata["structured_result"]["order_id"] == "ORD-057"
    assert result.reply.evidence[0].reference == "ORD-057"
    assert len(executor.calls) == 1


def test_natural_product_alias_reaches_business_tool_as_canonical_category():
    agent, _, executor = service(
        payload(
            task(
                "order_feasibility",
                category="TOPS",
                pieces=800,
                requested_delivery_date="2026-04-25",
            )
        )
    )

    result = agent.handle_turn("Can we accept 800 hoodies by 2026-04-25?")

    assert result.session.state.tasks[0].decision.slots["category"] == "TOPS"
    assert executor.calls[0].slots["category"] == "TOPS"


def test_direct_conversation_answer_does_not_create_or_replay_business_tasks():
    agent, _, executor = service(
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        payload(
            mode="conversation",
            language="zh",
            conversation_response={
                "conclusion": "订单号通常写成 ORD-057。",
                "explanation": "这是系统使用的标准订单号格式。",
            },
        ),
    )
    first = agent.handle_turn("Assess 800 TOPS")

    second = agent.handle_turn("订单格式长什么样？", first.session)

    assert "ORD-057" in second.reply.text
    assert second.reply.metadata["intent"] == "conversation"
    assert second.session == first.session
    assert len(second.session.state.tasks) == 1
    assert executor.calls == []


def test_model_clarification_question_is_used_for_incomplete_request():
    question = "What delivery date should I use for the 800-piece TOPS order?"
    agent, _, executor = service(
        payload(
            task("order_feasibility", category="TOPS", pieces=800),
            needs_clarification=True,
            clarification_question=question,
        )
    )

    result = agent.handle_turn("Can we accept 800 hoodies?")

    assert result.reply.needs_clarification is True
    assert result.reply.suggested_question == question
    assert question in result.reply.text
    assert executor.calls == []


def test_two_workflows_keep_request_order_and_complete_evidence():
    agent, _, executor = service(
        payload(task("production_normality", stage="ASSEMBLY"), task(order_id="ORD-057"))
    )
    result = agent.handle_turn("Check assembly, then ORD-057")
    assert [call.intent.value for call in executor.calls] == [
        "production_normality",
        "order_status",
    ]
    assert result.reply.metadata["task_order"] == ["task-001", "task-002"]
    parts = task_replies(result.reply)
    assert len(parts) == 2
    assert len(result.reply.evidence) == sum(len(reply.evidence) for _, reply in parts)
    assert all(t.status == TaskStatus.COMPLETED for t in result.session.state.tasks)
    parts[0][1].text = "changed"
    assert "changed" not in result.reply.text


def test_missing_date_followup_executes_only_pending_task():
    agent, client, executor = service(
        payload(task(order_id="ORD-057"), task("order_feasibility", category="TOPS", pieces=800)),
        payload(updates=[{"task_id": "task-002", "requested_delivery_date": "2026-04-25"}]),
    )
    first = agent.handle_turn("Check ORD-057 and assess 800 TOPS")
    before = deepcopy(first.session)
    assert first.reply.needs_clarification
    assert len(executor.calls) == 1
    assert first.session.state.tasks[1].status == TaskStatus.PENDING
    second = agent.handle_turn("The TOPS order is due on 2026-04-25", first.session)
    assert first.session == before
    assert len(executor.calls) == 2
    assert executor.calls[-1].intent.value == "order_feasibility"
    assert all(t.status == TaskStatus.COMPLETED for t in second.session.state.tasks)
    assert second.reply.assumptions
    assert len(task_replies(second.reply)) == 1
    assert "Conversation tasks:" in client.calls[-1]["user_prompt"]
    assert "task-002" in client.calls[-1]["user_prompt"]


@pytest.mark.parametrize("bad_quantity", [0, -5, 2.5, True])
def test_invalid_quantity_cannot_execute_and_can_be_corrected(bad_quantity):
    agent, _, executor = service(
        payload(
            task(
                "order_feasibility",
                category="TOPS",
                pieces=bad_quantity,
                requested_delivery_date="2026-04-25",
            )
        ),
        payload(updates=[{"task_id": "task-001", "pieces": 800}]),
    )
    first = agent.handle_turn("Assess this quantity")
    assert first.reply.needs_clarification
    assert not executor.calls
    second = agent.handle_turn("Use 800 pieces", first.session)
    assert second.session.state.tasks[0].status == TaskStatus.COMPLETED
    assert executor.calls[0].slots["pieces"] == 800


def test_invalid_date_is_checked_against_factory_snapshot():
    agent, _, executor = service(
        payload(
            task(
                "order_feasibility",
                category="TOPS",
                pieces=800,
                requested_delivery_date="2026-03-30",
            )
        ),
        payload(updates=[{"task_id": "task-001", "requested_delivery_date": "2026-04-25"}]),
    )
    first = agent.handle_turn("Can we deliver by 2026-03-30?")
    assert not executor.calls
    assert first.reply.needs_clarification
    second = agent.handle_turn("Use 2026-04-25", first.session)
    assert second.session.state.tasks[0].status == TaskStatus.COMPLETED


def test_typo_confirmation_uses_memory_without_a_second_model_call():
    agent, client, executor = service(payload(task(order_id="ODR057"), language="zh"))
    first = agent.handle_turn("请查 ODR057")
    assert first.reply.needs_clarification
    assert not executor.calls
    second = agent.handle_turn("是的", first.session)
    assert len(client.calls) == 1
    assert executor.calls[0].slots["order_id"] == "ORD-057"
    assert second.reply.metadata["language"] == "zh"


def test_two_threads_share_service_but_never_share_parameters():
    agent, _, executor = service(
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        payload(task(order_id="ORD-057")),
        payload(updates=[{"task_id": "task-001", "requested_delivery_date": "2026-04-25"}]),
    )
    a, b = {"messages": []}, {"messages": []}
    run_chat_turn(a, "800 TOPS?", agent)
    before_a = deepcopy(a)
    run_chat_turn(b, "ORD-057?", agent)
    assert a == before_a
    before_b = deepcopy(b)
    run_chat_turn(a, "Delivery is 2026-04-25", agent)
    assert b == before_b
    assert [call.intent.value for call in executor.calls] == ["order_status", "order_feasibility"]
    assert ensure_chat_session(a) is not ensure_chat_session(b)


def test_global_issue_persists_until_explicit_reset():
    too_many = {"issue_type": "too_many_tasks"}
    agent, _, executor = service(
        payload(issues=[too_many], ambiguous=True),
        payload(task(order_id="ORD-057")),
        payload(task(order_id="ORD-057")),
    )
    first = agent.handle_turn("Run three tasks")
    assert first.session.global_issues
    second = agent.handle_turn("ORD-057?", first.session)
    assert second.session.global_issues
    assert second.reply.metadata["global_blockers"]
    assert not executor.calls
    third = agent.handle_turn("ORD-057?", reset_request(second.session))
    assert len(executor.calls) == 1
    assert not third.session.global_issues
    assert third.session.state.tasks[0].task_id == "task-002"


def test_unbound_ambiguity_is_not_cleared_by_an_unrelated_turn():
    agent, _, executor = service(
        payload(issues=[{"issue_type": "ambiguous_reference"}], ambiguous=True),
        payload(task("morning_briefing", top_n=3)),
    )
    first = agent.handle_turn("What about that one?")
    second = agent.handle_turn("Show a briefing", first.session)
    assert second.reply.needs_clarification
    assert not executor.calls


def test_tool_failure_does_not_stop_independent_second_task():
    agent, _, executor = service(
        payload(task("production_normality", stage="ASSEMBLY"), task(order_id="ORD-057")),
        fail_intent="production_normality",
    )
    result = agent.handle_turn("Check assembly and ORD-057")
    assert [t.status for t in result.session.state.tasks] == [
        TaskStatus.FAILED,
        TaskStatus.COMPLETED,
    ]
    assert len(executor.calls) == 2
    assert result.reply.evidence
    assert "private error details" not in result.reply.text


def test_no_data_is_held_and_explicit_order_correction_can_resume():
    agent, _, executor = service(
        payload(task(order_id="ORD-999")),
        payload(updates=[{"task_id": "task-001", "order_id": "ORD-057"}]),
    )
    first = agent.handle_turn("ORD-999?")
    assert first.session.state.tasks[0].status == TaskStatus.PENDING
    assert not first.reply.evidence
    second = agent.handle_turn("I mean ORD-057", first.session)
    assert second.session.state.tasks[0].status == TaskStatus.COMPLETED
    assert len(executor.calls) == 2


def test_unknown_message_leaves_pending_task_unchanged():
    agent, _, _ = service(
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        payload(task("unknown")),
    )
    first = agent.handle_turn("Assess 800 TOPS")
    second = agent.handle_turn("Translate this poem", first.session)
    assert second.session == first.session
    assert not second.reply.evidence


def test_model_failure_does_not_lose_prior_context_or_invent_results():
    agent, _, _ = service(
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        LLMClientError("offline test failure"),
    )
    first = agent.handle_turn("Assess 800 TOPS")
    second = agent.handle_turn("April 25", first.session)
    assert second.session == first.session
    assert not second.reply.evidence


def test_completed_task_cannot_be_reopened_by_implicit_slot_update():
    agent, _, executor = service(
        payload(task(order_id="ORD-057")),
        payload(updates=[{"task_id": "task-001", "order_id": "ORD-058"}]),
    )
    first = agent.handle_turn("ORD-057?")
    second = agent.handle_turn("Actually ORD-058", first.session)
    assert second.session.state.tasks[0].status == TaskStatus.COMPLETED
    assert second.session.global_issues
    assert len(executor.calls) == 1


def test_new_explicit_request_gets_unique_id_and_executes():
    agent, _, executor = service(
        payload(task(order_id="ORD-057")), payload(task(order_id="ORD-058"))
    )
    first = agent.handle_turn("ORD-057?")
    second = agent.handle_turn("Check ORD-058", first.session)
    assert second.session.active_task_ids == ["task-002"]
    assert len(executor.calls) == 2


def test_old_thread_migration_and_reset_keep_visible_history():
    thread = {"messages": [{"role": "user", "content": "old question"}]}
    original = deepcopy(thread["messages"])
    ensure_chat_session(thread)
    clear_chat_request(thread)
    assert thread["messages"] == original
    assert isinstance(thread["agent_session"], ChatSession)


def test_ui_commits_session_only_after_a_successful_turn():
    class BrokenService:
        def handle_turn(self, text, session):
            raise RuntimeError("integration failure")

    thread = {"agent_session": ChatSession()}
    before = deepcopy(thread)
    with pytest.raises(RuntimeError):
        run_chat_turn(thread, "query", BrokenService())
    assert thread == before


def test_empty_model_task_list_preserves_existing_context_without_execution():
    agent, _, executor = service(
        payload(task("order_feasibility", category="TOPS", pieces=800)), payload()
    )
    first = agent.handle_turn("Assess 800 TOPS")
    second = agent.handle_turn("An unrecognized follow-up", first.session)
    assert second.session == first.session
    assert not second.reply.evidence
    assert all(call.intent.value == "unknown" for call in executor.calls)


def test_from_env_wires_legacy_and_enhanced_parsers_to_same_client(monkeypatch):
    client = MockLLMClient([payload(task(order_id="ORD-057"))])
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setattr("app.agent.service.create_llm_client", lambda settings: client)
    agent = AgentService.from_env(load_dotenv_file=False)
    result = agent.handle_turn("ORD-057?")
    assert agent.parser.client is client
    assert agent.turn_service.parser.client is client
    assert result.session.state.tasks[0].status == TaskStatus.COMPLETED


def test_all_failed_tasks_do_not_show_a_successful_ui_assessment():
    from app.ui.context_panels import reply_headline

    agent, _, _ = service(
        payload(task("production_normality", stage="ASSEMBLY")), fail_intent="production_normality"
    )
    result = agent.handle_turn("Check assembly")
    assert result.reply.metadata["status"] == "failed"
    assert reply_headline(result.reply) == ("No verified result", "caution")
