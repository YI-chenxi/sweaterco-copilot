"""Person B language integration, using mocked configuration and workflow execution."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app.agent.contracts import AgentReply, Evidence
from app.agent.orchestrator import AgentOrchestrator
from app.agent.service import AgentService
from app.agent.settings import LLMSettings
from app.ui.language import shared_language
from app.ui.view_models import metrics_for_reply
from tests.ui_events import dismiss_workflow_dialog

ENTRY = Path(__file__).parents[1] / "app/ui/streamlit_app.py"


@pytest.fixture
def screen(monkeypatch):
    st.cache_resource.clear()
    monkeypatch.setattr("dotenv.load_dotenv", Mock(side_effect=AssertionError("No config reads")))
    monkeypatch.setattr(
        LLMSettings, "from_env", Mock(return_value=SimpleNamespace(provider="openai", api_key=""))
    )
    service = Mock(side_effect=AssertionError("No model calls"))
    monkeypatch.setattr(AgentService, "from_env", service)

    def handle(decision):
        return AgentReply(
            "原始回答" if decision.language.value == "zh" else "Original answer",
            evidence=[Evidence("orders.csv", "ORD-057", "Original record")],
            warnings=["Original warning"],
            assumptions=["Original assumption"],
            metadata={
                "language": decision.language.value,
                "intent": decision.intent.value,
                "structured_result": {"order_id": "ORD-057", "judgement": "NORMAL"},
            },
        )

    guided = Mock(side_effect=handle)
    monkeypatch.setattr(AgentOrchestrator, "handle", guided)
    yield AppTest.from_file(str(ENTRY), default_timeout=20).run(), guided, service
    st.cache_resource.clear()


def switch(app, language):
    # Person A's control will write this one agreed key; B supplies no duplicate switch.
    app.session_state["ui_language"] = language
    app.run()
    assert not app.exception


@pytest.mark.parametrize(
    "flow", ["Order_Status", "Production_Health", "Morning_Briefing", "Check_Feasibility"]
)
def test_all_workflows_use_global_language_and_preserve_original_results(screen, flow):
    app, guided, service = screen
    switch(app, "zh")
    app.button(key="center_" + flow).click().run()
    assert not any(item.label in {"Response language", "回答语言"} for item in app.selectbox)
    assert app.button(key="run_workflow").label == "运行分析 →"
    expected_labels = {
        "Order_Status": "订单编号",
        "Production_Health": "生产工序",
        "Morning_Briefing": "重点问题数量",
        "Check_Feasibility": "要求交货日期",
    }
    widgets = (
        list(app.text_input) + list(app.selectbox) + list(app.number_input) + list(app.date_input)
    )
    assert expected_labels[flow] in [item.label for item in widgets]
    app.button(key="run_workflow").click().run()
    assert not app.exception
    assert guided.call_args.args[0].language.value == "zh"
    thread = app.session_state["threads"][0]
    before = deepcopy(thread["messages"])
    agent_session = deepcopy(thread["agent_session"])
    for language in ("en", "zh", "en"):
        switch(app, language)
        assert app.session_state["threads"][0]["messages"] == before
        assert app.session_state["threads"][0]["agent_session"] == agent_session
        assert app.session_state["active_thread"] == 1
    assert guided.call_count == 1
    app.button(key="expand_workflows").click().run()
    app.button(key="center_" + flow).click().run()
    assert app.button(key="run_workflow").label == "Run analysis →"
    app.button(key="run_workflow").click().run()
    assert guided.call_count == 2
    assert guided.call_args.args[0].language.value == "en"
    assert thread["messages"][1]["reply"].text == "原始回答"
    assert thread["messages"][-1]["reply"].text == "Original answer"
    service.assert_not_called()


@pytest.mark.parametrize(
    "flow,key,value",
    [
        ("Order_Status", "order_id", "ORD-102"),
        ("Production_Health", "stage", "PACKING"),
        ("Morning_Briefing", "top_n", 7),
        ("Check_Feasibility", "pieces", 1200),
    ],
)
def test_input_values_survive_language_changes_and_form_reopening(screen, flow, key, value):
    app, guided, _ = screen
    app.button(key="center_" + flow).click().run()
    widget_key = "wf_1_" + flow + "_" + key
    widget = (
        app.text_input
        if key == "order_id"
        else app.selectbox
        if key == "stage"
        else app.number_input
    )
    widget(key=widget_key).set_value(value).run()
    for lang in ("zh", "en", "zh"):
        switch(app, lang)
        assert (
            app.text_input
            if key == "order_id"
            else app.selectbox
            if key == "stage"
            else app.number_input
        )(key=widget_key).value == value
    dismiss_workflow_dialog(app)
    app.button(key="center_" + flow).click().run()
    assert (
        app.text_input
        if key == "order_id"
        else app.selectbox
        if key == "stage"
        else app.number_input
    )(key=widget_key).value == value
    guided.assert_not_called()


def test_invalid_input_preserved_and_validation_localized(screen):
    app, guided, _ = screen
    app.button(key="center_Order_Status").click().run()
    app.text_input(key="wf_1_Order_Status_order_id").set_value(" ").run()
    switch(app, "zh")
    app.button(key="run_workflow").click().run()
    assert app.error[0].value == "请输入订单编号。"
    assert app.text_input(key="wf_1_Order_Status_order_id").value == " "
    guided.assert_not_called()


def test_backend_failure_preserves_form_and_original_records(screen):
    app, guided, _ = screen
    guided.side_effect = ValueError("Invalid input")
    app.button(key="center_Order_Status").click().run()
    app.text_input(key="wf_1_Order_Status_order_id").set_value("ORD-999").run()
    app.button(key="run_workflow").click().run()
    assert app.error
    switch(app, "zh")
    assert app.text_input(key="wf_1_Order_Status_order_id").value == "ORD-999"
    assert guided.call_count == 1


def test_adapter_reads_one_shared_key_without_mutating_state():
    state = {"ui_language": "en", "reply_language": "中文"}
    before = dict(state)
    assert shared_language(state) == "en"
    assert state == before
    assert shared_language({"ui_language": "zh"}) == "zh"
    assert shared_language({}) == "en"
    assert shared_language({"reply_language": "中文"}) == "zh"


def test_metric_translation_does_not_mutate_backend_reply():
    reply = AgentReply(
        "Original",
        metadata={
            "intent": "order_status",
            "structured_result": {
                "order_id": "ORD-057",
                "judgement": "NORMAL",
                "derived_metrics": {"days_to_due": 8},
            },
        },
    )
    original = deepcopy(reply)
    chinese = metrics_for_reply(reply, language="zh")
    assert chinese[0].label == "订单" and chinese[0].value == "ORD-057"
    assert chinese[1].value == "正常"
    assert reply == original


def test_feasibility_all_parameters_and_thread_selection_survive_switch(screen):
    from datetime import date

    app, guided, _ = screen
    app.button(key="center_Check_Feasibility").click().run()
    app.selectbox(key="wf_1_Check_Feasibility_category").set_value("ACCESSORIES").run()
    app.date_input(key="wf_1_Check_Feasibility_date").set_value(date(2026, 5, 10)).run()
    app.number_input(key="wf_1_Check_Feasibility_pieces").set_value(900).run()
    switch(app, "zh")
    app.button(key="new_chat").click().run()
    app.button(key="thread_1").click().run()
    app.button(key="center_Check_Feasibility").click().run()
    assert app.selectbox(key="wf_1_Check_Feasibility_category").value == "ACCESSORIES"
    assert app.date_input(key="wf_1_Check_Feasibility_date").value == date(2026, 5, 10)
    assert app.number_input(key="wf_1_Check_Feasibility_pieces").value == 900
    assert app.session_state["active_thread"] == 1
    guided.assert_not_called()


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize(
    "workflow,values",
    [
        ("Order status", {"order_id": "ORD-057"}),
        ("Production health", {"stage": "ASSEMBLY"}),
        ("Morning briefing", {"top_n": 3}),
        (
            "Order feasibility",
            {"category": "TOPS", "pieces": 800, "requested_delivery_date": "2026-04-25"},
        ),
    ],
)
def test_existing_local_business_tools_support_both_response_languages(language, workflow, values):
    from app.ui.view_models import guided_decision

    reply = AgentOrchestrator().handle(guided_decision(workflow, language=language, **values))
    assert reply.metadata["language"] == language
    assert reply.text
    assert not reply.needs_clarification


def test_rejected_workflow_keeps_inputs_until_user_corrects_them(screen):
    app, guided, _ = screen
    guided.side_effect = None
    guided.return_value = AgentReply(
        "Original validation message",
        needs_clarification=True,
        metadata={"status": "failed", "language": "en"},
    )
    app.button(key="center_Order_Status").click().run()
    app.text_input(key="wf_1_Order_Status_order_id").set_value("ORD-999").run()
    app.button(key="run_workflow").click().run()
    switch(app, "zh")
    assert app.text_input(key="wf_1_Order_Status_order_id").value == "ORD-999"
    assert (
        app.session_state["threads"][0]["messages"][1]["reply"].text
        == "Original validation message"
    )
    assert guided.call_count == 1
