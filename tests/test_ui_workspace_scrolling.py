"""Ten-message workspace regressions without network or real model calls."""

from copy import deepcopy

import pytest

from app.agent.contracts import AgentReply, Evidence
from tests.test_ui_evidence_selection import execute
from tests.ui_events import dismiss_workflow_dialog

pytest_plugins = ["tests.test_ui_workflow_language"]


@pytest.mark.parametrize("mode", ("green", "day", "night"))
def test_workflow_dock_follows_greeting_and_rail_control_stays_outside_surface(screen, mode):
    app, guided, _ = screen
    execute(app)
    original = deepcopy(app.session_state["threads"][0]["messages"])
    app.button(key="theme_" + mode).click().run()
    app.button(key="hide_analysis").click().run()
    scroll = app.get_by_key("conversation_scroll")
    assert scroll.get_by_key("workflow_dock").get_by_key("expand_workflows")
    assert app.get_by_key("show_analysis")
    assert "show_analysis" not in {e.key for e in app.get_by_key("conversation_surface").button}
    assert len([b for b in app.button if b.key == "expand_workflows"]) == 1
    app.button(key="expand_workflows").click().run()
    assert app.get_by_key("conversation_scroll").get_by_key("center_Order_Status")
    app.button(key="show_analysis").click().run()
    assert not app.exception
    assert app.session_state["threads"][0]["messages"] == original
    guided.assert_called_once()


def test_ten_messages_retain_order_data_and_explicit_selection_after_reruns(screen):
    app, guided, service = screen
    originals = []
    for index in range(5):
        reply = AgentReply(
            "Detailed response " + str(index) + ". " + "Original details. " * 30,
            evidence=[Evidence(f"source-{index}.csv", str(index), "Original source")],
            warnings=[f"Warning {index}"],
            assumptions=[f"Assumption {index}"],
            metadata={"language": "en", "marker": index},
        )
        originals.append(deepcopy(reply))
        guided.side_effect = None
        guided.return_value = reply
        execute(app)
    thread = app.session_state["threads"][0]
    assert len(thread["messages"]) == 10
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"] * 5
    assert [m["reply"] for m in thread["messages"] if m["role"] == "assistant"] == originals
    first = thread["messages"][1]["ui_reply_id"]
    last = thread["messages"][9]["ui_reply_id"]
    snapshot = deepcopy(thread["messages"])
    app.button(key="evidence_" + first).click().run()
    assert not app.exception
    assert app.session_state["threads"][0]["selected_reply_id"] == first
    assert [w.value for w in app.warning] == ["Warning 0"]
    app.run()  # A rendering/scrolling interaction must not select the newest reply.
    assert app.session_state["threads"][0]["selected_reply_id"] == first
    app.button(key="evidence_" + last).click().run()
    assert [w.value for w in app.warning] == ["Warning 4"]
    assert app.session_state["threads"][0]["messages"] == snapshot
    assert guided.call_count == 5
    service.assert_not_called()


def test_composer_and_selected_payload_survive_panel_visibility_and_language(screen):
    app, guided, _ = screen
    execute(app)
    thread = app.session_state["threads"][0]
    selected = thread["selected_reply_id"]
    original = deepcopy(thread["messages"])
    app.button(key="hide_analysis").click().run()
    assert not app.exception
    assert len(app.chat_input) == 1
    assert app.chat_input(key="chat_prompt").value is None
    app.button(key="show_analysis").click().run()
    app.selectbox(key="ui_language").set_value("zh").run()
    assert not app.exception
    assert len(app.chat_input) == 1
    assert app.chat_input(key="chat_prompt").value is None
    assert app.session_state["threads"][0]["selected_reply_id"] == selected
    assert app.session_state["threads"][0]["messages"] == original
    assert guided.call_count == 1


def test_workflow_dialog_closes_without_execution_and_reopens_saved_parameters(screen):
    app, guided, service = screen
    app.button(key="center_Order_Status").click().run()
    assert app.get("dialog")
    assert not any(button.key == "close_structured" for button in app.button)
    app.text_input(key="wf_1_Order_Status_order_id").set_value("ORD-102").run()
    dismiss_workflow_dialog(app)
    assert app.session_state["workflow"] is None
    assert not app.get("dialog")
    assert app.session_state["threads"][0]["messages"] == []
    app.button(key="center_Order_Status").click().run()
    assert app.text_input(key="wf_1_Order_Status_order_id").value == "ORD-102"
    guided.assert_not_called()
    service.assert_not_called()
    app.button(key="run_workflow").click().run()
    assert not app.get("dialog")
    assert len(app.session_state["threads"][0]["messages"]) == 2
    assert guided.call_count == 1
