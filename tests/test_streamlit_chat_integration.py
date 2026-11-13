"""Exercise the real Streamlit screen with offline queued model responses."""

from copy import deepcopy
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

from app.agent.contracts import AgentReply
from app.agent.enhancement.contracts import TaskStatus
from app.agent.service import AgentService
from app.agent.settings import LLMSettings
from tests.test_chat_integration import payload, service, task

APP = Path(__file__).resolve().parents[1] / "app/ui/streamlit_app.py"


def screen(monkeypatch, *responses):
    agent, client, executor = service(*responses)
    st.cache_resource.clear()
    monkeypatch.setattr(AgentService, "from_env", classmethod(lambda cls: agent))
    settings = LLMSettings(provider="openai", api_key="offline-ui-test")
    monkeypatch.setattr(LLMSettings, "from_env", classmethod(lambda cls: settings))
    app = AppTest.from_file(str(APP)).run(timeout=20)
    assert not app.exception
    return app, client, executor


def send(app, message):
    app.chat_input(key="chat_prompt").set_value(message).run(timeout=20)
    assert not app.exception


def test_ui_missing_date_followup_saves_final_state_and_source_history(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task(order_id="ORD-057"), task("order_feasibility", category="TOPS", pieces=800)),
        payload(updates=[{"task_id": "task-002", "requested_delivery_date": "2026-04-25"}]),
    )
    send(app, "Check ORD-057 and assess 800 TOPS")
    assert app.button(key="reset_chat_request")
    assert not any(expander.label == "Requests waiting for details" for expander in app.expander)
    first_reply = app.session_state["threads"][0]["messages"][1]["reply"]
    assert first_reply.needs_clarification
    headline = first_reply.text.splitlines()[0].strip("* ")
    assert sum(item.value.count(headline) for item in app.markdown) == 1
    assert not any("Supporting Details" in item.value for item in app.markdown)
    for internal_term in (
        "task-002",
        "PENDING",
        "planner",
        "order_feasibility",
        "requested_delivery_date",
    ):
        assert internal_term not in first_reply.text
    send(app, "TOPS is due on 2026-04-25")
    thread = app.session_state["threads"][0]
    assert len(thread["messages"]) == 4
    assert all(task.status == TaskStatus.COMPLETED for task in thread["agent_session"].state.tasks)
    assert len(executor.calls) == 2
    assert thread["messages"][1]["reply"].evidence
    assert thread["messages"][3]["reply"].assumptions
    st.cache_resource.clear()


def test_ui_bare_order_id_resumes_the_pending_request(monkeypatch):
    app, client, executor = screen(monkeypatch, payload(task()))

    send(app, "How is my order doing?")
    send(app, "ORD-057")

    thread = app.session_state["threads"][0]
    assert len(client.calls) == 1
    assert len(executor.calls) == 1
    assert executor.calls[0].slots["order_id"] == "ORD-057"
    assert thread["agent_session"].state.tasks[0].status == TaskStatus.COMPLETED
    assert thread["messages"][-1]["reply"].needs_clarification is False
    st.cache_resource.clear()


def test_ui_new_conversation_does_not_inherit_pending_slots(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        payload(task(order_id="ORD-057")),
    )
    send(app, "Assess 800 TOPS")
    first = app.session_state["threads"][0]
    app.button(key="new_chat").click().run(timeout=20)
    assert not app.exception
    send(app, "Check ORD-057")
    threads = app.session_state["threads"]
    assert len(threads) == 2
    assert threads[0]["agent_session"].state.tasks[0].decision.intent.value == "order_status"
    assert first["agent_session"].state.tasks[0].status == TaskStatus.PENDING
    assert len(executor.calls) == 1
    st.cache_resource.clear()


def test_ui_global_blocker_reset_and_two_task_details(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(issues=[{"issue_type": "too_many_tasks"}], ambiguous=True),
        payload(task("production_normality", stage="ASSEMBLY"), task(order_id="ORD-057")),
    )
    send(app, "Run three tasks")
    assert not executor.calls
    app.button(key="reset_chat_request").click().run(timeout=20)
    assert not app.exception
    assert not app.session_state["threads"][0]["agent_session"].global_issues
    send(app, "Check assembly and ORD-057")
    assert len(executor.calls) == 2
    assert len(app.metric) >= 4
    assert len(app.session_state["threads"][0]["messages"]) == 4
    st.cache_resource.clear()


def test_ui_morning_briefing_does_not_repeat_findings_as_a_table(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task("morning_briefing", top_n=4)),
    )

    send(app, "Show me the top four priorities")

    assert len(executor.calls) == 1
    assert not any(expander.label.startswith("Priority findings") for expander in app.expander)
    assert not any("sc-answer-banner" in item.value for item in app.markdown)
    assert not any("sc-next-step" in item.value for item in app.markdown)
    assert not any("Review evidence before acting" in item.value for item in app.markdown)
    st.cache_resource.clear()


