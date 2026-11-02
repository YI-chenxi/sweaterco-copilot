"""Selected-answer ownership, stable UI ids, panel behavior and export regression."""

from copy import deepcopy

from app.agent.contracts import AgentReply, Evidence
from app.ui.analysis_selection import ensure_reply_ids, selected_answer
from app.ui.view_models import evidence_csv

pytest_plugins = ["tests.test_ui_workflow_language"]


def sample(order, source, warning):
    return AgentReply(
        "Original " + order,
        evidence=[Evidence(source, order, "Record for " + order)],
        warnings=[warning],
        assumptions=["Assumption for " + order],
        metadata={
            "language": "en",
            "intent": "order_status",
            "structured_result": {
                "order_id": order,
                "judgement": "NORMAL",
                "derived_metrics": {},
            },
        },
    )


def execute(app):
    if not app.session_state["workflow_panel_expanded"]:
        app.button(key="expand_workflows").click().run()
    app.button(key="center_Order_Status").click().run()
    app.button(key="run_workflow").click().run()
    assert not app.exception


def test_identifiers_survive_reruns_reordering_and_do_not_mutate_reply():
    reply = sample("ORD-057", "first.csv", "first warning")
    before = deepcopy(reply)
    thread = {"messages": [{"role": "assistant", "reply": reply}]}
    ensure_reply_ids(thread)
    identity = thread["messages"][0]["ui_reply_id"]
    thread["selected_reply_id"] = identity
    thread["messages"].insert(0, {"role": "user", "content": "Earlier question"})
    ensure_reply_ids(thread)
    chosen, number = selected_answer(thread)
    assert chosen["ui_reply_id"] == identity and number == 1
    assert chosen["reply"] is reply and reply == before
    thread["selected_reply_id"] = "missing or foreign identifier"
    assert selected_answer(thread) == (None, None)
    assert selected_answer({"messages": []}) == (None, None)


def test_duplicate_legacy_ids_are_repaired_without_replacing_original_result():
    first = {"role": "assistant", "reply": AgentReply("First"), "ui_reply_id": "same"}
    second = {"role": "assistant", "reply": AgentReply("Second"), "ui_reply_id": "same"}
    thread = {"messages": [first, second]}
    ensure_reply_ids(thread)
    assert first["ui_reply_id"] == "same"
    assert second["ui_reply_id"] != "same"
    assert selected_answer(thread) == (second, 2)


def test_previous_answer_controls_all_panel_data_and_export(screen):
    app, guided, service = screen
    first = sample("ORD-057", "first.csv", "Risk A")
    second = sample("ORD-102", "second.csv", "Risk B")
    guided.side_effect = [first, second]
    execute(app)
    execute(app)
    thread = app.session_state["threads"][0]
    originals = deepcopy(thread["messages"])
    first_id = thread["messages"][1]["ui_reply_id"]
    second_id = thread["messages"][3]["ui_reply_id"]
    assert first_id != second_id
    assert any(w.value == "Risk B" for w in app.warning)
    app.button(key="evidence_" + first_id).click().run()
    assert not app.exception
    assert app.session_state["threads"][0]["selected_reply_id"] == first_id
    assert [w.value for w in app.warning] == ["Risk A"]
    assert any("Assumption for ORD-057" in m.value for m in app.markdown)
    assert not any("Assumption for ORD-102" in m.value for m in app.markdown)
    table = app.dataframe[0].value
    assert table["Source"].tolist() == ["first.csv"]
    chosen, number = selected_answer(app.session_state["threads"][0])
    csv = evidence_csv(chosen["reply"])
    assert number == 1 and "ORD-057" in csv and "ORD-102" not in csv
    assert any("Answer 1" in m.value and "ORD-057" in m.value for m in app.markdown)
    app.selectbox(key="ui_language").set_value("zh").run()
    assert app.session_state["threads"][0]["selected_reply_id"] == first_id
    assert app.session_state["threads"][0]["messages"] == originals
    assert guided.call_count == 2
    service.assert_not_called()


def test_close_and_reopen_preserves_selection_and_history(screen):
    app, guided, _ = screen
    execute(app)
    thread = app.session_state["threads"][0]
    identity = thread["messages"][1]["ui_reply_id"]
    before = deepcopy(thread["messages"])
    app.button(key="hide_analysis").click().run()
    assert not app.exception
    assert not app.session_state["analysis_open"]
    assert app.button(key="show_analysis")
    app.button(key="evidence_" + identity).click().run()
    assert app.session_state["analysis_open"]
    assert app.session_state["threads"][0]["selected_reply_id"] == identity
    assert app.session_state["threads"][0]["messages"] == before
    assert guided.call_count == 1


def test_empty_selected_answer_does_not_borrow_latest_evidence(screen):
    app, guided, _ = screen
    guided.side_effect = [AgentReply("No evidence"), sample("ORD-102", "second.csv", "Risk B")]
    execute(app)
    execute(app)
    first_id = app.session_state["threads"][0]["messages"][1]["ui_reply_id"]
    app.button(key="evidence_" + first_id).click().run()
    assert not app.exception
    assert not app.dataframe and not app.warning
    assert not app.get("download_button")
    assert any("No evidence was provided" in c.value for c in app.caption)


def test_selection_is_thread_local_and_survives_returning_to_conversation(screen):
    app, guided, _ = screen
    execute(app)
    original = app.session_state["threads"][0]
    identity = original["messages"][1]["ui_reply_id"]
    app.button(key="new_chat").click().run()
    assert not app.warning and not app.dataframe
    assert app.session_state["active_thread"] == 2
    app.button(key="thread_1").click().run()
    assert app.session_state["threads"][1]["selected_reply_id"] == identity
    assert any("Original warning" == w.value for w in app.warning)
    assert guided.call_count == 1
