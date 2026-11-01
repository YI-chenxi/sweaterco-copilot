"""Ten-message workspace regressions without network or real model calls."""

from copy import deepcopy

from app.agent.contracts import AgentReply, Evidence
from tests.test_ui_evidence_selection import execute

pytest_plugins = ["tests.test_ui_workflow_language"]


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
    app.text_area(key="draft_text").set_value("Unsubmitted draft").run()
    app.button(key="hide_analysis").click().run()
    assert not app.exception
    assert app.text_area(key="draft_text").value == "Unsubmitted draft"
    app.button(key="show_analysis").click().run()
    app.selectbox(key="ui_language").set_value("zh").run()
    assert not app.exception
    assert app.text_area(key="draft_text").value == "Unsubmitted draft"
    assert app.session_state["threads"][0]["selected_reply_id"] == selected
    assert app.session_state["threads"][0]["messages"] == original
    assert guided.call_count == 1


def test_workflow_dialog_closes_without_execution_and_reopens_saved_parameters(screen):
    app, guided, service = screen
    app.button(key="center_Order_Status").click().run()
    assert app.get("dialog")
    app.text_input(key="wf_1_Order_Status_order_id").set_value("ORD-102").run()
    app.button(key="close_structured").click().run()
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