def test_ui_oldest_of_twelve_conversations_can_be_reopened_and_resumed(monkeypatch):
    app, _, executor = screen(
        monkeypatch,
        payload(task("order_feasibility", category="TOPS", pieces=800)),
        payload(updates=[{"task_id": "task-001", "requested_delivery_date": "2026-04-25"}]),
    )
    send(app, "Please assess an order for 800 TOPS and explain whether we can accept it")
    oldest = app.session_state["threads"][0]
    newer = [
        {
            "id": index,
            "title": "A previously truncated title…",
            "messages": [
                {"role": "user", "content": f"Check order ORD-{index:03d}"},
                {
                    "role": "assistant",
                    "reply": AgentReply(
                        text="Stored response.",
                        metadata={
                            "intent": "order_status",
                            "structured_result": {"order_id": f"ORD-{index:03d}"},
                        },
                    ),
                },
            ],
        }
        for index in range(12, 1, -1)
    ]
    app.session_state["threads"] = [*newer, oldest]
    app.session_state["active_thread"] = 12
    app.session_state["next_thread"] = 13
    app.run(timeout=20)
    assert not app.exception
    assert all(app.button(key=f"thread_{index}") for index in range(1, 13))
    assert app.button(key="thread_1").label == "◦ 800 TOPS feasibility"
    untouched = deepcopy(app.session_state["threads"][:-1])
    app.button(key="thread_1").click().run(timeout=20)
    assert not app.exception
    assert app.session_state["active_thread"] == 1
    assert (
        app.session_state["threads"][-1]["agent_session"].state.tasks[0].status
        == TaskStatus.PENDING
    )
    send(app, "The delivery date is 2026-04-25")
    assert app.session_state["threads"][:-1] == untouched
    assert (
        app.session_state["threads"][-1]["agent_session"].state.tasks[0].status
        == TaskStatus.COMPLETED
    )
    assert len(executor.calls) == 1
    st.cache_resource.clear()


def test_ui_uses_one_native_composer_and_only_configure_in_workspace(monkeypatch):
    app, _, _ = screen(monkeypatch)
    assert len(app.chat_input) == 1
    assert app.chat_input[0].key == "chat_prompt"
    assert not app.text_area
    assert not any(button.label == "Send →" for button in app.button)
    assert not any("Configure" in expander.label for expander in app.expander)
    markdown = " ".join(item.value for item in app.markdown)
    captions = " ".join(item.value for item in app.caption)
    assert 'class="sc-workspace"' not in markdown
    assert 'class="sc-service-state"' not in markdown
    assert "AI Configure" in markdown
    assert 'class="sc-ai-config-state online"' in markdown
    assert "Natural-language AI uses the model" not in captions
    app.button(key="new_chat").click().run(timeout=20)
    assert not app.exception
    assert len(app.chat_input) == 1
    assert app.session_state["threads"][0]["messages"] == []
    st.cache_resource.clear()


def test_ui_unconfigured_chat_keeps_workflows_without_queuing_a_question(monkeypatch):
    app, _, executor = screen(monkeypatch)
    monkeypatch.setattr(
        LLMSettings, "from_env", classmethod(lambda cls: LLMSettings(provider="openai", api_key=""))
    )
    app.run(timeout=20)
    markdown = " ".join(item.value for item in app.markdown)
    assert 'class="sc-ai-config-state offline"' in markdown
    assert "Not configured" in markdown
    send(app, "Check ORD-057")
    assert not executor.calls
    assert not app.session_state["threads"][0]["messages"]
    assert any("needs a model key" in warning.value for warning in app.warning)
    st.cache_resource.clear()


def test_ui_failed_request_keeps_one_user_message_and_allows_the_next_turn(monkeypatch):
    import app.ui.chat_state as chat_state

    app, _, executor = screen(monkeypatch, payload(task(order_id="ORD-057")))
    original = chat_state.run_chat_turn

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(chat_state, "run_chat_turn", original)
        raise RuntimeError("offline simulated failure")

    monkeypatch.setattr(chat_state, "run_chat_turn", fail_once)
    send(app, "Check ORD-057")
    messages = app.session_state["threads"][0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["reply"].metadata["status"] == "failed"
    assert "pending_chat" not in app.session_state
    send(app, "Check ORD-057 again")
    assert len(executor.calls) == 1
    assert len(app.session_state["threads"][0]["messages"]) == 4
    st.cache_resource.clear()
