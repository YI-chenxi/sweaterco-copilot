"""The actual shared language control preserves sessions and workflow inputs."""

from copy import deepcopy

pytest_plugins = ["tests.test_ui_workflow_language"]


def test_visible_control_switches_whole_interface_without_execution(screen):
    app, guided, service = screen
    assert app.selectbox(key="ui_language").label == "Language / 语言"
    assert app.selectbox(key="ui_language").value == "en"
    for code, new_chat, order, placeholder in (
        ("zh", "新建会话", "订单状态", "询问订单"),
        ("en", "New conversation", "Order Status", "Ask about orders"),
    ):
        app.selectbox(key="ui_language").set_value(code).run()
        assert not app.exception
        assert new_chat in app.button(key="new_chat").label
        assert order in app.button(key="center_Order_Status").label
        assert placeholder in app.chat_input(key="chat_prompt").proto.placeholder
    guided.assert_not_called()
    service.assert_not_called()


def test_real_switch_preserves_history_inputs_and_agent_session(screen):
    app, guided, _ = screen
    app.button(key="center_Order_Status").click().run()
    app.button(key="run_workflow").click().run()
    thread = app.session_state["threads"][0]
    messages = deepcopy(thread["messages"])
    agent_session = deepcopy(thread["agent_session"])
    app.button(key="expand_workflows").click().run()
    app.button(key="center_Order_Status").click().run()
    app.text_input(key="wf_1_Order_Status_order_id").set_value("ORD-102").run()
    for code in ("zh", "en", "zh"):
        app.selectbox(key="ui_language").set_value(code).run()
        assert not app.exception
        assert app.text_input(key="wf_1_Order_Status_order_id").value == "ORD-102"
        assert len(app.chat_input) == 1
        assert app.chat_input(key="chat_prompt").value is None
        assert app.session_state["threads"][0]["messages"] == messages
        assert app.session_state["threads"][0]["agent_session"] == agent_session
        assert app.session_state["active_thread"] == 1
        assert guided.call_count == 1
    app.button(key="run_workflow").click().run()
    assert guided.call_args.args[0].language.value == "zh"
    assert thread["messages"][1]["reply"].text == "Original answer"
    assert thread["messages"][-1]["reply"].text == "原始回答"


def test_language_stays_selected_when_creating_and_opening_conversations(screen):
    app, guided, _ = screen
    app.selectbox(key="ui_language").set_value("zh").run()
    app.button(key="new_chat").click().run()
    app.button(key="thread_1").click().run()
    assert not app.exception
    assert app.selectbox(key="ui_language").value == "zh"
    assert app.session_state["active_thread"] == 1
    assert len(app.session_state["threads"]) == 2
    guided.assert_not_called()


def test_existing_process_refreshes_catalog_without_clearing_state(screen, monkeypatch):
    from app.ui import language

    app, guided, _ = screen
    monkeypatch.delitem(language.ZH, "New conversation")
    app.selectbox(key="ui_language").set_value("zh").run()
    assert "新建会话" in app.button(key="new_chat").label
    guided.assert_not_called()


def test_saved_english_reply_has_chinese_view_and_preserved_original(screen):
    from app.agent.contracts import AgentReply

    app, guided, _ = screen
    guided.side_effect = None
    guided.return_value = AgentReply(
        "**Order ORD-057 does not currently show a delivery risk.**",
        metadata={
            "language": "en",
            "intent": "order_status",
            "structured_result": {
                "order_id": "ORD-057",
                "judgement": "NORMAL",
                "labels": [],
                "derived_metrics": {"days_to_due": -58, "days_since_activity": 66},
            },
        },
    )
    app.button(key="center_Order_Status").click().run()
    app.button(key="run_workflow").click().run()
    original = deepcopy(app.session_state["threads"][0]["messages"])
    app.selectbox(key="ui_language").set_value("zh").run()
    assert not app.exception
    assert any("目前未显示交付风险" in item.value for item in app.markdown)
    assert not any("原始回答" in e.label or "Original response" in e.label for e in app.expander)
    assert not any("does not currently show" in item.value for item in app.markdown)
    app.selectbox(key="ui_language").set_value("en").run()
    assert any("does not currently show" in item.value for item in app.markdown)
    assert not any("原始回答" in e.label or "Original response" in e.label for e in app.expander)
    assert app.session_state["threads"][0]["messages"] == original
    assert guided.call_count == 1
