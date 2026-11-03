"""A new answer requests one scroll; unrelated reruns do not."""

from app.ui.answer_scroll import consume_scroll_target, scroll_script
from tests.test_ui_evidence_selection import execute

pytest_plugins = ["tests.test_ui_workflow_language"]


def test_scroll_request_consumed_once_and_stale_target_ignored():
    thread = {"messages": [{"ui_reply_id": "one"}], "scroll_to_answer": "one"}
    assert consume_scroll_target(thread) == "one"
    assert consume_scroll_target(thread) is None
    thread["scroll_to_answer"] = "missing"
    assert consume_scroll_target(thread) is None


def test_new_result_scrolls_once_but_selection_and_expansion_do_not(screen):
    app, guided, _ = screen
    execute(app)
    thread = app.session_state["threads"][0]
    identity = thread["selected_reply_id"]
    scripts = [item.proto.body for item in app.get("html")]
    assert any("sc-answer-" + identity in s for s in scripts)
    assert "scroll_to_answer" not in thread
    app.button(key="full_" + identity).click().run()
    assert not app.get("html")
    app.button(key="evidence_" + identity).click().run()
    assert not app.get("html")
    assert guided.call_count == 1


def test_script_scrolls_only_conversation_not_page_or_analysis():
    script = scroll_script("one")
    assert "region.scrollTo" in script
    assert "scrollIntoView" not in script
    assert "window.scroll" not in script
    assert "analysis_scroll" not in script
